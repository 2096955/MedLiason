"""Reflection Analyzer — multi-hop gap analysis for evidence sets.

Identifies logical gaps, contradictions, and missing perspectives
across collected evidence items. Suggests follow-up queries to
different specialist agents (IQVIA Med-R1 step 5 pattern).
"""

import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import DOMAIN_AGENT_ROUTING

# Contradiction signal words (one term affirms, the other negates)
CONTRADICTION_PAIRS = [
    ("effective", "ineffective"),
    ("safe", "unsafe"),
    ("beneficial", "harmful"),
    ("significant", "insignificant"),
    ("increase", "decrease"),
    ("improve", "worsen"),
    ("positive", "negative"),
    ("recommended", "not recommended"),
    ("associated", "not associated"),
]

# Perspective categories to check coverage
PERSPECTIVE_CATEGORIES = [
    "clinical_efficacy",
    "safety_profile",
    "cost_effectiveness",
    "patient_outcomes",
    "mechanism_of_action",
    "population_subgroups",
    "long_term_effects",
    "guideline_recommendations",
]

PERSPECTIVE_KEYWORDS = {
    "clinical_efficacy": ["efficacy", "effective", "outcome", "response", "benefit", "improvement"],
    "safety_profile": ["safety", "adverse", "side effect", "toxicity", "risk", "harm"],
    "cost_effectiveness": ["cost", "economic", "value", "affordable", "expensive", "price"],
    "patient_outcomes": ["patient", "quality of life", "survival", "mortality", "morbidity"],
    "mechanism_of_action": ["mechanism", "pathway", "receptor", "target", "binding", "inhibit"],
    "population_subgroups": ["subgroup", "elderly", "pediatric", "pregnant", "comorbid", "race"],
    "long_term_effects": ["long-term", "chronic", "sustained", "years", "follow-up", "durability"],
    "guideline_recommendations": ["guideline", "recommendation", "consensus", "standard of care"],
}


def _find_contradictions(evidence: list[dict]) -> list[dict]:
    """Identify contradictory findings between evidence items."""
    contradictions = []
    for i, ev_a in enumerate(evidence):
        text_a = f"{ev_a.get('title', '')} {ev_a.get('snippet', '')}".lower()
        for j, ev_b in enumerate(evidence):
            if j <= i:
                continue
            text_b = f"{ev_b.get('title', '')} {ev_b.get('snippet', '')}".lower()
            for pos, neg in CONTRADICTION_PAIRS:
                if (pos in text_a and neg in text_b) or (neg in text_a and pos in text_b):
                    contradictions.append({
                        "evidence_a": ev_a.get("title", f"item_{i}"),
                        "evidence_b": ev_b.get("title", f"item_{j}"),
                        "signal": f"{pos} vs {neg}",
                    })
                    break  # One contradiction per pair is enough
    return contradictions


def _find_missing_perspectives(evidence: list[dict]) -> list[str]:
    """Identify which analytical perspectives are missing from evidence."""
    all_text = " ".join(
        f"{e.get('title', '')} {e.get('snippet', '')}".lower() for e in evidence
    )
    missing = []
    for category, keywords in PERSPECTIVE_KEYWORDS.items():
        if not any(kw in all_text for kw in keywords):
            missing.append(category)
    return missing


