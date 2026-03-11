"""Research Question Decomposer — breaks questions into domain-routed sub-questions.

Uses keyword-based heuristics to split compound research questions and
route each sub-question to the appropriate specialist agent.

Also stores the selected agent list in session state (``_selected_agents``)
so that the protocol_step_validator callback can restrict DELEGATE-step
peer tools to only those agents the decomposer chose.
"""

import logging
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import DOMAIN_AGENT_ROUTING

log = logging.getLogger(__name__)

# Session state key read by protocol_step_validator to restrict peer tools
_SELECTED_AGENTS_KEY = "_selected_agents"

# Minimum character length for each fragment produced by comma-clause splitting.
# Prevents splitting relative/dependent clauses that happen to contain a
# question word (e.g., "for a patient with X, which plan …").
_MIN_COMMA_SPLIT_FRAGMENT = 30

# Context-setting clause openers.  When the first fragment starts with one of
# these AND contains no question word of its own, the comma introduces a
# *dependent* clause — not a second topic — so we must not split.
_CONTEXT_OPENERS = (
    "when ",
    "for ",
    "in ",
    "given ",
    "if ",
    "regarding ",
    "considering ",
)


def _score_domain(text: str, domain_info: dict) -> float:
    """Score how well a text matches a domain based on keyword overlap.

    Short keywords (<=3 chars like 'rna', 'dna', 'snp') use word-boundary
    matching to prevent false positives from substrings (e.g. 'alternatives'
    matching 'rna'). Longer keywords use simple substring matching.
    """
    text_lower = text.lower()
    keywords = domain_info["keywords"]
    matches = 0
    for kw in keywords:
        if len(kw) <= 3:
            # Word-boundary match for short keywords
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                matches += 1
        else:
            if kw in text_lower:
                matches += 1
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

    # Debug: log all domain scores for diagnosis
    if log.isEnabledFor(logging.DEBUG):
        top_scores = [(d, round(s, 4)) for d, _, s in scored[:5]]
        log.debug("query_decomposer: domain scores for %r: %s", question[:80], top_scores)

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

    # Heuristic: if query contains capitalized words that look like drug
    # brand names (not common English words), include DrugSpecialist
    import re
    words = re.findall(r"[A-Z][a-z]{2,}", question)
    # Filter out common non-drug words
    common = {"What", "When", "How", "Can", "Are", "The", "This", "That",
              "Which", "Where", "Why", "Who", "Please", "Tell", "Not"}
    potential_brands = [w for w in words if w not in common]
    if potential_brands and not any(r["agent"] == "DrugSpecialist" for r in results):
        log.info("query_decomposer: brand-name heuristic fired for %s → adding DrugSpecialist", potential_brands)
        results.append({
            "domain": "drugs",
            "agent": "DrugSpecialist",
            "confidence": 0.4,
            "role": "secondary",
        })

    log.info(
        "query_decomposer: routed %r → %s",
        question[:60],
        [(r["agent"], r["role"], r["confidence"]) for r in results],
    )
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
                # Split on commas followed by a question word (what/how/…).
                # Guards:
                #  1. Length gate — only attempt on questions > 100 chars.
                #  2. Fragment size — every fragment must be >= _MIN_COMMA_SPLIT_FRAGMENT.
                #  3. Context-clause — if the first fragment is a context-setting
                #     clause (starts with When/For/In/… and contains no question
                #     word), the comma introduces a dependent clause, not a
                #     second topic.
                if len(question) > 100:
                    parts = re.split(
                        r",\s+(?:what|how|why|which|where|when|who)\s+",
                        question,
                        flags=re.IGNORECASE,
                    )
                    if len(parts) > 1 and all(
                        len(p.strip()) >= _MIN_COMMA_SPLIT_FRAGMENT for p in parts
                    ):
                        first = parts[0].strip().lower()
                        # Identify which opener (if any) the fragment starts with
                        matched_opener = next(
                            (op for op in _CONTEXT_OPENERS if first.startswith(op)),
                            None,
                        )
                        if matched_opener is not None:
                            # Strip the opener itself before searching for
                            # question words — "When considering X" uses
                            # "when" as a temporal conjunction, not an
                            # interrogative.
                            remainder = first[len(matched_opener):]
                            has_question_word = bool(
                                re.search(
                                    r"\b(?:what|how|why|which|where|when|who)\b",
                                    remainder,
                                )
                            )
                            is_context_clause = not has_question_word
                        else:
                            # First fragment doesn't start with a context
                            # opener — treat as a genuine question topic.
                            is_context_clause = False

                        if not is_context_clause:
                            sub_questions = [
                                p.strip() for p in parts if p.strip()
                            ]

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

        sorted_agents = sorted(all_agents)

        # ── Store selected agents in session state for protocol validator ──
        # The protocol_step_validator callback reads _selected_agents to
        # restrict DELEGATE-step peer tools to only those the decomposer
        # selected, reducing tool-selection noise from 10 peer tools to 3-5.
        # Uses the same _invocation_context.session.state pattern as
        # memory_plane.py (lines 507-512).
        try:
            inv = getattr(tool_context, "_invocation_context", None)
            session_obj = getattr(inv, "session", None) if inv else None
            if session_obj and hasattr(session_obj, "state"):
                session_obj.state[_SELECTED_AGENTS_KEY] = sorted_agents
                log.info(
                    "[QueryDecomposer] Stored _selected_agents in session state: %s",
                    sorted_agents,
                )
        except Exception:
            # Don't break decomposition if session state is unavailable
            log.debug(
                "[QueryDecomposer] Could not store _selected_agents in session state"
            )

        return {
            "original_question": question,
            "sub_questions": sub_questions,
            "routing_confidence": avg_confidence,
            "count": len(sub_questions),
            "all_agents": sorted_agents,
        }
