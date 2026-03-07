"""Completeness Checker — validates evidence coverage against decomposition plan.

Matches collected evidence items to sub-questions from the query
decomposition plan.  When an LLM model is configured (via ``tool_config``),
uses semantic matching to determine whether evidence *meaningfully answers*
each sub-question.  Falls back to keyword overlap when the LLM call fails
or when no model is configured.

Extended metrics: structural_coverage, quality_coverage, domain matching,
and a ``proceed`` flag.
"""

import json as _json
import logging
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from litellm import acompletion as _litellm_acompletion

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.observability import log_tokens
from lifesci_common.llm_utils import json_mode_kwargs
from lifesci_common.triage_utils import extract_json_from_text

logger = logging.getLogger("medexpert.completeness_checker")

# ---------------------------------------------------------------------------
# Keyword helpers (retained as fallback)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# LLM semantic coverage check
# ---------------------------------------------------------------------------

_COVERAGE_PROMPT_TEMPLATE = (
    "You are evaluating research coverage. For each sub-question, determine\n"
    "if the evidence MEANINGFULLY answers it (not just mentions related keywords).\n\n"
    "SUB-QUESTIONS:\n{questions}\n\n"
    "EVIDENCE SUMMARIES:\n{evidence}\n\n"
    "Return ONLY a JSON object (no markdown fences):\n"
    '{{"coverage": [\n'
    '  {{"question": "<exact sub-question text>",\n'
    '    "answered": true | false,\n'
    '    "confidence": <0.0-1.0>,\n'
    '    "matching_evidence_title": "<title of best-matching evidence or null>",\n'
    '    "gap_reason": "<why not answered, or null>"}}\n'
    "]}}"
)


