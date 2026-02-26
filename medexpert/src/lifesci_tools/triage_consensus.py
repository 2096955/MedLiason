"""Triage Consensus Engine — programmatic diagnosis clustering.

Groups specialist diagnoses by similarity and selects the cluster with
the highest combined confidence.  No LLM dependency — pure Python logic.

Uses the same Jaccard token similarity approach as ``cold_store.py``.
"""

import logging
import re

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

from lifesci_common.constants import TRIAGE_CONSENSUS_JACCARD_THRESHOLD

log = logging.getLogger(__name__)

# Reuse stop words from cold_store for consistency
_STOP_WORDS = frozenset(
    [
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do",
        "does", "for", "from", "has", "have", "how", "in", "is", "it",
        "its", "may", "not", "of", "on", "or", "so", "that", "the",
        "their", "them", "then", "there", "these", "this", "to", "was",
        "we", "were", "what", "when", "where", "which", "who", "why",
        "will", "with", "would",
    ]
)


class TriageConsensusTool(DynamicTool):
    """Programmatic consensus engine for specialist diagnoses."""

    @property
    def tool_name(self) -> str:
        return "triage_consensus"

    @property
    def tool_description(self) -> str:
        return (
            "Programmatic consensus engine that groups specialist diagnoses by "
            "similarity and selects the cluster with the highest average confidence. "
            "No LLM calls — deterministic Python logic. "
            "Discards 'Insufficient information' responses (confidence 0). "
            "Returns INCONCLUSIVE if all specialists returned insufficient info."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "verdicts": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON array of specialist verdicts: "
                        "[{specialist, diagnosis, confidence, thinking, tier}]"
                    ),
                ),
            },
            required=["verdicts"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        import json

        raw = args.get("verdicts", "[]")
        try:
            verdicts = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            verdicts = []

        if not verdicts:
            return _inconclusive_result()

        # 1. Discard insufficient information (confidence == 0)
        valid = [
            v for v in verdicts
            if isinstance(v, dict)
            and v.get("confidence", 0) > 0
            and v.get("diagnosis", "").lower() != "insufficient information"
        ]
        insufficient_count = len(verdicts) - len(valid)

        if not valid:
            return _inconclusive_result(insufficient_count=insufficient_count)

        # 2. Group by exact match (lowercased, stripped)
        clusters: list[list[dict]] = []
        for v in valid:
            placed = False
            diag_norm = _normalize_diagnosis(v["diagnosis"])
            for cluster in clusters:
                ref_norm = _normalize_diagnosis(cluster[0]["diagnosis"])
                if diag_norm == ref_norm:
                    cluster.append(v)
                    placed = True
                    break
            if not placed:
                clusters.append([v])

        # 3. Merge similar clusters via Jaccard
        threshold = TRIAGE_CONSENSUS_JACCARD_THRESHOLD
        merged = True
        while merged:
            merged = False
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    sim = _jaccard_similarity(
                        _tokenize(clusters[i][0]["diagnosis"]),
                        _tokenize(clusters[j][0]["diagnosis"]),
                    )
                    if sim >= threshold:
                        clusters[i].extend(clusters[j])
                        clusters.pop(j)
                        merged = True
                        break
                if merged:
                    break

        # 4. Rank clusters by average confidence, then count
        clusters.sort(
            key=lambda c: (
                sum(v["confidence"] for v in c) / len(c),
                len(c),
            ),
            reverse=True,
        )

        # 5. Select winner
        winner = clusters[0]
        mean_confidence = sum(v["confidence"] for v in winner) / len(winner)
        # Use the highest-confidence diagnosis text as the canonical name
        winner.sort(key=lambda v: v["confidence"], reverse=True)
        consensus_diagnosis = winner[0]["diagnosis"]

        supporting = [
            {"specialist": v["specialist"], "confidence": v["confidence"]}
            for v in winner
        ]
        dissenting = []
        for cluster in clusters[1:]:
            for v in cluster:
                dissenting.append({
                    "specialist": v["specialist"],
                    "alternative_diagnosis": v["diagnosis"],
                    "confidence": v["confidence"],
                })

        return {
            "consensus_diagnosis": consensus_diagnosis,
            "mean_confidence": round(mean_confidence, 1),
            "supporting_specialists": supporting,
            "dissenting_specialists": dissenting,
            "cluster_count": len(clusters),
            "insufficient_count": insufficient_count,
        }


def _inconclusive_result(insufficient_count: int = 0) -> dict:
    """Return when no valid consensus can be formed."""
    return {
        "consensus_diagnosis": "INCONCLUSIVE",
        "mean_confidence": 0,
        "supporting_specialists": [],
        "dissenting_specialists": [],
        "cluster_count": 0,
        "insufficient_count": insufficient_count,
    }


def _normalize_diagnosis(text: str) -> str:
    """Lowercase, strip, remove punctuation."""
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


def _tokenize(text: str) -> set[str]:
    """Lowercase, split, remove stop words — returns token set."""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return {t for t in text.split() if t and t not in _STOP_WORDS}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard index: |intersection| / |union|."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0
