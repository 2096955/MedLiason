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


def _route_question(question: str) -> tuple[str, str, float]:
    """Route a question to the best-matching domain and agent.

    Returns (domain, agent_name, confidence).
    """
    best_domain = "literature"
    best_agent = "LiteratureSpecialist"
    best_score = 0.0

    for domain, info in DOMAIN_AGENT_ROUTING.items():
        score = _score_domain(question, info)
        if score > best_score:
            best_score = score
            best_domain = domain
            best_agent = info["agent"]

    # Confidence is 0-1 based on keyword match density
    confidence = min(best_score * 5, 1.0)  # Scale up since individual scores are low

    # If no keywords matched at all, default to literature with low confidence
    if best_score == 0:
        return "literature", "LiteratureSpecialist", 0.2

    return best_domain, best_agent, round(confidence, 2)


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
            "routed to the appropriate specialist agent based on domain keywords. "
            "Returns the original question, sub-questions with domain/agent routing, "
            "and an overall routing confidence score."
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
        total_confidence = 0.0
        for i, sq in enumerate(sub_questions_text):
            domain, agent, confidence = _route_question(sq)
            sub_questions.append({
                "question": sq,
                "domain": domain,
                "target_agent": agent,
                "priority": i + 1,
                "routing_confidence": confidence,
            })
            total_confidence += confidence

        avg_confidence = (
            round(total_confidence / len(sub_questions), 2) if sub_questions else 0.0
        )

        return {
            "original_question": question,
            "sub_questions": sub_questions,
            "routing_confidence": avg_confidence,
            "count": len(sub_questions),
        }
