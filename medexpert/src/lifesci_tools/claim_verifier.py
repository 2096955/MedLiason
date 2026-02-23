"""Claim Verifier — 3-stage claim-citation alignment analysis.

Stage 1: Structural — citation ID exists in the provided list.
Stage 2: LLM entailment — calls an LLM to classify ENTAILS / CONTRADICTS / NEUTRAL.
Stage 3: Keyword fallback — Jaccard overlap (used when ``use_llm=False`` or LLM fails).

The tool reads ``model``, ``temperature``, and ``use_llm`` from ``tool_config``
(passed via the agent YAML).
"""

import json
import logging
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from litellm import acompletion as _litellm_acompletion

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.observability import log_tokens

logger = logging.getLogger("medexpert.claim_verifier")

# ---------------------------------------------------------------------------
# Text helpers (unchanged)
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords (4+ chars, no stop words)."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "for", "and", "or", "but",
        "in", "on", "at", "to", "from", "of", "with", "by", "as", "if",
        "not", "no", "nor", "so", "yet", "this", "that", "these", "those",
        "what", "which", "who", "whom", "also", "just", "about", "into",
        "more", "most", "than", "very", "been", "being", "other", "some",
        "such", "each", "both",
    }
    words = set(re.findall(r"[a-z]{4,}", text.lower()))
    return words - stop_words


def _extract_claims_with_citations(text: str) -> list[dict]:
    """Extract sentences that contain citation markers [[cite:...]].

    Handles citations placed either inline or after the sentence-ending period,
    e.g. both "claim [[cite:X]]." and "claim. [[cite:X]]".

    Returns list of {claim_text, cited_ids}.
    """
    # Split into raw segments on sentence boundaries
    raw_segments = re.split(r"(?<=[.!?])\s+", text)

    # Re-attach citation-only fragments and leading citations to the preceding sentence
    sentences: list[str] = []
    for seg in raw_segments:
        stripped = re.sub(r"\s*\[\[cite:[^\]]+\]\]", "", seg).strip()
        if not stripped and sentences:
            # Citation-only fragment — merge with previous sentence
            sentences[-1] = sentences[-1] + " " + seg
        elif sentences and seg.startswith("[[cite:"):
            # Segment starts with a citation that belongs to the previous sentence.
            # Split off leading citations and merge them back, keep the rest as new segment.
            leading = []
            remainder = seg
            while remainder.startswith("[[cite:"):
                m = re.match(r"\[\[cite:[^\]]+\]\]\s*", remainder)
                if m:
                    leading.append(m.group(0).rstrip())
                    remainder = remainder[m.end():]
                else:
                    break
            if leading:
                sentences[-1] = sentences[-1] + " " + " ".join(leading)
            if remainder.strip():
                sentences.append(remainder.strip())
        else:
            sentences.append(seg)

    claims = []
    for sentence in sentences:
        cite_ids = re.findall(r"\[\[cite:([^\]]+)\]\]", sentence)
        if cite_ids:
            # Remove citation markers to get pure claim text
            clean_text = re.sub(r"\s*\[\[cite:[^\]]+\]\]", "", sentence).strip()
            if clean_text:
                claims.append({
                    "claim_text": clean_text,
                    "cited_ids": cite_ids,
                })

    return claims


# ---------------------------------------------------------------------------
# LLM entailment helpers
# ---------------------------------------------------------------------------

_ENTAILMENT_PROMPT_TEMPLATE = (
    "You are an entailment classifier for biomedical claims.\n\n"
    "CLAIM: {claim}\n"
    "SOURCE: {source}\n\n"
    "Classify the relationship between the CLAIM and the SOURCE text.\n"
    'Respond with ONLY a JSON object (no markdown fences):\n'
    '{{"entailment": "ENTAILS" | "CONTRADICTS" | "NEUTRAL", '
    '"confidence": <0.0-1.0>, '
    '"evidence_excerpt": "<relevant excerpt from SOURCE>"}}'
)

_VALID_ENTAILMENT_LABELS = {"ENTAILS", "CONTRADICTS", "NEUTRAL"}


