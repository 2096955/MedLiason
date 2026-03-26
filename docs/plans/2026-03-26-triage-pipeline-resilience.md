# Triage Pipeline Resilience Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix two triage pipeline failures observed in production traces: specialist panel total timeout and intake agent premature task completion.

**Architecture:** Two independent fixes — (1) add API-level retry + increased timeout + diagnostics to the specialist panel's direct litellm calls, (2) add a prompt hardening + after_model_callback guard to prevent the intake agent from completing without calling the peer orchestrator.

**Tech Stack:** Python, asyncio, litellm, ADK callbacks (CallbackContext/LlmResponse), pytest

---

### Task 1: Increase Specialist Panel Timeout

**Files:**
- Modify: `medexpert/src/lifesci_common/constants.py:496`

**Step 1: Change the timeout constant**

In `medexpert/src/lifesci_common/constants.py` at line 496, change:

```python
TRIAGE_SPECIALIST_TIMEOUT_S = 30
```

to:

```python
TRIAGE_SPECIALIST_TIMEOUT_S = 60
```

**Step 2: Verify no other code hardcodes 30s**

Run: `cd medexpert && grep -rn "timeout.*30" src/lifesci_tools/triage_specialist_panel.py`
Expected: No matches (the tool reads from config/constant, not hardcoded)

**Step 3: Commit**

```bash
git add medexpert/src/lifesci_common/constants.py
git commit --signoff -m "fix: increase triage specialist timeout 30s→60s"
```

---

### Task 2: Add API-Level Retry to Specialist Panel

**Files:**
- Modify: `medexpert/src/lifesci_tools/triage_specialist_panel.py:119-275`
- Test: `medexpert/tests/unit/test_triage_specialist_panel.py`

**Step 2a: Write failing tests for retry behavior**

Add these tests to `medexpert/tests/unit/test_triage_specialist_panel.py`:

```python
@pytest.mark.asyncio
async def test_specialist_panel_retries_on_timeout(tool, mock_ctx):
    """TimeoutError on first attempt triggers one retry."""
    good_response = _mock_llm_response(
        '{"diagnosis": "ME/CFS", "confidence": 75, "thinking": "Classic PEM"}'
    )
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError("simulated timeout")
        return good_response

    with patch("litellm.completion", side_effect=side_effect):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    assert result["verdicts"][0]["diagnosis"] == "ME/CFS"
    assert call_count == 2  # 1 failure + 1 retry


@pytest.mark.asyncio
async def test_specialist_panel_retries_on_transient_error(tool, mock_ctx):
    """Transient litellm errors (503-like) trigger one retry."""
    good_response = _mock_llm_response(
        '{"diagnosis": "Hypothyroidism", "confidence": 60, "thinking": "TSH"}'
    )
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("503 Service Unavailable")
        return good_response

    with patch("litellm.completion", side_effect=side_effect):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    assert result["verdicts"][0]["diagnosis"] == "Hypothyroidism"
    assert call_count == 2


@pytest.mark.asyncio
async def test_specialist_panel_diagnostic_thinking_on_failure(tool, mock_ctx):
    """When all retries fail, the verdict thinking field contains the exception info."""
    with patch("litellm.completion", side_effect=TimeoutError("vertex cold start")):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    verdict = result["verdicts"][0]
    assert verdict["confidence"] == 0
    assert "TimeoutError" in verdict["thinking"]
    assert "vertex cold start" in verdict["thinking"]


@pytest.mark.asyncio
async def test_specialist_panel_no_retry_on_non_transient_error(tool, mock_ctx):
    """Non-transient errors (e.g. ValueError) do not trigger retry."""
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise ValueError("bad model name")

    with patch("litellm.completion", side_effect=side_effect):
        result = await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    assert result["verdicts"][0]["confidence"] == 0
    # Should NOT retry on ValueError — only 1 call
    assert call_count == 1


@pytest.mark.asyncio
async def test_specialist_panel_forwards_num_retries(tool, mock_ctx):
    """num_retries from model config dict is forwarded to litellm.completion."""
    tool.tool_config["model"] = {"model": "test-model", "num_retries": 4}
    good = _mock_llm_response(
        '{"diagnosis": "Anemia", "confidence": 50, "thinking": "low Hb"}'
    )

    with patch("litellm.completion", return_value=good) as mock_comp:
        await tool._run_async_impl(
            {
                "specialists": '["family_physician"]',
                "clinical_note": '{"chief_complaint": "fatigue"}',
            },
            mock_ctx,
        )

    _, kwargs = mock_comp.call_args
    assert kwargs.get("num_retries") == 4
```

