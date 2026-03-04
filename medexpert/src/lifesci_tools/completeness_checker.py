"""Completeness Checker — validates evidence coverage against decomposition plan.

Matches collected evidence items to sub-questions from the query
decomposition plan using keyword overlap to identify gaps.

Extended metrics: structural_coverage, quality_coverage, domain matching,
and a ``proceed`` flag.
"""

import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text, ignoring stop words."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "for", "and", "or", "but",
        "in", "on", "at", "to", "from", "of", "with", "by", "as", "if",
        "not", "no", "nor", "so", "yet", "both", "each", "few", "more",
        "most", "other", "some", "such", "than", "too", "very", "what",
        "which", "who", "whom", "this", "that", "these", "those", "it",
    }
    words = set(re.findall(r"[a-z]{3,}", text.lower()))
    return words - stop_words


def _compute_overlap(keywords_a: set[str], keywords_b: set[str]) -> float:
    """Compute Jaccard-like overlap between two keyword sets."""
    if not keywords_a or not keywords_b:
        return 0.0
    intersection = keywords_a & keywords_b
    union = keywords_a | keywords_b
    return len(intersection) / len(union) if union else 0.0


class CompletenessCheckerTool(DynamicTool):
    """Validates evidence coverage against a query decomposition plan."""

    @property
    def tool_name(self) -> str:
        return "completeness_checker"

    @property
    def tool_description(self) -> str:
        return (
            "Checks whether collected evidence fully covers all sub-questions from "
            "the decomposition plan. Returns coverage percentage, answered questions, "
            "gaps, and suggestions for filling those gaps.\n\n"
            "Returns proceed=true when structural_coverage >= 0.70 AND "
            "quality_coverage >= 0.40. If proceed=false, one targeted follow-up "
            "delegation is recommended before synthesis."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "decomposition_plan_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON string of the decomposition plan with sub_questions array. "
                        "Each sub-question has: question, domain, target_agent, priority."
                    ),
                ),
                "collected_evidence_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON string of collected evidence items array. "
                        "Each item has: source, title, snippet, and optionally domain."
                    ),
                ),
            },
            required=["decomposition_plan_json", "collected_evidence_json"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        import json

        try:
            plan = json.loads(args.get("decomposition_plan_json", "{}"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in decomposition_plan_json"}

        try:
            evidence = json.loads(args.get("collected_evidence_json", "[]"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in collected_evidence_json"}

        sub_questions = plan.get("sub_questions", [])
        if not sub_questions:
            return {"error": "No sub_questions found in decomposition plan"}

        # Build evidence keyword index
        evidence_keywords = []
        for item in evidence:
            text = f"{item.get('title', '')} {item.get('snippet', '')}"
            evidence_keywords.append({
                "keywords": _extract_keywords(text),
                "item": item,
            })

        answered = []
        gaps = []
        threshold = 0.1  # Minimum overlap to consider "covered"
        domain_match_count = 0

        for sq in sub_questions:
            sq_text = sq.get("question", "")
            sq_keywords = _extract_keywords(sq_text)
            sq_domain = sq.get("domain", "unknown")
            best_overlap = 0.0
            best_match = None
            best_domain_matched = False

            for ev in evidence_keywords:
                overlap = _compute_overlap(sq_keywords, ev["keywords"])
                # Fallback: if keyword extraction yielded too few terms,
                # check if any evidence keywords appear as substrings in the question
                if not sq_keywords:
                    sq_lower = sq_text.lower()
                    matched = sum(1 for kw in ev["keywords"] if kw in sq_lower)
                    overlap = matched / max(len(ev["keywords"]), 1) if matched else 0.0

                # Domain boost: if sub-question domain matches evidence domain
                ev_domain = ev["item"].get("domain", "")
                domain_matched = bool(
                    ev_domain and sq_domain and ev_domain.lower() == sq_domain.lower()
                )
                boosted_overlap = min(overlap + 0.3, 1.0) if domain_matched else overlap

                if boosted_overlap > best_overlap:
                    best_overlap = boosted_overlap
                    best_match = ev["item"]
                    best_domain_matched = domain_matched

            if best_overlap >= threshold and best_match:
                has_quality_grade = bool(best_match.get("grade"))
                entry = {
                    "question": sq["question"],
                    "domain": sq_domain,
                    "coverage_score": round(best_overlap, 3),
                    "matched_evidence": best_match.get("title", ""),
                    "has_quality_grade": has_quality_grade,
                    "domain_matched": best_domain_matched,
                }
                answered.append(entry)
                if best_domain_matched:
                    domain_match_count += 1
            else:
                # Determine failure type from evidence metadata.
                # If collected evidence for this sub-question's domain contains
                # execution_status="failed" or mcp_failures, it's an MCP error,
                # not a lack of evidence.
                failure_type = "no_evidence"  # default
                for ev in evidence:
                    ev_domain = ev.get("domain", "")
                    if ev_domain and sq_domain and ev_domain.lower() == sq_domain.lower():
                        exec_status = ev.get("execution_status", "success")
                        ev_failures = ev.get("mcp_failures", [])
                        if exec_status in ("failed", "partial") or ev_failures:
                            failure_type = "mcp_error"
                            break

                gaps.append({
                    "question": sq["question"],
                    "domain": sq_domain,
                    "target_agent": sq.get("target_agent", ""),
                    "priority": sq.get("priority", 99),
                    "failure_type": failure_type,
                })

        total = len(sub_questions)
        coverage_pct = round(len(answered) / total * 100, 1) if total > 0 else 0.0

        # Structural coverage: fraction of sub-questions with any evidence match
        structural_coverage = round(len(answered) / total, 3) if total > 0 else 0.0

        # Quality coverage: fraction of answered questions that have a quality grade
        graded_count = sum(1 for a in answered if a.get("has_quality_grade"))
        quality_coverage = (
            round(graded_count / total, 3) if total > 0 else 0.0
        )

        # Proceed flag: structural >= 0.70 AND quality >= 0.40
        proceed = structural_coverage >= 0.70 and quality_coverage >= 0.40

        suggestions = []
        for gap in gaps:
            suggestions.append(
                f"Query {gap['target_agent']} about: {gap['question']}"
            )

        return {
            "complete": len(gaps) == 0,
            "coverage_pct": coverage_pct,
            "total_questions": total,
            "answered": answered,
            "gaps": gaps,
            "suggestions": suggestions,
            "structural_coverage": structural_coverage,
            "quality_coverage": quality_coverage,
            "proceed": proceed,
            "domain_match_count": domain_match_count,
        }