async def _llm_entailment(
    claim_text: str,
    source_text: str,
    model: str,
    temperature: float,
    session_id: str,
) -> dict:
    """Call LLM to classify entailment.

    Returns parsed dict with keys: entailment, confidence, evidence_excerpt,
    prompt_tokens, completion_tokens.  Raises on LLM or parse failure.
    """
    # Use % formatting to avoid {/} issues in biomedical text
    prompt = _ENTAILMENT_PROMPT_TEMPLATE.replace("{claim}", claim_text).replace(
        "{source}", source_text
    )
    response = await _litellm_acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=256,
    )
    content = response.choices[0].message.content.strip()

    # Extract token counts
    usage = getattr(response, "usage", None)
    p_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    c_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

    # Log token usage
    if usage:
        log_tokens(
            session_id=session_id,
            model=model,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
        )

    # Strip markdown fences if present
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

    parsed = json.loads(content)
    # Normalise entailment label
    label = str(parsed.get("entailment", "NEUTRAL")).upper()
    if label not in _VALID_ENTAILMENT_LABELS:
        label = "NEUTRAL"
    parsed["entailment"] = label
    parsed["prompt_tokens"] = p_tokens
    parsed["completion_tokens"] = c_tokens
    return parsed


def _entailment_to_score(entailment: str) -> float:
    """Map entailment label to a numeric alignment score."""
    return {"ENTAILS": 1.0, "CONTRADICTS": 0.0, "NEUTRAL": 0.3}.get(
        entailment.upper(), 0.3
    )


# ---------------------------------------------------------------------------
# Keyword fallback (original Jaccard logic)
# ---------------------------------------------------------------------------


