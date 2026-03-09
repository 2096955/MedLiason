"""Uncertainty Quantifier — per-claim confidence profiling.

Produces a structured confidence assessment for the research answer,
including overall confidence, per-claim basis, and what new evidence
would change the conclusion.  Uses an LLM when configured, falls back
to a heuristic derived from evidence grades and source counts.
"""

import json as _json
import logging
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from litellm import acompletion as _litellm_acompletion

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.observability import log_tokens
from lifesci_common.llm_utils import json_mode_kwargs
from lifesci_common.triage_utils import extract_json_from_text

logger = logging.getLogger("medexpert.uncertainty_quantifier")

# ---------------------------------------------------------------------------
# LLM uncertainty analysis
# ---------------------------------------------------------------------------

_UNCERTAINTY_PROMPT_TEMPLATE = (
    "You are a medical research uncertainty analyst. Given a question and the\n"
    "evidence collected, produce a structured confidence assessment.\n\n"
    "QUESTION: {question}\n\n"
    "EVIDENCE SUMMARY:\n{evidence}\n\n"
    "Return ONLY a JSON object (no markdown fences):\n"
    '{{"overall_confidence": <0.0-1.0>,\n'
    '  "confidence_level": "high" | "moderate" | "low" | "insufficient",\n'
    '  "rationale": "<why this confidence level>",\n'
    '  "per_claim_confidence": [\n'
    '    {{"claim": "<key claim>",\n'
    '      "confidence": <0.0-1.0>,\n'
    '      "basis": "multiple_rcts" | "single_study" | "expert_opinion" | "extrapolation" | "meta_analysis",\n'
    '      "caveats": ["<limitation>"]}}\n'
    '  ],\n'
    '  "what_would_change_this": ["<new evidence that would shift the answer>"]\n'
    "}}"
)


async def _llm_uncertainty(
    question: str,
    evidence: list[dict],
    model: str,
    temperature: float,
    session_id: str,
    vertex_kwargs: dict | None = None,
) -> dict | None:
    """Use LLM to produce per-claim confidence assessment.

    Returns parsed dict on success, or ``None`` on failure.
    """
    ev_lines = []
    for i, ev in enumerate(evidence[:15]):
        title = ev.get("title", "Untitled")[:120]
        snippet = (ev.get("snippet", "") or "")[:200]
        grade = ev.get("grade", "N/A")
        ev_lines.append(f"{i + 1}. [{grade}] {title}: {snippet}")

    prompt = (
        _UNCERTAINTY_PROMPT_TEMPLATE
        .replace("{question}", question[:500])
        .replace("{evidence}", "\n".join(ev_lines))
    )

    try:
        response = await _litellm_acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=8192,
            **json_mode_kwargs(model),
            **(vertex_kwargs or {}),
        )
        content = response.choices[0].message.content.strip()

        # Token logging
        usage = getattr(response, "usage", None)
        if usage:
            log_tokens(
                session_id=session_id,
                model=model,
                prompt_tokens=getattr(usage, "prompt_tokens", 0),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )

        # Lenient JSON extraction (handles fences, trailing commas, truncation)
        parsed = extract_json_from_text(content)
        return parsed

    except Exception as exc:
        logger.warning(
            "[uncertainty_quantifier] LLM uncertainty analysis failed: %s", exc
        )
        return None


# ---------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------

_GRADE_TO_SCORE = {"High": 0.85, "Moderate": 0.6, "Low": 0.35, "Very Low": 0.15}


