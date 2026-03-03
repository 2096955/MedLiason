"""Research Question Decomposer — breaks questions into domain-routed sub-questions.

Uses keyword-based heuristics to split compound research questions and
route each sub-question to the appropriate specialist agent.
"""

import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import DOMAIN_AGENT_ROUTING


def _score_domain(text: str, domain_info: dict) -> float:
    """Score how well a text matches a domain based on keyword overlap."""
    text_lower = text.lower()
    keywords = domain_info["keywords"]
    matches = sum(1 for kw in keywords if kw in text_lower)
    return matches / max(len(keywords), 1)


def _route_question(question: str) -> list[dict]:
    """Route a question to primary + secondary specialist agents.

    Returns list of dicts: [{domain, agent, confidence, role}, ...].
    Primary = highest keyword match. Up to 2 secondary agents included
    if they have any keyword matches, ensuring multi-source evidence.
    """
    scored = []
    for domain, info in DOMAIN_AGENT_ROUTING.items():
        score = _score_domain(question, info)
        scored.append((domain, info["agent"], score))

    scored.sort(key=lambda x: x[2], reverse=True)

    results = []

    # Primary: best match, or literature if nothing matched
    if scored[0][2] > 0:
        primary = scored[0]
    else:
        primary = ("literature", "LiteratureSpecialist", 0.04)
    results.append({
        "domain": primary[0],
        "agent": primary[1],
        "confidence": round(min(primary[2] * 5, 1.0), 2),
        "role": "primary",
    })

    # Secondary: up to 2 more with any keyword match
    for domain, agent, score in scored[1:]:
        if score > 0 and agent != results[0]["agent"] and len(results) < 3:
            results.append({
                "domain": domain,
                "agent": agent,
                "confidence": round(min(score * 5, 1.0), 2),
                "role": "secondary",
            })

    # Always include LiteratureSpecialist if not already present
    if not any(r["agent"] == "LiteratureSpecialist" for r in results):
        results.append({
            "domain": "literature",
            "agent": "LiteratureSpecialist",
            "confidence": 0.3,
            "role": "secondary",
        })

    return results


def _split_question(question: str, max_sub: int) -> list[str]:
    """Split a compound question into sub-questions using heuristics.

    Handles conjunctions (and, or, also), semicolons, question marks,
    and numbered lists.
    """
    # Already a simple question
    if len(question) < 50 and "?" in question:
        return [question.strip()]

    sub_questions = []

    # Split on numbered patterns (1. / 1) / a. / a))
    numbered = re.split(r"\d+[.)]\s+|[a-z][.)]\s+", question)
    if len(numbered) > 2:
        sub_questions = [s.strip() for s in numbered if s.strip()]
    else:
        # Split on semicolons
        parts = question.split(";")
        if len(parts) > 1:
            sub_questions = [p.strip() for p in parts if p.strip()]
        else:
            # Split on conjunctions: "and", "also", "as well as", "in addition"
            # Use word boundary after "and" to avoid requiring double whitespace
            conj_pattern = r"\s+(?:and also|and\b|also\b|as well as|in addition to|furthermore|moreover)\s+"
            parts = re.split(conj_pattern, question, flags=re.IGNORECASE)
            if len(parts) > 1:
                sub_questions = [p.strip() for p in parts if p.strip()]
            else:
                # Split on commas between clauses (only if question is long)
                if len(question) > 100:
                    parts = re.split(r",\s+(?:what|how|why|which|where|when|who)\s+", question, flags=re.IGNORECASE)
                    if len(parts) > 1:
                        sub_questions = [p.strip() for p in parts if p.strip()]

    # If no splitting occurred, treat the whole question as one sub-question
    if not sub_questions:
        sub_questions = [question.strip()]

    # Ensure each sub-question ends with a question mark
    cleaned = []
    for sq in sub_questions:
        sq = sq.strip().rstrip(".")
        if not sq.endswith("?"):
            sq += "?"
        cleaned.append(sq)

    return cleaned[:max_sub]


class QueryDecomposerTool(DynamicTool):
    """Decomposes research questions into domain-routed sub-questions."""

    @property
    def tool_name(self) -> str:
        return "query_decomposer"

    @property
    def tool_description(self) -> str:
        return (
            "Breaks down a complex research question into sub-questions, each "
            "routed to a primary specialist plus secondary specialists for "
            "multi-source evidence. Returns sub-questions with domain/agent "
            "routing, secondary agents, and a list of all agents to delegate to."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "question": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The research question to decompose",
                ),
                "max_sub_questions": adk_types.Schema(
                    type=adk_types.Type.INTEGER,
                    description="Maximum number of sub-questions to generate (default 5)",
                    nullable=True,
                ),
            },
            required=["question"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        question = args.get("question", "").strip()
        max_sub = args.get("max_sub_questions", 5)

        if not question:
            return {"error": "Question is required"}

        if max_sub < 1:
            max_sub = 1
        elif max_sub > 10:
            max_sub = 10

        sub_questions_text = _split_question(question, max_sub)

        sub_questions = []
        all_agents: set[str] = set()
        total_confidence = 0.0
        for i, sq in enumerate(sub_questions_text):
            routes = _route_question(sq)
            primary = routes[0]
            secondaries = routes[1:]
            sub_questions.append({
                "question": sq,
                "domain": primary["domain"],
                "target_agent": primary["agent"],
                "secondary_agents": [
                    {"agent": r["agent"], "domain": r["domain"]}
                    for r in secondaries
                ],
                "priority": i + 1,
                "routing_confidence": primary["confidence"],
            })
            total_confidence += primary["confidence"]
            all_agents.add(primary["agent"])
            for r in secondaries:
                all_agents.add(r["agent"])

        avg_confidence = (
            round(total_confidence / len(sub_questions), 2) if sub_questions else 0.0
        )

        return {
            "original_question": question,
            "sub_questions": sub_questions,
            "routing_confidence": avg_confidence,
            "count": len(sub_questions),
            "all_agents": sorted(all_agents),
        }
