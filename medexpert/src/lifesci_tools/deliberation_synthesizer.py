"""Deliberation Synthesizer — advisory board consensus analysis.

Analyzes perspectives from multiple advisory board personas, identifies
consensus themes and disagreements, computes a consensus score, and
flags minority dissent.
"""

import re
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool


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


class DeliberationSynthesizerTool(DynamicTool):
    """Analyzes advisory board perspectives for consensus and dissent."""

    @property
    def tool_name(self) -> str:
        return "deliberation_synthesizer"

    @property
    def tool_description(self) -> str:
        return (
            "Analyzes perspectives from advisory board personas (Clinical Pragmatist, "
            "Research Methodologist, Patient Advocate, Health Economist, Bioethicist, "
            "Global Health Specialist). Identifies consensus themes, disagreements, "
            "computes a consensus score, and flags minority dissent."
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
        import json

        try:
            perspectives = json.loads(args.get("perspectives_json", "[]"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in perspectives_json"}

        if not isinstance(perspectives, list) or len(perspectives) < 2:
            return {"error": "At least 2 perspectives are required"}

        # Extract themes for each perspective
        persona_themes = {}
        for p in perspectives:
            persona = p.get("persona", "Unknown")
            text = p.get("perspective_text", "")
            persona_themes[persona] = _extract_themes(text)

        personas = list(persona_themes.keys())
        all_theme_sets = list(persona_themes.values())

        # Find consensus themes (appear in majority of perspectives)
        all_words = set()
        for ts in all_theme_sets:
            all_words |= ts

        # Strict majority: more than half must agree (e.g. 2 of 2, 2 of 3, 3 of 4)
        import math
        majority_threshold = math.ceil((len(personas) + 1) / 2)
        consensus_themes = []
        disagreement_themes = []

        for word in all_words:
            count = sum(1 for ts in all_theme_sets if word in ts)
            if count >= majority_threshold:
                consensus_themes.append(word)
            elif count == 1:
                disagreement_themes.append(word)

        # Compute pairwise similarity for consensus score
        similarity_sum = 0.0
        pair_count = 0
        for i in range(len(personas)):
            for j in range(i + 1, len(personas)):
                similarity_sum += _jaccard_similarity(
                    all_theme_sets[i], all_theme_sets[j]
                )
                pair_count += 1

        consensus_score = round(similarity_sum / pair_count, 3) if pair_count > 0 else 0.0

        # Identify minority dissent (personas whose themes diverge most from consensus)
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

        # Build synthesis text
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
        }
