"""TriageProgressData model for Medical Triage pipeline SSE events.

Moved here from SAM core (data_parts.py) to keep the framework package
unmodified and installable from PyPI.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TriageProgressData(BaseModel):
    """
    Data model for Medical Triage pipeline progress updates.
    Provides structured stage tracking for the triage intake -> specialist
    panel -> consensus -> evaluation -> NBA pipeline.

    Uses cumulative emission: each SSE event contains the full accumulated
    pipeline state up to that point. Frontend replaces wholesale on each event.
    """

    type: Literal["triage_progress"] = Field(
        "triage_progress",
        description="The constant type for this data part.",
    )
    stage: int = Field(..., description="Current triage stage (1-5)")
    stage_name: str = Field(
        ...,
        description="Stage name: INTAKE, ROUTING, SPECIALIST_PANEL, CONSENSUS, "
        "EVALUATION, NBA",
    )
    total_stages: int = Field(default=5, description="Total stages in triage pipeline")
    detail: str = Field(..., description="Human-readable description of current activity")
    specialist_verdicts: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Cumulative list of specialist verdicts received so far. "
        "Each: {specialist, diagnosis, confidence, thinking, tier}",
    )
    consensus: Optional[Dict[str, Any]] = Field(
        None,
        description="Consensus result: {consensus_diagnosis, mean_confidence, "
        "supporting_specialists, dissenting_specialists}",
    )
    evaluation: Optional[Dict[str, Any]] = Field(
        None,
        description="Evaluation result: {eval_confidence, explanation, "
        "flag_for_review, flag_reason, confirmed_emergency}",
    )
    nba: Optional[Dict[str, Any]] = Field(
        None,
        description="Next Best Action: {route, urgency, reasoning, "
        "self_care_instructions, escalation_criteria}",
    )
    emergency_override: bool = Field(
        default=False,
        description="True if emergency symptoms detected during intake",
    )
    error: Optional[str] = Field(
        None,
        description="Error message if the pipeline failed at this stage",
    )