def _heuristic_confidence(evidence: list[dict]) -> dict:
    """Derive confidence from evidence grades and source count."""
    if not evidence:
        return {
            "overall_confidence": 0.0,
            "confidence_level": "insufficient",
            "rationale": "No evidence collected.",
            "per_claim_confidence": [],
            "what_would_change_this": ["Any relevant evidence"],
            "method": "heuristic",
        }

    grades = [ev.get("grade", "Very Low") for ev in evidence]
    scores = [_GRADE_TO_SCORE.get(g, 0.15) for g in grades]
    avg_score = sum(scores) / len(scores)

    # Boost for multiple sources
    source_bonus = min(len(evidence) * 0.02, 0.1)  # up to +0.1 for 5+ sources
    overall = min(avg_score + source_bonus, 1.0)

    if overall >= 0.7:
        level = "high"
    elif overall >= 0.45:
        level = "moderate"
    elif overall >= 0.2:
        level = "low"
    else:
        level = "insufficient"

    rationale_parts = [
        f"Based on {len(evidence)} sources",
        f"average evidence grade score {avg_score:.2f}",
    ]
    if source_bonus > 0:
        rationale_parts.append(f"+{source_bonus:.2f} multi-source bonus")

    return {
        "overall_confidence": round(overall, 3),
        "confidence_level": level,
        "rationale": "; ".join(rationale_parts),
        "per_claim_confidence": [],
        "what_would_change_this": [],
        "method": "heuristic",
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class UncertaintyQuantifierTool(DynamicTool):
    """Produces structured confidence profiles for research answers."""

    def __init__(self, tool_config: Optional[dict] = None, **kwargs):
        super().__init__(tool_config=tool_config, **kwargs)
        cfg = tool_config or {}
        raw_model = cfg.get("model")
        if isinstance(raw_model, dict):
            self.model: str = raw_model.get("model", "")
            self._vertex_kwargs: dict = {
                k: raw_model[k]
                for k in ("vertex_project", "vertex_location")
                if k in raw_model
            }
        elif raw_model:
            self.model = raw_model
            self._vertex_kwargs = {}
        else:
            self.model = ""
            self._vertex_kwargs = {}
        self.temperature: float = float(cfg.get("temperature", 0.1))

    @property
    def tool_name(self) -> str:
        return "uncertainty_quantifier"

    @property
    def tool_description(self) -> str:
        return (
            "Produces a structured confidence profile for the research answer. "
            "Returns overall confidence (0-1) and confidence_level. When an LLM "
            "model is configured, also returns per_claim_confidence with basis "
            "(multiple_rcts, single_study, expert_opinion, etc.), caveats per "
            "claim, and what_would_change_this. On the heuristic fallback path "
            "(no model), per_claim_confidence and what_would_change_this are "
            "empty. Call after evidence collection and before report generation. "
            "Assesses the COMPLETE research answer, NOT individual studies. "
            "For per-study GRADE scoring, use evidence_grader instead."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "question": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The research question being assessed.",
                ),
                "evidence_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON string of evidence items array. Each item should have: "
                        "title, snippet, grade, and optionally source/domain."
                    ),
                ),
            },
            required=["question", "evidence_json"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        question = args.get("question", "").strip()
        if not question:
            return {"error": "Question is required"}

        try:
            evidence = _json.loads(args.get("evidence_json", "[]"))
        except _json.JSONDecodeError:
            return {"error": "Invalid JSON in evidence_json"}

        if not isinstance(evidence, list):
            return {"error": "evidence_json must be a JSON array"}

        # Try LLM when configured and evidence available
        if self.model and evidence:
            session_id = ""
            if hasattr(tool_context, "session") and tool_context.session:
                session_id = getattr(tool_context.session, "id", "")
            llm_result = await _llm_uncertainty(
                question=question,
                evidence=evidence,
                model=self.model,
                temperature=self.temperature,
                session_id=session_id,
                vertex_kwargs=self._vertex_kwargs,
            )
            if llm_result:
                try:
                    overall = float(llm_result.get("overall_confidence") or 0.5)
                except (TypeError, ValueError):
                    overall = 0.5
                return {
                    "overall_confidence": overall,
                    "confidence_level": llm_result.get("confidence_level", "moderate"),
                    "rationale": llm_result.get("rationale", ""),
                    "per_claim_confidence": llm_result.get("per_claim_confidence", []),
                    "what_would_change_this": llm_result.get("what_would_change_this", []),
                    "method": "llm",
                }

        # Heuristic fallback
        return _heuristic_confidence(evidence)