Add this import at the top of the test file (after the existing imports):

```python
import asyncio  # noqa: F401 — needed for TimeoutError in tests
```

**Step 2b: Run tests to verify they fail**

Run: `cd medexpert && python -m pytest tests/unit/test_triage_specialist_panel.py -v -k "retry or diagnostic or forwards_num" --no-header`
Expected: All 5 new tests FAIL

**Step 2c: Implement retry logic in `_consult_specialist`**

Replace the `_consult_specialist` method body (lines 157-240) in `medexpert/src/lifesci_tools/triage_specialist_panel.py` with:

```python
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

            # Transient errors that warrant an API-level retry
            _TRANSIENT_ERRORS = (asyncio.TimeoutError, ConnectionError, OSError)

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
                        last_exc = last_exc or Exception("JSON parse failed after retry")
                    except _TRANSIENT_ERRORS as exc:
                        last_exc = exc
                        log.warning(
                            "Specialist %s transient error (attempt %d): %s",
                            specialist, api_attempt + 1, exc,
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
```

**Step 2d: Run tests to verify they pass**

Run: `cd medexpert && python -m pytest tests/unit/test_triage_specialist_panel.py -v --no-header`
Expected: ALL tests pass (new + existing)

**Step 2e: Commit**

```bash
git add medexpert/src/lifesci_tools/triage_specialist_panel.py medexpert/tests/unit/test_triage_specialist_panel.py
git commit --signoff -m "fix: add API-level retry + diagnostics to specialist panel

- Retry once on transient errors (TimeoutError, ConnectionError, OSError)
- 3s backoff between API retries
- Forward num_retries from model config to litellm.completion
- Include exception type+message in verdict thinking field
- Non-transient errors (ValueError etc.) fail immediately without retry"
```

---

### Task 3: Create Triage Handoff Guard Callback

**Files:**
- Create: `medexpert/src/lifesci_tools/triage_handoff_guard.py`
- Test: `medexpert/tests/unit/test_triage_handoff_guard.py`

**Step 3a: Write failing tests**

Create `medexpert/tests/unit/test_triage_handoff_guard.py`:

