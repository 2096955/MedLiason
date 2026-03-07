"""Reflection Analyzer — multi-hop gap analysis for evidence sets.

Identifies logical gaps, contradictions, and missing perspectives
across collected evidence items.  When an LLM model is configured
(via ``tool_config``), uses the LLM for nuanced contradiction and gap
detection (magnitude differences, population differences, temporal
changes).  Falls back to keyword heuristics when the LLM fails or when
no model is configured.
"""

import json as _json
import logging
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from litellm import acompletion as _litellm_acompletion

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import DOMAIN_AGENT_ROUTING
from lifesci_common.observability import log_tokens
from lifesci_common.llm_utils import json_mode_kwargs
from lifesci_common.triage_utils import extract_json_from_text

logger = logging.getLogger("medexpert.reflection_analyzer")

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


# ---------------------------------------------------------------------------
# LLM reflection analysis
# ---------------------------------------------------------------------------

_REFLECTION_PROMPT_TEMPLATE = (
    "Analyze the following evidence for a medical research question.\n"
    "Focus on intellectual honesty — what do we NOT know?\n\n"
    "QUESTION: {question}\n\n"
    "EVIDENCE:\n{evidence}\n\n"
    "Return ONLY a JSON object (no markdown fences):\n"
    '{{"contradictions": [\n'
    '  {{"claim_a": "<text>", "source_a": "<title>",\n'
    '    "claim_b": "<text>", "source_b": "<title>",\n'
    '    "nature": "direct_conflict" | "magnitude_difference" | "population_difference" | "temporal_change",\n'
    '    "significance": "high" | "medium" | "low",\n'
    '    "explanation": "<why they conflict>"}}\n'
    '],\n'
    '"gaps": [\n'
    '  {{"description": "<what is missing>",\n'
    '    "importance": "critical" | "important" | "minor",\n'
    '    "what_would_fill_it": "<what evidence is needed>"}}\n'
    '],\n'
    '"limitations": ["<study-level limitation>"],\n'
    '"confidence_statement": "<honest assessment of overall confidence>",\n'
    '"strongest_evidence": "<what we are most sure about>",\n'
    '"weakest_evidence": "<what we are least sure about>"\n'
    "}}"
)


async def _llm_reflection(
    question: str,
    evidence: list[dict],
    model: str,
    temperature: float,
    session_id: str,
    vertex_kwargs: dict | None = None,
) -> dict | None:
    """Use LLM for nuanced contradiction and gap detection.

    Returns parsed dict on success, or ``None`` on failure (caller falls
    back to keyword heuristics).
    """
    ev_lines = []
    for i, ev in enumerate(evidence[:20]):
        title = ev.get("title", "Untitled")[:120]
        snippet = (ev.get("snippet", "") or "")[:300]
        ev_lines.append(f"{i + 1}. {title}: {snippet}")

    prompt = (
        _REFLECTION_PROMPT_TEMPLATE
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
            "[reflection_analyzer] LLM reflection failed, falling back "
            "to keyword heuristics: %s",
            exc,
        )
        return None


class ReflectionAnalyzerTool(DynamicTool):
    """Multi-hop gap analysis for collected evidence sets."""

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
        self.temperature: float = float(cfg.get("temperature", 0.2))

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
        question = args.get("question", "").strip()
        depth = args.get("analysis_depth", "deep").lower()

        if not question:
            return {"error": "Question is required"}

        try:
            evidence = _json.loads(args.get("evidence_json", "[]"))
        except _json.JSONDecodeError:
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

        # ------------------------------------------------------------------
        # Try LLM reflection (if model configured and deep analysis)
        # ------------------------------------------------------------------
        llm_result: dict | None = None
        if self.model and depth == "deep":
            session_id = ""
            if hasattr(tool_context, "session") and tool_context.session:
                session_id = getattr(tool_context.session, "id", "")
            llm_result = await _llm_reflection(
                question=question,
                evidence=evidence,
                model=self.model,
                temperature=self.temperature,
                session_id=session_id,
                vertex_kwargs=self._vertex_kwargs,
            )

        if llm_result:
            # Use LLM results, enrich with keyword heuristic data
            llm_contradictions = llm_result.get("contradictions", [])
            llm_gaps_raw = llm_result.get("gaps", [])
            llm_gaps = [
                g.get("description", str(g)) if isinstance(g, dict) else str(g)
                for g in llm_gaps_raw
            ]
            # Merge keyword-detected gaps that LLM might have missed
            keyword_gaps = _identify_gaps(question, evidence)
            for kg in keyword_gaps:
                if not any(kg.lower() in g.lower() for g in llm_gaps):
                    llm_gaps.append(kg)

            missing_perspectives = _find_missing_perspectives(evidence)
            # Adapt LLM contradictions to the shape _suggest_follow_ups expects
            compat_contradictions = []
            for c in llm_contradictions:
                compat_contradictions.append({
                    "evidence_a": c.get("source_a", ""),
                    "evidence_b": c.get("source_b", ""),
                    "signal": c.get("nature", c.get("explanation", "")),
                })
            follow_ups = _suggest_follow_ups(llm_gaps, missing_perspectives, compat_contradictions)

            return {
                "gaps": llm_gaps,
                "contradictions": llm_contradictions,
                "missing_perspectives": missing_perspectives,
                "follow_up_queries": follow_ups,
                "evidence_count": len(evidence),
                "analysis_depth": depth,
                "method": "llm",
                "confidence_statement": llm_result.get("confidence_statement", ""),
                "strongest_evidence": llm_result.get("strongest_evidence", ""),
                "weakest_evidence": llm_result.get("weakest_evidence", ""),
                "limitations": llm_result.get("limitations", []),
            }

        # ------------------------------------------------------------------
        # Keyword fallback
        # ------------------------------------------------------------------
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
            "method": "keyword",
        }
