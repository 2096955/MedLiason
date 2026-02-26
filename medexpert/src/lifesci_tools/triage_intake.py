"""Triage Intake Tool — validates and structures clinical intake data.

Processes patient symptom data collected by the TriageIntakeAgent's
multi-turn conversation, detects emergency conditions, and produces
a structured clinical note JSON.

Returns a handoff signal:
- ``status="complete"`` — intake is done, delegate to orchestrator
- ``status="needs_more_info"`` — ask patient about ``missing_fields``
- ``status="emergency"`` — immediate emergency detected, delegate with flag
"""

import json
import logging

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import TRIAGE_EMERGENCY_KEYWORDS
from lifesci_common.triage_utils import emit_triage_progress

log = logging.getLogger(__name__)


class TriageIntakeTool(DynamicTool):
    """Validates intake data, detects emergencies, produces structured clinical note."""

    @property
    def tool_name(self) -> str:
        return "triage_intake"

    @property
    def tool_description(self) -> str:
        return (
            "Validates and structures clinical intake data from the patient conversation. "
            "Detects emergency conditions requiring immediate override. "
            "Returns status='complete' when intake is sufficient (chief complaint + "
            "at least 2 symptoms with duration/severity), status='needs_more_info' "
            "with a list of missing fields, or status='emergency' when emergency "
            "keywords are detected. Also emits triage progress via SSE."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "chief_complaint": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The patient's main reason for seeking help",
                ),
                "symptoms": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON array of symptom objects: [{symptom, duration, severity, ...}]",
                ),
                "medical_history": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Known medical conditions",
                    nullable=True,
                ),
                "family_history": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Family medical history",
                    nullable=True,
                ),
                "medications": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Current medications",
                    nullable=True,
                ),
                "allergies": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Known allergies",
                    nullable=True,
                ),
                "lifestyle_factors": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Relevant lifestyle factors",
                    nullable=True,
                ),
                "additional_notes": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Any additional clinical notes",
                    nullable=True,
                ),
            },
            required=["chief_complaint"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        chief_complaint = (args.get("chief_complaint") or "").strip()
        if not chief_complaint:
            return {
                "status": "needs_more_info",
                "missing_fields": ["chief_complaint"],
                "message": "Chief complaint is required to begin triage.",
            }

        # Parse symptoms
        symptoms_raw = args.get("symptoms", "[]")
        try:
            symptoms = json.loads(symptoms_raw) if isinstance(symptoms_raw, str) else symptoms_raw
            if not isinstance(symptoms, list):
                symptoms = []
        except (json.JSONDecodeError, TypeError):
            symptoms = []

        # Emergency detection — scan all text fields
        all_text = " ".join(
            str(v).lower()
            for v in [chief_complaint, symptoms_raw, args.get("additional_notes", "")]
            if v
        )
        emergency_type = _detect_emergency(all_text)

        # Emit stage 1 progress via SSE
        await emit_triage_progress(
            tool_context,
            stage=1,
            stage_name="INTAKE",
            detail="Emergency detected — initiating fast-path" if emergency_type
            else "Gathering patient information",
            emergency_override=bool(emergency_type),
            log_identifier="[TriageIntake:Progress]",
        )

        # Build structured clinical note
        clinical_note = {
            "chief_complaint": chief_complaint,
            "symptoms": symptoms,
            "medical_history": args.get("medical_history") or None,
            "family_history": args.get("family_history") or None,
            "medications": args.get("medications") or None,
            "allergies": args.get("allergies") or None,
            "lifestyle_factors": args.get("lifestyle_factors") or None,
            "additional_notes": args.get("additional_notes") or None,
            "emergency_flag": bool(emergency_type),
        }

        # Emergency path
        if emergency_type:
            return {
                "status": "emergency",
                "clinical_note": clinical_note,
                "emergency_type": emergency_type,
                "message": (
                    "Based on what you've described, this may require immediate "
                    "emergency attention. Please call emergency services "
                    "(911/999/112) or go to your nearest emergency department."
                ),
            }

        # Check completeness — need chief complaint + at least 2 symptoms with detail
        detailed_symptoms = [
            s for s in symptoms
            if isinstance(s, dict) and s.get("symptom")
            and (s.get("duration") or s.get("severity"))
        ]

        if len(detailed_symptoms) < 2:
            missing = []
            if len(symptoms) < 2:
                missing.append("at least 2 symptoms")
            for s in symptoms:
                if isinstance(s, dict):
                    if not s.get("duration"):
                        missing.append(f"duration for '{s.get('symptom', 'symptom')}'")
                    if not s.get("severity"):
                        missing.append(f"severity for '{s.get('symptom', 'symptom')}'")
            return {
                "status": "needs_more_info",
                "clinical_note": clinical_note,
                "missing_fields": missing[:3],
                "message": "Need more symptom details before routing to specialists.",
            }

        return {
            "status": "complete",
            "clinical_note": clinical_note,
            "symptom_count": len(detailed_symptoms),
        }

def _detect_emergency(text: str) -> str | None:
    """Scan text for emergency keywords. Returns the matched keyword or None."""
    text_lower = text.lower()
    for keyword in TRIAGE_EMERGENCY_KEYWORDS:
        if keyword in text_lower:
            return keyword
    return None