```python
"""Tests for the triage handoff guard after_model_callback."""

from unittest.mock import MagicMock

import pytest
from google.genai import types as adk_types

from lifesci_tools.triage_handoff_guard import triage_handoff_guard_callback


def _make_callback_context(state: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.state = state or {}
    return ctx


def _make_llm_response(
    text: str | None = None,
    function_calls: list[tuple[str, dict]] | None = None,
    partial: bool = False,
) -> MagicMock:
    """Build a mock LlmResponse with optional text and function calls."""
    resp = MagicMock()
    resp.partial = partial

    parts = []
    if text:
        parts.append(adk_types.Part(text=text))
    for name, args in (function_calls or []):
        parts.append(
            adk_types.Part(
                function_call=adk_types.FunctionCall(
                    id=f"call_{name}", name=name, args=args
                )
            )
        )

    resp.content = adk_types.Content(role="model", parts=parts) if parts else None
    return resp


def _make_host(agent_name: str = "TriageIntakeAgent", handoff_guard: bool = True):
    host = MagicMock()
    host.get_config.side_effect = lambda key, default=None: {
        "agent_name": agent_name,
        "app_config": {"triage_handoff_guard": handoff_guard},
    }.get(key, default)
    return host


class TestHandoffGuardRejects:
    """Cases where the guard should soft-reject."""

    def test_rejects_text_only_after_complete_intake(self):
        """Text mentioning routing without peer tool call → reject."""
        ctx = _make_callback_context(
            state={"_triage_intake_complete": True}
        )
        resp = _make_llm_response(
            text="Thank you. I'm now routing your case to our specialist panel."
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)

        assert result is not None  # Modified response returned
        # Should contain guidance text, not the original
        assert any(
            "peer_TriageOrchestratorAgent" in (p.text or "")
            for p in result.content.parts
        )

    def test_rejects_when_specialist_panel_mentioned(self):
        """Any text indicating handoff intent without tool call → reject."""
        ctx = _make_callback_context(
            state={"_triage_intake_complete": True}
        )
        resp = _make_llm_response(
            text="I'm routing this to the specialist panel for analysis."
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is not None


class TestHandoffGuardPasses:
    """Cases where the guard should NOT reject (return None)."""

    def test_passes_text_plus_peer_tool_call(self):
        """Text + peer_TriageOrchestratorAgent call → pass through."""
        ctx = _make_callback_context(
            state={"_triage_intake_complete": True}
        )
        resp = _make_llm_response(
            text="Thank you. Routing your case now.",
            function_calls=[
                ("peer_TriageOrchestratorAgent", {"task_description": "triage"})
            ],
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None  # No modification

    def test_passes_when_intake_not_complete(self):
        """If triage_intake hasn't returned complete, guard is inactive."""
        ctx = _make_callback_context(state={})
        resp = _make_llm_response(
            text="What is the main reason you are seeking medical help today?"
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None

    def test_passes_when_guard_disabled(self):
        """Guard disabled in config → always pass through."""
        ctx = _make_callback_context(
            state={"_triage_intake_complete": True}
        )
        resp = _make_llm_response(
            text="Routing to specialist panel."
        )
        host = _make_host(handoff_guard=False)

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None

    def test_passes_partial_responses(self):
        """Streaming partial responses are never rejected."""
        ctx = _make_callback_context(
            state={"_triage_intake_complete": True}
        )
        resp = _make_llm_response(
            text="Routing to specialist panel.", partial=True
        )
        host = _make_host()

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None

    def test_passes_for_non_intake_agent(self):
        """Guard only fires for TriageIntakeAgent."""
        ctx = _make_callback_context(
            state={"_triage_intake_complete": True}
        )
        resp = _make_llm_response(
            text="Routing to specialist panel."
        )
        host = _make_host(agent_name="OrchestratorAgent")

        result = triage_handoff_guard_callback(ctx, resp, host)
        assert result is None
```

**Step 3b: Run tests to verify they fail**

