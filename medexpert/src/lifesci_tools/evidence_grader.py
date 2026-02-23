"""GRADE Methodology Evidence Grader.

Scores evidence quality using the GRADE (Grading of Recommendations,
Assessment, Development and Evaluations) framework. Pure logic tool —
no external API calls.
"""

from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import (
    GRADE_DOWNGRADE_FACTORS,
    GRADE_LEVELS,
    GRADE_STUDY_TYPE_SCORES,
    GRADE_UPGRADE_FACTORS,
)


def _compute_grade(score: float) -> str:
    """Map a numeric score to a GRADE level string."""
    for level, (low, high) in GRADE_LEVELS.items():
        if low <= score < high:
            return level
    return "Very Low"


class EvidenceGraderTool(DynamicTool):
    """Scores evidence using the GRADE methodology framework."""

    @property
    def tool_name(self) -> str:
        return "evidence_grader"

    @property
    def tool_description(self) -> str:
        return (
            "Grades evidence quality using the GRADE methodology. Takes study type, "
            "sample size, methodology descriptors, and effect size. Returns a score, "
            "grade level (High/Moderate/Low/Very Low), rationale, and factors applied."
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

        return {
            "score": round(score, 2),
            "grade": grade,
            "study_type": study_type,
            "rationale": "; ".join(rationale_parts),
            "factors_applied": factors_applied,
        }
