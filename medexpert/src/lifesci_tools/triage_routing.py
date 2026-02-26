"""Triage Routing Tool — selects specialists based on symptom keywords.

Loads ``specialist_routing.json`` and matches patient symptoms against
keyword rules.  Always includes Tier 1 (generalists) and selects up to
3 Tier 2 specialists by keyword overlap score.
"""

import json
import logging
from pathlib import Path

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import (
    TRIAGE_MAX_TIER2_SPECIALISTS,
    TRIAGE_TIER1_SPECIALISTS,
)
from lifesci_common.triage_utils import emit_triage_progress

log = logging.getLogger(__name__)

# Cache the routing config after first load
_routing_cache: dict | None = None


def _load_routing_config(config_path: str = "") -> dict:
    """Load specialist_routing.json, with caching."""
    global _routing_cache
    if _routing_cache is not None:
        return _routing_cache

    if not config_path:
        config_path = str(
            Path(__file__).resolve().parents[2] / "configs" / "triage" / "specialist_routing.json"
        )
    path = Path(config_path)
    if not path.exists():
        log.warning("Specialist routing config not found at %s", path)
        return {"routing_rules": [], "max_tier2_specialists": TRIAGE_MAX_TIER2_SPECIALISTS}

    _routing_cache = json.loads(path.read_text())
    return _routing_cache


class TriageRoutingTool(DynamicTool):
    """Routes triage cases to appropriate specialists based on symptom keywords."""

    @property
    def tool_name(self) -> str:
        return "triage_routing"

    @property
    def tool_description(self) -> str:
        return (
            "Selects specialist doctors to consult based on patient symptoms. "
            "Always includes 3 generalists (Family Physician, Internist, "
            "Emergency Medicine). Adds up to 3 additional specialists "
            "matched by symptom keywords. Returns the full specialist list "
            "for the specialist panel stage."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "clinical_note": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="JSON string of the structured clinical note from intake",
                ),
            },
            required=["clinical_note"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        # Parse clinical note
        raw = args.get("clinical_note", "{}")
        try:
            note = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            note = {}

        config_path = (self.tool_config or {}).get("routing_config_path", "")
        config = _load_routing_config(config_path)
        max_tier2 = config.get("max_tier2_specialists", TRIAGE_MAX_TIER2_SPECIALISTS)

        # Build searchable text from clinical note
        search_text = _build_search_text(note).lower()

        # Score Tier 2 specialists by keyword matches
        tier2_scores: list[tuple[str, int]] = []
        for rule in config.get("routing_rules", []):
            specialist = rule.get("specialist", "")
            keywords = rule.get("keywords", [])
            matches = sum(1 for kw in keywords if kw.lower() in search_text)
            if matches >= config.get("match_threshold", 1):
                tier2_scores.append((specialist, matches))

        # Sort by match count (descending), take top N
        tier2_scores.sort(key=lambda x: x[1], reverse=True)
        tier2_selected = [s[0] for s in tier2_scores[:max_tier2]]

        all_specialists = TRIAGE_TIER1_SPECIALISTS + tier2_selected

        # Emit stage 2 progress (cumulative — includes emergency_override if set)
        await emit_triage_progress(
            tool_context,
            stage=2,
            stage_name="ROUTING",
            detail=f"Selected {len(all_specialists)} specialists: "
            + ", ".join(all_specialists),
            log_identifier="[TriageRouting:Progress]",
        )

        return {
            "tier1": TRIAGE_TIER1_SPECIALISTS,
            "tier2": tier2_selected,
            "all_specialists": all_specialists,
            "total": len(all_specialists),
            "tier2_scores": [{"specialist": s, "matches": m} for s, m in tier2_scores[:max_tier2]],
        }

def _clear_routing_cache() -> None:
    """Clear the routing config cache. Used by tests for isolation."""
    global _routing_cache
    _routing_cache = None


def _build_search_text(note: dict) -> str:
    """Flatten clinical note fields into searchable text."""
    parts = [
        note.get("chief_complaint", ""),
        note.get("additional_notes", ""),
        note.get("medical_history", ""),
    ]
    symptoms = note.get("symptoms", [])
    if isinstance(symptoms, list):
        for s in symptoms:
            if isinstance(s, dict):
                parts.append(s.get("symptom", ""))
                parts.append(s.get("location", "") or "")
                parts.append(s.get("aggravating_factors", "") or "")
    return " ".join(str(p) for p in parts if p)