async def _llm_coverage_check(
    sub_questions: list[dict],
    evidence: list[dict],
    model: str,
    temperature: float,
    session_id: str,
    vertex_kwargs: dict | None = None,
) -> list[dict] | None:
    """Use LLM to semantically assess which sub-questions are answered.

    Returns a list of per-question dicts on success, or ``None`` on failure
    (so the caller can fall back to keyword matching).
    """
    # Build compact representations for the prompt
    q_lines = []
    for i, sq in enumerate(sub_questions):
        q_lines.append(f"{i + 1}. [{sq.get('domain', '?')}] {sq.get('question', '')}")

    ev_lines = []
    for i, ev in enumerate(evidence[:15]):
        title = ev.get("title", "Untitled")[:120]
        snippet = (ev.get("snippet", "") or "")[:200]
        ev_lines.append(f"{i + 1}. {title}: {snippet}")

    prompt = (
        _COVERAGE_PROMPT_TEMPLATE
        .replace("{questions}", "\n".join(q_lines))
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
        if not parsed:
            return None
        coverage = parsed.get("coverage", [])
        if not isinstance(coverage, list):
            return None
        return coverage

    except Exception as exc:
        logger.warning(
            "[completeness_checker] LLM coverage check failed, falling back "
            "to keyword matching: %s",
            exc,
        )
        return None


class CompletenessCheckerTool(DynamicTool):
    """Validates evidence coverage against a query decomposition plan."""

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
            # No model configured — will use keyword fallback only
            self.model = ""
            self._vertex_kwargs = {}
        self.temperature: float = float(cfg.get("temperature", 0.2))

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
        try:
            plan = _json.loads(args.get("decomposition_plan_json", "{}"))
        except _json.JSONDecodeError:
            return {"error": "Invalid JSON in decomposition_plan_json"}

        try:
            evidence = _json.loads(args.get("collected_evidence_json", "[]"))
        except _json.JSONDecodeError:
            return {"error": "Invalid JSON in collected_evidence_json"}

        sub_questions = plan.get("sub_questions", [])
        if not sub_questions:
            return {"error": "No sub_questions found in decomposition plan"}

        # ------------------------------------------------------------------
        # Try LLM semantic coverage first (if model configured)
        # ------------------------------------------------------------------
        llm_coverage: list[dict] | None = None
        if self.model and evidence:
            session_id = ""
            if hasattr(tool_context, "session") and tool_context.session:
                session_id = getattr(tool_context.session, "id", "")
            llm_coverage = await _llm_coverage_check(
                sub_questions=sub_questions,
                evidence=evidence,
                model=self.model,
                temperature=self.temperature,
                session_id=session_id,
                vertex_kwargs=self._vertex_kwargs,
            )

        # ------------------------------------------------------------------
        # Build results — use LLM signal when available, keyword fallback otherwise
        # ------------------------------------------------------------------

        # Build evidence keyword index (needed for fallback and grade lookup)
        evidence_keywords = []
        for item in evidence:
            text = f"{item.get('title', '')} {item.get('snippet', '')}"
            evidence_keywords.append({
                "keywords": _extract_keywords(text),
                "item": item,
            })

        # Index LLM results by question text for O(1) lookup
        llm_by_question: dict[str, dict] = {}
        if llm_coverage:
            for entry in llm_coverage:
                q = entry.get("question", "")
                if q:
                    llm_by_question[q] = entry

        answered = []
        gaps = []
        threshold = 0.1  # Minimum keyword overlap to consider "covered"
        domain_match_count = 0
        used_keyword_fallback = False

        for sq in sub_questions:
            sq_text = sq.get("question", "")
            sq_domain = sq.get("domain", "unknown")

            # --- Check LLM result first ---
            llm_entry = llm_by_question.get(sq_text)
            if llm_entry and llm_entry.get("answered"):
                # LLM says answered — find matching evidence for grade check
                matched_title = llm_entry.get("matching_evidence_title", "")
                matched_item = None
                domain_matched = False
                for ev in evidence_keywords:
                    if ev["item"].get("title", "") == matched_title:
                        matched_item = ev["item"]
                        break
                if not matched_item and evidence_keywords:
                    matched_item = evidence_keywords[0]["item"]
                if matched_item:
                    ev_domain = matched_item.get("domain", "")
                    domain_matched = bool(
                        ev_domain and sq_domain and ev_domain.lower() == sq_domain.lower()
                    )

                has_quality_grade = bool(matched_item.get("grade")) if matched_item else False
                answered.append({
                    "question": sq_text,
                    "domain": sq_domain,
                    "coverage_score": round(float(llm_entry.get("confidence", 0.8)), 3),
                    "matched_evidence": matched_title or (matched_item.get("title", "") if matched_item else ""),
                    "has_quality_grade": has_quality_grade,
                    "domain_matched": domain_matched,
                    "method": "llm",
                })
                if domain_matched:
                    domain_match_count += 1
                continue

            if llm_entry and not llm_entry.get("answered"):
                # LLM explicitly says NOT answered — record as gap
                failure_type = self._detect_failure_type(evidence, sq_domain)
                gaps.append({
                    "question": sq_text,
                    "domain": sq_domain,
                    "target_agent": sq.get("target_agent", ""),
                    "priority": sq.get("priority", 99),
                    "failure_type": failure_type,
                    "gap_reason": llm_entry.get("gap_reason"),
                    "method": "llm",
                })
                continue

            # --- Keyword fallback ---
            used_keyword_fallback = True
            sq_keywords = _extract_keywords(sq_text)
            best_overlap = 0.0
            best_match = None
            best_domain_matched = False

            for ev in evidence_keywords:
                overlap = _compute_overlap(sq_keywords, ev["keywords"])
                if not sq_keywords:
                    sq_lower = sq_text.lower()
                    matched = sum(1 for kw in ev["keywords"] if kw in sq_lower)
                    overlap = matched / max(len(ev["keywords"]), 1) if matched else 0.0

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
                answered.append({
                    "question": sq_text,
                    "domain": sq_domain,
                    "coverage_score": round(best_overlap, 3),
                    "matched_evidence": best_match.get("title", ""),
                    "has_quality_grade": has_quality_grade,
                    "domain_matched": best_domain_matched,
                    "method": "keyword",
                })
                if best_domain_matched:
                    domain_match_count += 1
            else:
                failure_type = self._detect_failure_type(evidence, sq_domain)
                gaps.append({
                    "question": sq_text,
                    "domain": sq_domain,
                    "target_agent": sq.get("target_agent", ""),
                    "priority": sq.get("priority", 99),
                    "failure_type": failure_type,
                })

        # Determine overall method used
        if llm_by_question and used_keyword_fallback:
            method = "mixed"
        elif llm_by_question:
            method = "llm"
        else:
            method = "keyword"

        total = len(sub_questions)
        coverage_pct = round(len(answered) / total * 100, 1) if total > 0 else 0.0
        structural_coverage = round(len(answered) / total, 3) if total > 0 else 0.0
        graded_count = sum(1 for a in answered if a.get("has_quality_grade"))
        quality_coverage = round(graded_count / total, 3) if total > 0 else 0.0
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
            "method": method,
        }

    @staticmethod
    def _detect_failure_type(evidence: list[dict], sq_domain: str) -> str:
        """Determine whether a gap is due to MCP error or missing evidence."""
        for ev in evidence:
            ev_domain = ev.get("domain", "")
            if ev_domain and sq_domain and ev_domain.lower() == sq_domain.lower():
                exec_status = ev.get("execution_status", "success")
                ev_failures = ev.get("mcp_failures", [])
                if exec_status in ("failed", "partial") or ev_failures:
                    return "mcp_error"
        return "no_evidence"
