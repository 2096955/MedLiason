"""Deliberation Synthesizer — advisory board consensus analysis.

Analyzes perspectives from multiple advisory board personas.  When an LLM
model is configured (via ``tool_config``), uses the LLM for genuine
multi-perspective synthesis — identifying real disagreements, assessing
which positions have stronger evidence, and flagging blind spots.  Falls
back to keyword-based theme analysis when the LLM fails or when no model
is configured.
"""

import json as _json
import logging
import math
import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from litellm import acompletion as _litellm_acompletion

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.observability import log_tokens
from lifesci_common.llm_utils import json_mode_kwargs
from lifesci_common.triage_utils import extract_json_from_text

logger = logging.getLogger("medexpert.deliberation_synthesizer")

# ---------------------------------------------------------------------------
# Keyword helpers (retained as fallback)
# ---------------------------------------------------------------------------


def _extract_themes(text: str) -> set[str]:
    """Extract key theme words from perspective text."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "for", "and", "or", "but",
        "in", "on", "at", "to", "from", "of", "with", "by", "as", "if",
        "not", "no", "nor", "so", "yet", "both", "each", "few", "more",
        "most", "other", "some", "such", "than", "too", "very", "what",
        "which", "who", "whom", "this", "that", "these", "those", "it",
        "also", "just", "about", "into", "over", "after", "before",
    }
    words = set(re.findall(r"[a-z]{4,}", text.lower()))
    return words - stop_words


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# LLM deliberation analysis
# ---------------------------------------------------------------------------

_DELIBERATION_PROMPT_TEMPLATE = (
    "You are synthesizing advisory board perspectives on a medical question.\n"
    "Each perspective comes from a different analytical lens.\n\n"
    "PERSPECTIVES:\n{perspectives}\n\n"
    "Analyze these perspectives and return ONLY a JSON object (no markdown fences):\n"
    '{{"consensus_points": ["<point where most agree>"],\n'
    '  "contested_points": [\n'
    '    {{"point": "<disagreement>",\n'
    '      "positions": [\n'
    '        {{"persona": "<name>", "position": "<their stance>", '
    '"strength": "strong" | "moderate" | "weak"}}\n'
    '      ],\n'
    '      "resolution_note": "<which position has stronger evidence and why>"}}\n'
    '  ],\n'
    '  "blind_spots": ["<important angle none covered>"],\n'
    '  "synthesis": "<2-3 paragraph narrative synthesizing all perspectives>",\n'
    '  "confidence_level": "high" | "moderate" | "low",\n'
    '  "key_uncertainty": "<single biggest uncertainty>"\n'
    "}}"
)


async def _llm_deliberation(
    perspectives: list[dict],
    model: str,
    temperature: float,
    session_id: str,
    vertex_kwargs: dict | None = None,
) -> dict | None:
    """Use LLM for genuine multi-perspective synthesis.

    Returns parsed dict on success, or ``None`` on failure.
    """
    p_lines = []
    for p in perspectives[:6]:
        persona = p.get("persona", "Unknown")
        text = (p.get("perspective_text", "") or "")[:800]
        p_lines.append(f"**{persona}**: {text}")

    prompt = _DELIBERATION_PROMPT_TEMPLATE.replace(
        "{perspectives}", "\n\n".join(p_lines)
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
            "[deliberation_synthesizer] LLM deliberation failed, falling "
            "back to keyword analysis: %s",
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Keyword fallback (original implementation)
# ---------------------------------------------------------------------------


def _keyword_analysis(perspectives: list[dict]) -> dict:
    """Keyword-based consensus/dissent analysis (fallback path)."""
    persona_themes = {}
    for p in perspectives:
        persona = p.get("persona", "Unknown")
        text = p.get("perspective_text", "")
        persona_themes[persona] = _extract_themes(text)

    personas = list(persona_themes.keys())
    all_theme_sets = list(persona_themes.values())

    # Consensus themes (majority agreement)
    all_words: set[str] = set()
    for ts in all_theme_sets:
        all_words |= ts

    majority_threshold = math.ceil((len(personas) + 1) / 2)
    consensus_themes: list[str] = []
    disagreement_themes: list[str] = []

    for word in all_words:
        count = sum(1 for ts in all_theme_sets if word in ts)
        if count >= majority_threshold:
            consensus_themes.append(word)
        elif count == 1:
            disagreement_themes.append(word)

    # Pairwise similarity
    similarity_sum = 0.0
    pair_count = 0
    for i in range(len(personas)):
        for j in range(i + 1, len(personas)):
            similarity_sum += _jaccard_similarity(
                all_theme_sets[i], all_theme_sets[j]
            )
            pair_count += 1
    consensus_score = round(similarity_sum / pair_count, 3) if pair_count > 0 else 0.0

    # Minority dissent
    consensus_set = set(consensus_themes)
    minority_dissent = []
    for persona, themes in persona_themes.items():
        overlap_with_consensus = _jaccard_similarity(themes, consensus_set)
        if overlap_with_consensus < 0.3 and themes:
            unique_themes = themes - consensus_set
            minority_dissent.append({
                "persona": persona,
                "divergence_score": round(1 - overlap_with_consensus, 3),
                "unique_themes": sorted(list(unique_themes))[:10],
            })

    # Synthesis text
    synthesis_parts = []
    if consensus_themes:
        synthesis_parts.append(
            f"Consensus on {len(consensus_themes)} themes across {len(personas)} perspectives."
        )
    if minority_dissent:
        dissenters = ", ".join(d["persona"] for d in minority_dissent)
        synthesis_parts.append(f"Minority dissent from: {dissenters}.")
    if not consensus_themes:
        synthesis_parts.append(
            "No strong consensus identified. Perspectives are highly divergent."
        )

    return {
        "consensus_themes": sorted(consensus_themes)[:20],
        "disagreement_themes": sorted(disagreement_themes)[:20],
        "consensus_score": consensus_score,
        "minority_dissent": minority_dissent,
        "synthesis": " ".join(synthesis_parts),
        "perspective_count": len(personas),
        "analysis_method": "single_model_simulation",
        "limitation": (
            "Advisory board perspectives were generated by a single LLM "
            "simulating multiple personas. This is NOT equivalent to a "
            "multi-agent debate with independent models. Consensus scores "
            "reflect keyword overlap, not genuine intellectual agreement. "
            "Treat these perspectives as structured brainstorming aids, "
            "not as independent expert opinions."
        ),
        "method": "keyword",
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class DeliberationSynthesizerTool(DynamicTool):
    """Analyzes advisory board perspectives for consensus and dissent."""

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
        self.temperature: float = float(cfg.get("temperature", 0.3))

    @property
    def tool_name(self) -> str:
        return "deliberation_synthesizer"

    @property
    def tool_description(self) -> str:
        return (
            "Analyzes perspectives from advisory board personas (Clinical Pragmatist, "
            "Research Methodologist, Patient Advocate, Health Economist, Bioethicist, "
            "Global Health Specialist). When an LLM model is configured, produces "
            "genuine multi-perspective synthesis with consensus_points, contested_points "
            "(with resolution notes), blind_spots, confidence_level, and key_uncertainty. "
            "Falls back to keyword-based consensus_themes, disagreement_themes, "
            "consensus_score, and minority_dissent detection."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "perspectives_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON string of perspectives array. Each item: "
                        "{persona: str, perspective_text: str}"
                    ),
                ),
            },
            required=["perspectives_json"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        try:
            perspectives = _json.loads(args.get("perspectives_json", "[]"))
        except _json.JSONDecodeError:
            return {"error": "Invalid JSON in perspectives_json"}

        if not isinstance(perspectives, list) or len(perspectives) < 2:
            return {"error": "At least 2 perspectives are required"}

        # ------------------------------------------------------------------
        # Try LLM deliberation (if model configured)
        # ------------------------------------------------------------------
        if self.model:
            session_id = ""
            if hasattr(tool_context, "session") and tool_context.session:
                session_id = getattr(tool_context.session, "id", "")
            llm_result = await _llm_deliberation(
                perspectives=perspectives,
                model=self.model,
                temperature=self.temperature,
                session_id=session_id,
                vertex_kwargs=self._vertex_kwargs,
            )
            if llm_result:
                return {
                    "consensus_points": llm_result.get("consensus_points", []),
                    "contested_points": llm_result.get("contested_points", []),
                    "blind_spots": llm_result.get("blind_spots", []),
                    "synthesis": llm_result.get("synthesis", ""),
                    "confidence_level": llm_result.get("confidence_level", "moderate"),
                    "key_uncertainty": llm_result.get("key_uncertainty", ""),
                    "perspective_count": len(perspectives),
                    "analysis_method": "llm_deliberation",
                    "method": "llm",
                    # Retain keyword metrics for backward compatibility
                    **self._keyword_metrics(perspectives),
                }

        # ------------------------------------------------------------------
        # Keyword fallback
        # ------------------------------------------------------------------
        return _keyword_analysis(perspectives)

    @staticmethod
    def _keyword_metrics(perspectives: list[dict]) -> dict:
        """Extract consensus_score and themes from keyword analysis for backward compat."""
        full = _keyword_analysis(perspectives)
        return {
            "consensus_themes": full["consensus_themes"],
            "consensus_score": full["consensus_score"],
        }
