"""Triage Specialist Panel — parallel LLM calls with persona prompts.

Loads specialist persona ``.md`` files + ``shared_output_instructions.md``,
makes parallel LiteLLM calls (one per selected specialist), parses JSON
responses, and emits cumulative SSE progress as verdicts arrive.
"""

import asyncio
import json
import logging
from pathlib import Path

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import TRIAGE_SPECIALIST_TIMEOUT_S
from lifesci_common.triage_utils import emit_triage_progress, extract_json_from_text

log = logging.getLogger(__name__)

# Prompt file cache
_prompt_cache: dict[str, str] = {}


def _load_prompt(specialist: str, prompts_dir: str = "") -> str:
    """Load a specialist persona .md file, with caching."""
    if specialist in _prompt_cache:
        return _prompt_cache[specialist]

    if not prompts_dir:
        prompts_dir = str(
            Path(__file__).resolve().parents[2] / "prompts" / "triage" / "specialists"
        )

    path = Path(prompts_dir) / f"{specialist}.md"
    if not path.exists():
        log.warning("Specialist prompt not found: %s", path)
        return ""

    text = path.read_text(encoding="utf-8")
    _prompt_cache[specialist] = text
    return text


def _load_shared_instructions(prompts_dir: str = "") -> str:
    """Load the shared output instructions injected into all specialist prompts."""
    cache_key = "__shared__"
    if cache_key in _prompt_cache:
        return _prompt_cache[cache_key]

    if not prompts_dir:
        prompts_dir = str(Path(__file__).resolve().parents[2] / "prompts" / "triage")

    path = Path(prompts_dir) / "shared_output_instructions.md"
    if not path.exists():
        log.warning("Shared output instructions not found: %s", path)
        return ""

    text = path.read_text(encoding="utf-8")
    _prompt_cache[cache_key] = text
    return text


def _clear_prompt_cache() -> None:
    """Clear the prompt cache. Used by tests for isolation."""
    _prompt_cache.clear()


