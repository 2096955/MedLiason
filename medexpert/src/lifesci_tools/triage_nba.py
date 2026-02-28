"""Triage Next Best Action (NBA) — deterministic patient routing.

Routes patients to the appropriate level of care based on diagnosis,
confidence, evaluation flags, and emergency status.  Pure decision-rule
logic — no LLM calls.

Also dispatches ``triage_outcome`` to the cold_worker for learning.
"""

import logging
import os

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import TRIAGE_NBA_ROUTES
from lifesci_common.triage_utils import emit_triage_progress

log = logging.getLogger(__name__)

# Life-threatening diagnoses that escalate to hospital/emergency
_LIFE_THREATENING_KEYWORDS = [
    "myocardial infarction", "heart attack", "stroke", "pulmonary embolism",
    "anaphylaxis", "sepsis", "septic shock", "aortic dissection",
    "pneumothorax", "status epilepticus", "meningitis",
    "diabetic ketoacidosis", "dka", "acute abdomen",
]


class TriageNBATool(DynamicTool):
    """Determines the Next Best Action routing for triage results."""

    @property
    def tool_name(self) -> str:
        return "triage_nba"

    @property
    def tool_description(self) -> str:
        return (
            "Determines the Next Best Action (NBA) for the patient based on "
            "the triage evaluation results. Routes to: emergency_services, "
            "hospital, specialist_referral, gp_visit, pharmacist, or self_care. "
            "Pure decision-rule logic — no LLM calls."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "consensus_diagnosis": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The consensus diagnosis from the panel",
                ),
                "mean_confidence": adk_types.Schema(
                    type=adk_types.Type.NUMBER,
                    description="Mean confidence from consensus (0-100)",
                ),
                "eval_confidence": adk_types.Schema(
                    type=adk_types.Type.NUMBER,
                    description="Evaluator confidence (0-100)",
                ),
                "flag_for_review": adk_types.Schema(
                    type=adk_types.Type.BOOLEAN,
                    description="Whether evaluator flagged for review",
                    nullable=True,
                ),
                "flag_reason": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Reason for the flag",
                    nullable=True,
                ),
                "emergency_override": adk_types.Schema(
                    type=adk_types.Type.BOOLEAN,
                    description="Whether emergency was detected during intake",
                    nullable=True,
                ),
                "specialist_type": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Primary specialist type from consensus",
                    nullable=True,
                ),
                "session_id": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Session ID for cold store dispatch",
                    nullable=True,
                ),
            },
            required=["consensus_diagnosis", "mean_confidence", "eval_confidence"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        diagnosis = args.get("consensus_diagnosis", "")
        mean_conf = float(args.get("mean_confidence", 0))
        eval_conf = float(args.get("eval_confidence", 0))
        flag = bool(args.get("flag_for_review", False))
        flag_reason = args.get("flag_reason", "")
        emergency = bool(args.get("emergency_override", False))
        specialist_type = args.get("specialist_type")

        is_life_threatening = _is_life_threatening(diagnosis)

        # Decision rules (deterministic)
        if emergency:
            result = _build_result(
                diagnosis=diagnosis,
                route="emergency_services",
                urgency="immediate",
                reasoning="Emergency symptoms detected — requires immediate emergency intervention.",
            )
        elif flag and is_life_threatening:
            result = _build_result(
                diagnosis=diagnosis,
                route="hospital",
                urgency="immediate",
                reasoning=f"Potentially life-threatening condition flagged for review: {flag_reason}",
            )
        elif flag and mean_conf < 40:
            result = _build_result(
                diagnosis=diagnosis,
                route="hospital",
                urgency="urgent",
                reasoning=f"Low confidence ({mean_conf:.0f}%) with review flag: {flag_reason}",
            )
        elif flag:
            result = _build_result(
                diagnosis=diagnosis,
                route="gp_visit",
                urgency="urgent",
                reasoning=f"Flagged for clinical review: {flag_reason}",
            )
        elif is_life_threatening and eval_conf < 60:
            result = _build_result(
                diagnosis=diagnosis,
                route="hospital",
                urgency="urgent",
                reasoning=f"Potentially life-threatening condition with moderate confidence ({eval_conf:.0f}%).",
            )
        elif diagnosis == "INCONCLUSIVE":
            result = _build_result(
                diagnosis=diagnosis,
                route="gp_visit",
                urgency="urgent",
                reasoning="Specialist panel could not reach consensus. Professional evaluation recommended.",
            )
        elif eval_conf >= 75 and not is_life_threatening and _is_self_care_eligible(diagnosis):
            result = _build_result(
                diagnosis=diagnosis,
                route="self_care",
                urgency="low",
                reasoning=f"High confidence ({eval_conf:.0f}%) for a mild, self-limiting condition.",
                self_care_instructions=(
                    "Rest, monitor symptoms, and use over-the-counter remedies as appropriate. "
                    "Stay hydrated and avoid known triggers."
                ),
                escalation_criteria=(
                    "Seek medical attention if symptoms worsen, persist beyond 1-2 weeks, "
                    "or if new concerning symptoms develop."
                ),
                follow_up_timeframe="See a doctor if not improving within 2 weeks.",
            )
        elif eval_conf >= 70 and _is_otc_eligible(diagnosis):
            result = _build_result(
                diagnosis=diagnosis,
                route="pharmacist",
                urgency="low",
                reasoning=f"Minor condition with high confidence ({eval_conf:.0f}%). OTC treatment likely sufficient.",
            )
        elif specialist_type and eval_conf >= 60:
            result = _build_result(
                diagnosis=diagnosis,
                route="specialist_referral",
                urgency="routine",
                specialist_type=specialist_type,
                reasoning=f"Condition requires specialist management by {specialist_type}.",
                follow_up_timeframe="Book appointment within 2 weeks.",
            )
        elif eval_conf >= 60:
            result = _build_result(
                diagnosis=diagnosis,
                route="gp_visit",
                urgency="routine",
                reasoning=f"Moderate-to-high confidence ({eval_conf:.0f}%). GP evaluation recommended.",
            )
        else:
            result = _build_result(
                diagnosis=diagnosis,
                route="gp_visit",
                urgency="urgent",
                reasoning=f"Low confidence ({eval_conf:.0f}%). Professional evaluation needed.",
            )

        # Emit stage 5 progress (cumulative — includes prior stages)
        await emit_triage_progress(
            tool_context,
            stage=5,
            stage_name="NBA",
            detail=f"Routed to {result['route']} ({result['urgency']})",
            nba=result,
            emergency_override=emergency,
            log_identifier="[TriageNBA:Progress]",
        )

        # Dispatch to cold store for learning
        self._dispatch_triage_outcome(args, result)

        return result

    def _dispatch_triage_outcome(self, args: dict, result: dict) -> None:
        """Enqueue triage outcome to cold_worker for learning."""
        cold_db_path = (self.tool_config or {}).get(
            "cold_db_path", os.environ.get("COLD_DB_PATH", "")
        )
        if not cold_db_path:
            return
        try:
            from lifesci_tools.cold_worker import enqueue_session_flush

            session_id = args.get("session_id", "")
            enqueue_session_flush({
                "type": "triage_outcome",
                "session_id": session_id,
                "chief_complaint": args.get("consensus_diagnosis", ""),
                "consensus_diagnosis": args.get("consensus_diagnosis", ""),
                "mean_confidence": float(args.get("mean_confidence", 0)),
                "nba_route": result.get("route", ""),
                "nba_urgency": result.get("urgency", ""),
                "flag_for_review": int(bool(args.get("flag_for_review", False))),
                "emergency_override": int(bool(args.get("emergency_override", False))),
                "cold_db_path": cold_db_path,
            })
        except Exception:
            log.exception("Failed to dispatch triage outcome to cold store")


def _build_result(
    diagnosis: str,
    route: str,
    urgency: str,
    reasoning: str,
    specialist_type: str | None = None,
    self_care_instructions: str | None = None,
    escalation_criteria: str | None = None,
    follow_up_timeframe: str | None = None,
) -> dict:
    """Build a standardised NBA result dict."""
    route_info = TRIAGE_NBA_ROUTES.get(route, {"color": "gray"})
    return {
        "diagnosis": diagnosis,
        "route": route,
        "urgency": urgency,
        "color": route_info.get("color", "gray"),
        "specialist_type": specialist_type,
        "reasoning": reasoning,
        "self_care_instructions": self_care_instructions,
        "escalation_criteria": escalation_criteria,
        "follow_up_timeframe": follow_up_timeframe,
        "disclaimer": (
            "This is an AI-assisted triage suggestion for educational purposes only. "
            "It does NOT replace professional medical judgment. Always consult a "
            "qualified healthcare professional for medical advice."
        ),
    }


def _is_life_threatening(diagnosis: str) -> bool:
    """Check if the diagnosis is potentially life-threatening."""
    diag_lower = diagnosis.lower()
    return any(kw in diag_lower for kw in _LIFE_THREATENING_KEYWORDS)


_SELF_CARE_ELIGIBLE = [
    "common cold", "viral upper respiratory", "plantar fasciitis",
    "tension headache", "muscle strain", "minor sprain", "sunburn",
    "mild allergic rhinitis", "seasonal allergies",
    "upper respiratory infection", "acute rhinitis",
    "viral rhinitis", "acute nasopharyngitis",
    "influenza-like illness", "acute viral illness",
]


def _is_self_care_eligible(diagnosis: str) -> bool:
    """Check if the diagnosis is eligible for self-care routing."""
    diag_lower = diagnosis.lower()
    return any(cond in diag_lower for cond in _SELF_CARE_ELIGIBLE)


_OTC_ELIGIBLE = [
    "acid reflux", "gerd", "mild eczema", "contact dermatitis",
    "hemorrhoids", "athlete's foot", "urticaria", "hives",
    "mild conjunctivitis", "heartburn",
    "allergic rhinitis", "viral rhinitis",
    "allergic conjunctivitis",  # excludes bacterial conjunctivitis
]


def _is_otc_eligible(diagnosis: str) -> bool:
    """Check if the diagnosis is eligible for pharmacist/OTC routing."""
    diag_lower = diagnosis.lower()
    return any(cond in diag_lower for cond in _OTC_ELIGIBLE)