def _identify_gaps(question: str, evidence: list[dict]) -> list[str]:
    """Identify logical gaps between the question and collected evidence."""
    gaps = []
    q_lower = question.lower()

    # Check if evidence covers the question's key concepts
    q_words = set(re.findall(r"[a-z]{4,}", q_lower))
    evidence_text = " ".join(
        f"{e.get('title', '')} {e.get('snippet', '')}".lower() for e in evidence
    )
    evidence_words = set(re.findall(r"[a-z]{4,}", evidence_text))

    uncovered = q_words - evidence_words
    # Filter to meaningful uncovered words
    stop_additions = {"what", "does", "about", "there", "their", "where", "when", "have", "been"}
    uncovered -= stop_additions

    if uncovered and len(uncovered) > len(q_words) * 0.3:
        gaps.append(
            f"Key question concepts not well covered in evidence: {', '.join(sorted(list(uncovered)[:5]))}"
        )

    if len(evidence) < 3:
        gaps.append("Limited evidence base — fewer than 3 sources collected")

    # Check for domain diversity
    domains = {e.get("source", e.get("domain", "unknown")) for e in evidence}
    if len(domains) <= 1 and len(evidence) > 1:
        gaps.append("All evidence from a single source/domain — limited diversity")

    return gaps


def _suggest_follow_ups(
    gaps: list[str],
    missing_perspectives: list[str],
    contradictions: list[dict],
) -> list[dict]:
    """Generate follow-up queries based on identified issues."""
    follow_ups = []

    for perspective in missing_perspectives[:3]:
        # Find the best agent for this perspective
        agent = "LiteratureSpecialist"
        if perspective in ("safety_profile",):
            agent = "DrugSpecialist"
        elif perspective in ("cost_effectiveness",):
            agent = "ProviderIntelSpecialist"
        elif perspective in ("guideline_recommendations",):
            agent = "LiteratureSpecialist"
        elif perspective in ("population_subgroups",):
            agent = "EpidemiologySpecialist"

        follow_ups.append({
            "query": f"Provide evidence on {perspective.replace('_', ' ')}",
            "target_agent": agent,
            "reason": f"Missing perspective: {perspective}",
        })

    for contradiction in contradictions[:2]:
        follow_ups.append({
            "query": f"Clarify the conflicting findings regarding {contradiction['signal']}",
            "target_agent": "LiteratureSpecialist",
            "reason": f"Contradiction between: {contradiction['evidence_a']} and {contradiction['evidence_b']}",
        })

    return follow_ups


class ReflectionAnalyzerTool(DynamicTool):
    """Multi-hop gap analysis for collected evidence sets."""

    @property
    def tool_name(self) -> str:
        return "reflection_analyzer"

    @property
    def tool_description(self) -> str:
        return (
            "Analyzes collected evidence for logical gaps, contradictions, and "
            "missing perspectives. Suggests follow-up queries to specialist agents. "
            "Supports shallow (quick scan) and deep (thorough) analysis depths."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "question": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The original research question being investigated",
                ),
                "evidence_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON string of evidence items array. Each item should have: "
                        "title, snippet, and optionally source/domain."
                    ),
                ),
                "analysis_depth": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Analysis depth: 'shallow' (quick scan) or 'deep' (thorough). Default: deep",
                    nullable=True,
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
        import json

        question = args.get("question", "").strip()
        depth = args.get("analysis_depth", "deep").lower()

        if not question:
            return {"error": "Question is required"}

        try:
            evidence = json.loads(args.get("evidence_json", "[]"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in evidence_json"}

        if not isinstance(evidence, list):
            return {"error": "evidence_json must be a JSON array"}

        if not evidence:
            return {
                "gaps": ["No evidence collected — evidence list is empty"],
                "contradictions": [],
                "missing_perspectives": list(PERSPECTIVE_CATEGORIES),
                "follow_up_queries": [],
                "evidence_count": 0,
                "analysis_depth": depth,
                "status": "no_evidence",
            }

        gaps = _identify_gaps(question, evidence)
        contradictions = _find_contradictions(evidence) if depth == "deep" else []
        missing_perspectives = _find_missing_perspectives(evidence)
        follow_ups = _suggest_follow_ups(gaps, missing_perspectives, contradictions)

        return {
            "gaps": gaps,
            "contradictions": contradictions,
            "missing_perspectives": missing_perspectives,
            "follow_up_queries": follow_ups,
            "evidence_count": len(evidence),
            "analysis_depth": depth,
        }