class TriageSpecialistPanelTool(DynamicTool):
    """Runs the specialist panel — parallel LLM calls with persona prompts."""

    @property
    def tool_name(self) -> str:
        return "triage_specialist_panel"

    @property
    def tool_description(self) -> str:
        return (
            "Runs the specialist diagnostic panel. For each selected specialist, "
            "loads their persona prompt + shared output instructions, calls the LLM "
            "in parallel, and parses JSON responses. Returns a list of specialist "
            "verdicts: [{specialist, diagnosis, confidence, thinking, tier}]. "
            "Failed or timed-out calls are treated as 'Insufficient information'.\n\n"
            "Valid specialists: emergency_physician, family_physician, internist, "
            "paediatrician, pharmacist.\n\n"
            "Confidence is an integer 0-100 where 100 = highest confidence in the "
            "diagnosis. A confidence of 0 indicates insufficient information or a "
            "failed/timed-out call.\n\n"
            "tier1_specialists are consulted first and given priority in consensus "
            "building; remaining specialists are tier2. Pass tier1_specialists as a "
            "JSON array to control consultation priority."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "specialists": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON array of specialist names to consult",
                ),
                "clinical_note": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON string of the structured clinical note",
                ),
                "tier1_specialists": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON array of tier 1 specialist names",
                    nullable=True,
                ),
            },
            required=["specialists", "clinical_note"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        raw_specs = args.get("specialists", "[]")
        try:
            specialists = (
                json.loads(raw_specs) if isinstance(raw_specs, str) else raw_specs
            )
        except (json.JSONDecodeError, TypeError):
            specialists = []

        raw_tier1 = args.get("tier1_specialists", "[]")
        try:
            tier1 = (
                json.loads(raw_tier1)
                if isinstance(raw_tier1, str)
                else (raw_tier1 or [])
            )
        except (json.JSONDecodeError, TypeError):
            tier1 = []
        tier1_set = set(tier1)

        clinical_note = args.get("clinical_note", "{}")
        shared_instructions = _load_shared_instructions(
            (self.tool_config or {}).get("prompts_dir", "")
        )

        # YAML anchor *specialist_model expands to a dict; extract model string + vertex kwargs
        raw_model = (self.tool_config or {}).get("model", "openai/gemini-2.5-flash-001")
        if isinstance(raw_model, dict):
            model = raw_model.get("model", "openai/gemini-2.5-flash-001")
            vertex_kwargs = {
                k: raw_model[k]
                for k in ("vertex_project", "vertex_location")
                if k in raw_model
            }
        else:
            model = raw_model
            vertex_kwargs = {}
        temperature = float((self.tool_config or {}).get("temperature", 0.3))
        timeout = int(
            (self.tool_config or {}).get("timeout", TRIAGE_SPECIALIST_TIMEOUT_S)
        )

        # Build tasks for parallel execution
        verdicts: list[dict] = []

        async def _consult_specialist(specialist: str) -> dict:
            """Make a single specialist LLM call with API-level retry."""
            persona = _load_prompt(
                specialist,
                (self.tool_config or {}).get("prompts_dir", ""),
            )
            if not persona:
                return {
                    "specialist": specialist,
                    "diagnosis": "Insufficient information",
                    "confidence": 0,
                    "thinking": f"No prompt file found for {specialist}",
                    "tier": "tier1" if specialist in tier1_set else "tier2",
                }

            system_prompt = f"{persona}\n\n{shared_instructions}"
            user_message = (
                f"[PATIENT_INPUT_START]\n{clinical_note}\n[PATIENT_INPUT_END]"
            )

            # Extract num_retries from model config for litellm's internal retry
            num_retries = 0
            if isinstance(raw_model, dict):
                num_retries = int(raw_model.get("num_retries", 0))

            # Transient errors that warrant an API-level retry.
            # Include both TimeoutError and asyncio.TimeoutError for Python 3.10
            # compatibility (they're the same class in 3.11+, but distinct in 3.10).
            _TRANSIENT_ERRORS = (
                asyncio.TimeoutError,
                TimeoutError,
                ConnectionError,
                OSError,
            )

            last_exc: Exception | None = None

            for api_attempt in range(2):  # 1 retry on transient API errors
                for parse_attempt in range(2):  # 1 retry on JSON parse failure
                    try:
                        import litellm

                        response = await asyncio.wait_for(
                            asyncio.to_thread(
                                litellm.completion,
                                model=model,
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_message},
                                ],
                                temperature=temperature,
                                max_tokens=500,
                                num_retries=num_retries,
                                **vertex_kwargs,
                            ),
                            timeout=timeout,
                        )
                        text = response.choices[0].message.content.strip()

                        result = extract_json_from_text(text)
                        if result is None:
                            log.debug(
                                "Parse failure for specialist=%s | response_len=%d | preview='%s'",
                                specialist,
                                len(text),
                                text[:50].replace("\n", " "),
                            )
                            raise json.JSONDecodeError(
                                "Specialist response returned None after extraction",
                                "[response redacted]",
                                0,
                            )
                        return {
                            "specialist": specialist,
                            "diagnosis": result.get("diagnosis", "Unknown"),
                            "confidence": int(result.get("confidence", 0)),
                            "thinking": result.get("thinking", ""),
                            "tier": "tier1" if specialist in tier1_set else "tier2",
                        }
                    except json.JSONDecodeError:
                        if parse_attempt == 0:
                            log.debug("JSON parse failed for %s, retrying", specialist)
                            continue
                        log.warning("JSON parse failed for %s after retry", specialist)
                        last_exc = last_exc or Exception(
                            "JSON parse failed after retry"
                        )
                    except _TRANSIENT_ERRORS as exc:
                        last_exc = exc
                        log.warning(
                            "Specialist %s transient error (attempt %d): %s",
                            specialist,
                            api_attempt + 1,
                            exc,
                        )
                        break  # break parse loop, retry API
                    except Exception as exc:
                        # Non-transient error — do not retry
                        last_exc = exc
                        log.warning("Specialist %s call failed: %s", specialist, exc)
                        return {
                            "specialist": specialist,
                            "diagnosis": "Insufficient information",
                            "confidence": 0,
                            "thinking": f"{type(exc).__name__}: {str(exc)[:100]}",
                            "tier": "tier1" if specialist in tier1_set else "tier2",
                        }
                else:
                    # Parse retries exhausted without transient error — don't retry API
                    break

                # Backoff before API retry
                if api_attempt == 0:
                    await asyncio.sleep(3)

            # All retries exhausted
            thinking = "Call failed or timed out"
            if last_exc:
                thinking = f"{type(last_exc).__name__}: {str(last_exc)[:100]}"

            return {
                "specialist": specialist,
                "diagnosis": "Insufficient information",
                "confidence": 0,
                "thinking": thinking,
                "tier": "tier1" if specialist in tier1_set else "tier2",
            }

        # Run all specialists in parallel
        tasks = [_consult_specialist(s) for s in specialists]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                log.warning("Specialist task exception: %s", r)
                continue
            verdicts.append(r)

        log.info(
            "Specialist panel: %d consulted, %d valid, %d insufficient",
            len(verdicts),
            sum(1 for v in verdicts if v["confidence"] > 0),
            sum(1 for v in verdicts if v["confidence"] == 0),
        )

        # Emit cumulative stage 3 progress
        await emit_triage_progress(
            tool_context,
            stage=3,
            stage_name="SPECIALIST_PANEL",
            detail=f"Received {len(verdicts)} specialist verdicts",
            specialist_verdicts=verdicts,
            log_identifier="[TriagePanel:Progress]",
        )

        return {
            "verdicts": verdicts,
            "total_consulted": len(verdicts),
            "insufficient_count": sum(1 for v in verdicts if v["confidence"] == 0),
        }
