"""GRADE Methodology Evidence Grader.

Scores evidence quality using the GRADE (Grading of Recommendations,
Assessment, Development and Evaluations) framework.  When an LLM model
is configured (via ``tool_config``), adds a contextual quality
assessment that extracts nuanced signals (blinding, funding bias,
generalizability) from the study description.  Falls back to pure
heuristic scoring when the LLM is not configured or fails.

Note: The LLM path is intentionally **not** wired into specialist YAMLs
by default because evidence_grader runs 3-10 times per specialist.
Enable it only on agents where the budget allows (e.g., orchestrator).
"""

import json as _json
import logging
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from litellm import acompletion as _litellm_acompletion

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import (
    GRADE_DOWNGRADE_FACTORS,
    GRADE_LEVELS,
    GRADE_STUDY_TYPE_SCORES,
    GRADE_UPGRADE_FACTORS,
)
from lifesci_common.observability import log_tokens
from lifesci_common.llm_utils import json_mode_kwargs
from lifesci_common.triage_utils import extract_json_from_text

logger = logging.getLogger("medexpert.evidence_grader")


def _compute_grade(score: float) -> str:
    """Map a numeric score to a GRADE level string."""
    for level, (low, high) in GRADE_LEVELS.items():
        if low <= score < high:
            return level
    return "Very Low"


# ---------------------------------------------------------------------------
# LLM contextual quality assessment
# ---------------------------------------------------------------------------

_QUALITY_PROMPT_TEMPLATE = (
    "Assess this {study_type} study. Return ONLY a JSON object (no markdown fences).\n\n"
    "STUDY: {description}\n\n"
    "Return:\n"
    '{{"sample_size_estimate": <int or null>,\n'
    '  "blinding": "double" | "single" | "open" | "unknown",\n'
    '  "funding_bias_risk": "low" | "medium" | "high" | "unknown",\n'
    '  "generalizability": "broad" | "narrow" | "unknown",\n'
    '  "confidence_note": "<one sentence on how confident we should be>"}}'
)

# Map LLM-detected signals to GRADE downgrade/upgrade factors
_LLM_SIGNAL_MAPPINGS = {
    "blinding": {
        "open": "no_blinding",       # -1.0
        "single": None,               # no penalty for single-blind
        "double": None,
        "unknown": None,
    },
    "funding_bias_risk": {
        "high": "publication_bias",    # -0.5
        "medium": None,
        "low": None,
        "unknown": None,
    },
    "generalizability": {
        "narrow": "indirectness",      # -0.5
        "broad": None,
        "unknown": None,
    },
}


async def _llm_quality_assessment(
    study_description: str,
    study_type: str,
    model: str,
    temperature: float,
    session_id: str,
    vertex_kwargs: dict | None = None,
) -> dict | None:
    """Use LLM to extract quality signals from study text.

    Returns parsed dict on success, or ``None`` on failure.
    """
    prompt = (
        _QUALITY_PROMPT_TEMPLATE
        .replace("{study_type}", study_type)
        .replace("{description}", study_description[:1500])
    )

    try:
        response = await _litellm_acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=2048,
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
            "[evidence_grader] LLM quality assessment failed: %s", exc
        )
        return None