def _keyword_verify(claim_keywords: set[str], citation_keywords: set[str]) -> dict:
    """Keyword-overlap verification (Jaccard). Returns per-claim result dict."""
    matched = claim_keywords & citation_keywords
    union = claim_keywords | citation_keywords
    score = len(matched) / len(union) if union else 0.0
    return {
        "alignment_score": round(score, 3),
        "keywords_matched": sorted(list(matched))[:10],
        "supported": score >= 0.15,
        "method": "keyword",
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class ClaimVerifierTool(DynamicTool):
    """Verifies claim-citation alignment using LLM entailment + keyword fallback."""

    def __init__(self, tool_config: Optional[dict] = None, **kwargs):
        super().__init__(tool_config=tool_config, **kwargs)
        cfg = tool_config or {}
        self.model: str = cfg.get("model", "openai/gemini-2.5-flash-001")
        self.temperature: float = float(cfg.get("temperature", 0.0))
        # Explicit bool coercion: YAML string "false" must map to False
        raw_llm = cfg.get("use_llm", True)
        if isinstance(raw_llm, str):
            self.use_llm: bool = raw_llm.lower() not in ("false", "0", "no")
        else:
            self.use_llm = bool(raw_llm)
        self.fallback_model: str = cfg.get("fallback_model", "")

    @property
    def tool_name(self) -> str:
        return "claim_verifier"

    @property
    def tool_description(self) -> str:
        return (
            "Analyzes a response text to verify that claims are properly supported "
            "by their cited sources. Extracts sentences with [[cite:...]] markers, "
            "uses LLM entailment analysis (or keyword fallback) to assess claim-source "
            "alignment, and flags unsupported or misattributed claims."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "response_text": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The full response text containing citation markers [[cite:...]]",
                ),
                "citations_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON string of citations array. Each item: "
                        "{id: str, title: str, snippet: str, url: str}"
                    ),
                ),
            },
            required=["response_text", "citations_json"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        response_text = args.get("response_text", "")
        try:
            citations = json.loads(args.get("citations_json", "[]"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in citations_json"}

        if not response_text:
            return {"error": "response_text is required"}

        # Derive a session id for observability
        session_id = ""
        if hasattr(tool_context, "session") and tool_context.session:
            session_id = getattr(tool_context.session, "id", "")

        # Build citation index
        citation_map: dict[str, dict] = {}
        for c in citations:
            cid = c.get("id", "")
            citation_map[cid] = {
                "title": c.get("title", ""),
                "snippet": c.get("snippet", ""),
                "keywords": _extract_keywords(
                    f"{c.get('title', '')} {c.get('snippet', '')}"
                ),
            }

        # Extract claims
        extracted_claims = _extract_claims_with_citations(response_text)

        verified_claims: list[dict] = []
        unsupported_claims: list[str] = []
        total_score = 0.0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        entailment_counts = {"ENTAILS": 0, "CONTRADICTS": 0, "NEUTRAL": 0}
        method_used = "llm" if self.use_llm else "keyword"

        for claim in extracted_claims:
            claim_keywords = _extract_keywords(claim["claim_text"])

            for cited_id in claim["cited_ids"]:
                citation_info = citation_map.get(cited_id)

                if citation_info is None:
                    # Stage 1: Structural — citation ID not found
                    verified_claims.append({
                        "claim_text": claim["claim_text"],
                        "cited_id": cited_id,
                        "alignment_score": 0.0,
                        "keywords_matched": [],
                        "supported": False,
                        "reason": "Citation ID not found",
                        "method": "structural",
                    })
                    unsupported_claims.append(claim["claim_text"])
                    continue

                source_text = f"{citation_info['title']} {citation_info['snippet']}"

                # Stage 2: LLM entailment (if enabled)
                if self.use_llm:
                    try:
                        ent_result = await _llm_entailment(
                            claim_text=claim["claim_text"],
                            source_text=source_text,
                            model=self.model,
                            temperature=self.temperature,
                            session_id=session_id,
                        )
                        entailment_label = ent_result["entailment"]  # already normalised
                        confidence = float(ent_result.get("confidence", 0.5))
                        evidence_excerpt = ent_result.get("evidence_excerpt", "")
                        score = _entailment_to_score(entailment_label)
                        supported = entailment_label == "ENTAILS" and confidence >= 0.5

                        # Only count valid labels (unknown labels were normalised to NEUTRAL)
                        entailment_counts[entailment_label] = (
                            entailment_counts.get(entailment_label, 0) + 1
                        )

                        # Aggregate token counts from LLM response
                        total_prompt_tokens += ent_result.get("prompt_tokens", 0)
                        total_completion_tokens += ent_result.get("completion_tokens", 0)

                        entry = {
                            "claim_text": claim["claim_text"],
                            "cited_id": cited_id,
                            "alignment_score": round(score, 3),
                            "keywords_matched": sorted(
                                list(claim_keywords & citation_info["keywords"])
                            )[:10],
                            "supported": supported,
                            "entailment": entailment_label,
                            "confidence": round(confidence, 3),
                            "evidence_excerpt": evidence_excerpt,
                            "method": "llm",
                        }
                        verified_claims.append(entry)

                        if not supported:
                            unsupported_claims.append(claim["claim_text"])

                        total_score += score
                        continue

                    except Exception:
                        # Try fallback model before keyword fallback
                        if self.fallback_model:
                            try:
                                logger.info(
                                    "Primary model failed, trying fallback: %s",
                                    self.fallback_model,
                                )
                                ent_result = await _llm_entailment(
                                    claim_text=claim["claim_text"],
                                    source_text=source_text,
                                    model=self.fallback_model,
                                    temperature=self.temperature,
                                    session_id=session_id,
                                )
                                entailment_label = ent_result["entailment"]
                                confidence = float(ent_result.get("confidence", 0.5))
                                evidence_excerpt = ent_result.get("evidence_excerpt", "")
                                score = _entailment_to_score(entailment_label)
                                supported = entailment_label == "ENTAILS" and confidence >= 0.5
                                entailment_counts[entailment_label] = (
                                    entailment_counts.get(entailment_label, 0) + 1
                                )
                                total_prompt_tokens += ent_result.get("prompt_tokens", 0)
                                total_completion_tokens += ent_result.get("completion_tokens", 0)
                                entry = {
                                    "claim_text": claim["claim_text"],
                                    "cited_id": cited_id,
                                    "alignment_score": round(score, 3),
                                    "keywords_matched": sorted(
                                        list(claim_keywords & citation_info["keywords"])
                                    )[:10],
                                    "supported": supported,
                                    "entailment": entailment_label,
                                    "confidence": round(confidence, 3),
                                    "evidence_excerpt": evidence_excerpt,
                                    "method": "llm_fallback",
                                }
                                verified_claims.append(entry)
                                if not supported:
                                    unsupported_claims.append(claim["claim_text"])
                                total_score += score
                                method_used = "mixed"
                                continue
                            except Exception:
                                logger.warning(
                                    "Fallback model also failed, using keyword",
                                    exc_info=True,
                                )
                        logger.warning(
                            "LLM entailment failed for claim, falling back to keyword",
                            exc_info=not self.fallback_model,
                        )
                        method_used = "mixed"
                        # Fall through to keyword

                # Stage 3: Keyword fallback
                kw_result = _keyword_verify(claim_keywords, citation_info["keywords"])
                entry = {
                    "claim_text": claim["claim_text"],
                    "cited_id": cited_id,
                    **kw_result,
                }
                verified_claims.append(entry)

                if not kw_result["supported"]:
                    unsupported_claims.append(claim["claim_text"])

                total_score += kw_result["alignment_score"]

        overall_score = (
            round(total_score / len(verified_claims), 3)
            if verified_claims
            else 0.0
        )

        result = {
            "claims": verified_claims,
            "overall_score": overall_score,
            "unsupported_claims": unsupported_claims,
            "total_claims": len(verified_claims),
            "supported_count": sum(1 for c in verified_claims if c["supported"]),
            "method": method_used,
            "entailment_summary": dict(entailment_counts),
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
        }
        return result