Run: `cd medexpert && python -m pytest tests/unit/test_triage_handoff_guard.py -v --no-header`
Expected: ImportError (module doesn't exist yet)

**Step 3c: Implement the handoff guard callback**

Create `medexpert/src/lifesci_tools/triage_handoff_guard.py`:

```python
"""Triage handoff guard — after_model_callback preventing premature task completion.

When the TriageIntakeAgent's triage_intake tool returns status="complete", the LLM
MUST call peer_TriageOrchestratorAgent in the same response turn. If the LLM
generates text indicating handoff intent without the peer tool call, this callback
soft-rejects the response so the LLM retries with the tool call included.
"""

import logging
import re
from typing import TYPE_CHECKING, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types as adk_types

if TYPE_CHECKING:
    from solace_agent_mesh.agent.sac.component import SamAgentComponent

log = logging.getLogger(__name__)

# Patterns indicating handoff intent in the LLM's text response
_HANDOFF_PATTERNS = re.compile(
    r"routing your case|specialist panel|routing.*to.*specialist|"
    r"routing.*case.*analysis|sending.*orchestrator",
    re.IGNORECASE,
)

_PEER_TOOL_NAME = "peer_TriageOrchestratorAgent"


def triage_handoff_guard_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
    host_component: "SamAgentComponent",
) -> Optional[LlmResponse]:
    """After-model callback that ensures the intake agent calls the peer tool.

    Only active when:
    - Agent is TriageIntakeAgent
    - Config has triage_handoff_guard: true
    - Session state has _triage_intake_complete: True
    - LLM response is final (not partial/streaming)

    Returns None (pass-through) when no issue detected.
    Returns a modified LlmResponse with soft-rejection text when the LLM
    generated handoff text without the peer tool call.
    """
    # Gate: only run for TriageIntakeAgent
    agent_name = host_component.get_config("agent_name", "")
    if "TriageIntakeAgent" not in agent_name:
        return None

    # Gate: config flag
    app_config = host_component.get_config("app_config", {})
    if not (app_config or {}).get("triage_handoff_guard"):
        return None

    # Skip partial (streaming) responses
    if llm_response.partial:
        return None

    # Gate: triage_intake must have returned complete
    state = callback_context.state or {}
    if not state.get("_triage_intake_complete"):
        return None

    # Check if response has content
    if not llm_response.content or not llm_response.content.parts:
        return None

    # Check for peer tool call
    has_peer_call = any(
        p.function_call and p.function_call.name == _PEER_TOOL_NAME
        for p in llm_response.content.parts
    )
    if has_peer_call:
        return None  # Correct behavior — pass through

    # Check for handoff intent in text
    text_parts = [p.text for p in llm_response.content.parts if p.text]
    combined_text = " ".join(text_parts)

    if not _HANDOFF_PATTERNS.search(combined_text):
        return None  # No handoff intent detected — pass through

    # SOFT REJECT: LLM said it would route but didn't call the tool
    log.warning(
        "[Callback:TriageHandoffGuard] Soft-rejecting response: "
        "handoff text detected without %s tool call. Agent will retry.",
        _PEER_TOOL_NAME,
    )

    rejection_text = (
        f"You indicated you would route the case but did not call "
        f"{_PEER_TOOL_NAME}. You MUST call {_PEER_TOOL_NAME} with the "
        f"clinical note JSON as the task_description. Include your 'Thank you' "
        f"text AND the tool call in the same response."
    )

    return LlmResponse(
        content=adk_types.Content(
            role="model",
            parts=[adk_types.Part(text=rejection_text)],
        ),
    )
```

**Step 3d: Run tests to verify they pass**

Run: `cd medexpert && python -m pytest tests/unit/test_triage_handoff_guard.py -v --no-header`
Expected: ALL 7 tests pass

**Step 3e: Commit**

```bash
git add medexpert/src/lifesci_tools/triage_handoff_guard.py medexpert/tests/unit/test_triage_handoff_guard.py
git commit --signoff -m "feat: add triage handoff guard after_model_callback

Prevents the intake agent from completing a task with text-only response
when triage_intake returned status='complete'. The guard soft-rejects so
the LLM retries with the peer_TriageOrchestratorAgent tool call included."
```

---

### Task 4: Wire Handoff Guard into SAM Callback Chain

**Files:**
- Modify: `src/solace_agent_mesh/agent/adk/setup.py:1469-1491`

**Step 4a: Add the handoff guard registration**

In `src/solace_agent_mesh/agent/adk/setup.py`, after the protocol_step_validator block (line ~1491) and before the artifact block processing (line ~1493), add:

```python
        # 2.6 Triage handoff guard (TriageIntakeAgent only)
        # Prevents premature task completion when intake is done but peer
        # tool call is missing. Gated by triage_handoff_guard: true in app_config.
        if (component.get_config("app_config", {}) or {}).get("triage_handoff_guard"):
            try:
                from lifesci_tools.triage_handoff_guard import (
                    triage_handoff_guard_callback,
                )

                handoff_guard_cb = functools.partial(
                    triage_handoff_guard_callback, host_component=component
                )
                callbacks_in_order_for_after_model.append(handoff_guard_cb)
                log.debug(
                    "%s Added triage_handoff_guard_callback to after_model chain.",
                    component.log_identifier,
                )
            except ImportError:
                log.warning(
                    "%s triage_handoff_guard enabled but lifesci_tools not found. "
                    "Handoff guard will be skipped.",
                    component.log_identifier,
                )
```

**Step 4b: Commit**

```bash
git add src/solace_agent_mesh/agent/adk/setup.py
git commit --signoff -m "feat: wire triage handoff guard into after_model callback chain

Registered after protocol_step_validator, before artifact block processing.
Gated by triage_handoff_guard: true in app_config — non-MedExpert
deployments are completely unaffected."
```

---

### Task 5: Set Session State Flag in triage_intake Tool

**Files:**
- Modify: `medexpert/src/lifesci_tools/triage_intake.py`
- Test: `medexpert/tests/unit/test_triage_intake.py`

The handoff guard checks `session.state["_triage_intake_complete"]`. The `triage_intake` tool must set this flag when it returns `status: "complete"`.

**Step 5a: Write failing test**

Add to `medexpert/tests/unit/test_triage_intake.py`:

```python
@pytest.mark.asyncio
async def test_triage_intake_sets_complete_flag_in_session_state(mock_tool_context):
    """When intake returns status='complete', _triage_intake_complete is set in state."""
    tool = TriageIntakeTool()
    mock_tool_context.state = {}
    result = await tool._run_async_impl(
        {
            "chief_complaint": "extreme fatigue",
            "symptoms": '[{"symptom": "fatigue", "duration": "7 months", "severity": "10/10"}]',
        },
        mock_tool_context,
    )

    assert result["status"] == "complete"
    assert mock_tool_context.state.get("_triage_intake_complete") is True
```

**Step 5b: Run test to verify it fails**

Run: `cd medexpert && python -m pytest tests/unit/test_triage_intake.py::test_triage_intake_sets_complete_flag_in_session_state -v --no-header`
Expected: FAIL (state flag not set)

**Step 5c: Add flag to triage_intake tool**

In `medexpert/src/lifesci_tools/triage_intake.py`, find the return path where `status: "complete"` is set and add just before the return:

```python
        # Signal to handoff guard that intake is done
        if result.get("status") == "complete":
            tool_context.state["_triage_intake_complete"] = True
```

**Step 5d: Run tests to verify pass**

Run: `cd medexpert && python -m pytest tests/unit/test_triage_intake.py -v --no-header`
Expected: ALL pass

**Step 5e: Commit**

```bash
git add medexpert/src/lifesci_tools/triage_intake.py medexpert/tests/unit/test_triage_intake.py
git commit --signoff -m "feat: set _triage_intake_complete flag in session state

Used by the triage_handoff_guard callback to detect when the intake
agent should be calling peer_TriageOrchestratorAgent."
```

---

### Task 6: Update Triage Intake YAML Configs (All 3 Variants)

**Files:**
- Modify: `medexpert/configs/agents/triage_intake.yaml`
- Modify: `medexpert/configs/pro/agents/triage_intake.yaml`
- Modify: `medexpert/configs/opus/agents/triage_intake.yaml`

**Step 6a: Add handoff guard config and prompt hardening to standard config**

In `medexpert/configs/agents/triage_intake.yaml`, add `triage_handoff_guard: true` to `app_config` (after `max_llm_calls_per_task: 15`):

```yaml
      triage_handoff_guard: true
```

In the same file, find the HANDOFF CONTRACT section and add after the `status="complete"` block (after line "panel for analysis."):

```
        CRITICAL: When status="complete", you MUST call peer_TriageOrchestratorAgent
        in the SAME response turn as the "Thank you" text. Do NOT generate a text-only
        response — the task will be marked complete and the patient will never receive
        their triage results. Every response after intake is complete MUST include the
        peer tool call.
```

**Step 6b: Apply identical changes to Pro and Opus variants**

Apply the same two changes (config flag + prompt text) to:
- `medexpert/configs/pro/agents/triage_intake.yaml`
- `medexpert/configs/opus/agents/triage_intake.yaml`

**Step 6c: Verify config parity**

Run: `cd medexpert && python -m pytest tests/unit/test_config_parity.py -v --no-header`
Expected: PASS (if config parity test exists and covers triage_intake)

**Step 6d: Commit**

```bash
git add medexpert/configs/agents/triage_intake.yaml medexpert/configs/pro/agents/triage_intake.yaml medexpert/configs/opus/agents/triage_intake.yaml
git commit --signoff -m "feat: enable triage handoff guard + prompt hardening (all 3 variants)

- triage_handoff_guard: true in app_config
- CRITICAL prompt text requiring peer tool call in same response turn"
```

---

### Task 7: Run Full Test Suite

**Step 7a: Run all medexpert unit tests**

Run: `cd medexpert && python -m pytest tests/unit/ -v --no-header -x`
Expected: ALL pass

**Step 7b: Run SAM framework tests (verify no regressions from setup.py change)**

Run: `cd .. && python -m pytest tests/unit/ -v --no-header -x -k "not stress and not long_soak"`
Expected: ALL pass

**Step 7c: Lint**

Run: `cd medexpert && ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: No errors

**Step 7d: Fix any lint issues and commit**

If lint issues found, fix and commit:

```bash
git add -u
git commit --signoff -m "style: fix lint issues from triage resilience changes"
```