class EvidenceGraderTool(DynamicTool):
    """Scores evidence using the GRADE methodology framework."""

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
        return "evidence_grader"

    @property
    def tool_description(self) -> str:
        return (
            "Grades evidence quality using the GRADE methodology. Takes study type, "
            "sample size, methodology descriptors, and effect size. Returns a score, "
            "grade level (High/Moderate/Low/Very Low), rationale, and factors applied. "
            "Scores INDIVIDUAL studies, NOT the overall research answer. "
            "For answer-level confidence profiling, use uncertainty_quantifier instead."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "study_type": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "Type of study: meta_analysis, systematic_review, rct, cohort, "
                        "case_control, cross_sectional, case_report, case_series, "
                        "expert_opinion, narrative_review"
                    ),
                ),
                "sample_size": adk_types.Schema(
                    type=adk_types.Type.INTEGER,
                    description="Number of participants/subjects in the study",
                    nullable=True,
                ),
                "methodology_descriptors": adk_types.Schema(
                    type=adk_types.Type.ARRAY,
                    items=adk_types.Schema(type=adk_types.Type.STRING),
                    description=(
                        "List of methodology factors. Downgrades: small_sample, "
                        "no_blinding, high_dropout, inconsistency, indirectness, "
                        "imprecision, publication_bias. Upgrades: large_effect, "
                        "dose_response, confounders_reduce_effect"
                    ),
                    nullable=True,
                ),
                "effect_size": adk_types.Schema(
                    type=adk_types.Type.NUMBER,
                    description="Reported effect size (e.g., odds ratio, hazard ratio). Optional.",
                    nullable=True,
                ),
                "study_description": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "Free-text description of the study (abstract, title, or summary). "
                        "When provided and an LLM model is configured, enables contextual "
                        "quality assessment for nuanced signals (blinding, funding bias, "
                        "generalizability). Optional."
                    ),
                    nullable=True,
                ),
            },
            required=["study_type"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        study_type = args.get("study_type", "").lower().strip()
        sample_size = args.get("sample_size")
        descriptors = args.get("methodology_descriptors") or []
        effect_size = args.get("effect_size")
        study_description = args.get("study_description", "")

        # Base score from study type
        base_score = GRADE_STUDY_TYPE_SCORES.get(study_type)
        if base_score is None:
            return {
                "error": f"Unknown study type '{study_type}'",
                "valid_types": list(GRADE_STUDY_TYPE_SCORES.keys()),
            }

        score = base_score
        factors_applied = []
        rationale_parts = [f"Base score {base_score} for {study_type}"]
        confidence_note = ""
        method = "heuristic"

        # ------------------------------------------------------------------
        # LLM contextual quality assessment (optional)
        # ------------------------------------------------------------------
        llm_signals: dict | None = None
        if self.model and study_description:
            session_id = ""
            if hasattr(tool_context, "session") and tool_context.session:
                session_id = getattr(tool_context.session, "id", "")
            llm_signals = await _llm_quality_assessment(
                study_description=study_description,
                study_type=study_type,
                model=self.model,
                temperature=self.temperature,
                session_id=session_id,
                vertex_kwargs=self._vertex_kwargs,
            )

        if llm_signals:
            method = "llm_enhanced"
            confidence_note = llm_signals.get("confidence_note", "")

            # LLM-inferred sample size — only use if caller didn't provide one
            if sample_size is None:
                llm_sample = llm_signals.get("sample_size_estimate")
                if isinstance(llm_sample, int) and llm_sample > 0:
                    sample_size = llm_sample
                    rationale_parts.append(f"LLM-inferred sample_size={llm_sample}")

            # Map LLM signals to GRADE factors (only add if not already present)
            existing_factors = {d.lower().strip() for d in descriptors}
            for signal_key, mapping in _LLM_SIGNAL_MAPPINGS.items():
                signal_value = llm_signals.get(signal_key, "unknown")
                grade_factor = mapping.get(signal_value)
                if grade_factor and grade_factor not in existing_factors:
                    descriptors = list(descriptors) + [grade_factor]
                    rationale_parts.append(
                        f"LLM-detected {signal_key}={signal_value} → {grade_factor}"
                    )

        # Auto-detect small sample if sample_size provided
        if sample_size is not None and sample_size < 100:
            if "small_sample" not in descriptors:
                descriptors = list(descriptors) + ["small_sample"]

        # Apply downgrade factors
        for descriptor in descriptors:
            descriptor_lower = descriptor.lower().strip()
            if descriptor_lower in GRADE_DOWNGRADE_FACTORS:
                adjustment = GRADE_DOWNGRADE_FACTORS[descriptor_lower]
                score += adjustment
                factors_applied.append(
                    {"factor": descriptor_lower, "adjustment": adjustment, "type": "downgrade"}
                )
                rationale_parts.append(f"Downgrade {adjustment} for {descriptor_lower}")

            elif descriptor_lower in GRADE_UPGRADE_FACTORS:
                adjustment = GRADE_UPGRADE_FACTORS[descriptor_lower]
                score += adjustment
                factors_applied.append(
                    {"factor": descriptor_lower, "adjustment": adjustment, "type": "upgrade"}
                )
                rationale_parts.append(f"Upgrade +{adjustment} for {descriptor_lower}")

        # Auto-detect large effect from effect_size
        if effect_size is not None and abs(effect_size) >= 2.0:
            if not any(f["factor"] == "large_effect" for f in factors_applied):
                adjustment = GRADE_UPGRADE_FACTORS["large_effect"]
                score += adjustment
                factors_applied.append(
                    {"factor": "large_effect", "adjustment": adjustment, "type": "upgrade"}
                )
                rationale_parts.append(
                    f"Upgrade +{adjustment} for large_effect (effect_size={effect_size})"
                )

        # Clamp score to [0, 5]
        score = max(0.0, min(5.0, score))
        grade = _compute_grade(score)

        result = {
            "score": round(score, 2),
            "grade": grade,
            "study_type": study_type,
            "rationale": "; ".join(rationale_parts),
            "factors_applied": factors_applied,
            "method": method,
        }
        if confidence_note:
            result["confidence_note"] = confidence_note
        return result
