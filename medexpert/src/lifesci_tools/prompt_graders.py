"""Medical domain graders for specialist prompt evolution.

Scores specialist agent performance using 5 complementary graders:
  1. Entity Preservation  — medical entities in output vs. source evidence
  2. Evidence Fidelity     — token-level similarity to source evidence
  3. Citation Accuracy     — leverages existing claim_verifier output
  4. Composite Quality     — heuristic from coverage + grade + substantiveness
  5. LLM-as-Judge          — async batched Gemini Flash call (separate worker)

Graders 1-4 are synchronous (called from cold worker's _persist_one).
Grader 5 runs in the llm_grader_worker background thread.
"""

import logging
import re
from typing import Any

from lifesci_common.constants import (
    GRADER_THRESHOLDS,
    GRADER_WEIGHTS,
    MEDICAL_ENTITY_PATTERNS,
)
from lifesci_tools.cold_store import _STOP_WORDS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MEDICAL_TERMS = frozenset(
    # High-value medical vocabulary that gets 2x weight in fidelity scoring.
    # Kept small and general — domain tools already capture specifics.
    [
        "efficacy",
        "safety",
        "pharmacokinetics",
        "bioavailability",
        "toxicity",
        "pathogenic",
        "benign",
        "malignant",
        "metastatic",
        "recurrence",
        "remission",
        "prognosis",
        "diagnosis",
        "prevalence",
        "incidence",
        "morbidity",
        "mortality",
        "contraindication",
        "interaction",
        "adverse",
        "placebo",
        "randomized",
        "blinded",
        "cohort",
        "meta-analysis",
        "systematic",
        "dosage",
        "milligrams",
        "micrograms",
        "receptor",
        "agonist",
        "antagonist",
        "inhibitor",
        "mutation",
        "variant",
        "allele",
        "genotype",
        "phenotype",
        "expression",
        "sequencing",
        "carcinogen",
        "teratogen",
        "hepatotoxic",
        "nephrotoxic",
        "cytotoxic",
        "immunogenic",
        "antibody",
        "antigen",
        "epitope",
        "kinase",
        "protease",
        "enzyme",
        "substrate",
        "metabolite",
    ]
)


def _tokenise(text: str) -> list[str]:
    """Lowercase, remove punctuation, keep tokens of 4+ chars, strip stop words."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [t for t in text.split() if len(t) >= 4 and t not in _STOP_WORDS]


def _extract_entities(text: str) -> set[str]:
    """Extract medical entities from text using regex patterns."""
    entities: set[str] = set()
    for _pattern_name, pattern in MEDICAL_ENTITY_PATTERNS.items():
        for match in re.finditer(pattern, text):
            entities.add(match.group())
    return entities


# ---------------------------------------------------------------------------
# Grader 1: Entity Preservation (deterministic)
# ---------------------------------------------------------------------------


def grade_entity_preservation(
    specialist_response: str,
    source_evidence: list[dict[str, Any]],
) -> float:
    """Score how well the specialist preserved medical entities from sources.

    Returns 0.0-1.0 where 1.0 means all source entities appear in the response.
    Returns 1.0 if no entities are found in source evidence (nothing to preserve).
    """
    if not specialist_response or not source_evidence:
        return 0.0

    # Collect entities from all source evidence
    source_text = " ".join(
        str(s.get("text", ""))
        + " "
        + str(s.get("abstract", ""))
        + " "
        + str(s.get("title", ""))
        for s in source_evidence
    )
    expected = _extract_entities(source_text)
    if not expected:
        return 1.0  # Nothing to preserve

    found = sum(1 for entity in expected if entity in specialist_response)
    return found / len(expected)


# ---------------------------------------------------------------------------
# Grader 2: Evidence Fidelity (deterministic, text similarity)
# ---------------------------------------------------------------------------


def grade_evidence_fidelity(
    specialist_response: str,
    source_evidence: list[dict[str, Any]],
) -> float:
    """Weighted Jaccard similarity between response and source tokens.

    Medical terms count 2x. Returns 0.0-1.0.
    """
    if not specialist_response or not source_evidence:
        return 0.0

    source_text = " ".join(
        str(s.get("text", ""))
        + " "
        + str(s.get("abstract", ""))
        + " "
        + str(s.get("title", ""))
        for s in source_evidence
    )

    response_tokens = _tokenise(specialist_response)
    source_tokens = _tokenise(source_text)

    if not response_tokens or not source_tokens:
        return 0.0

    # Build weighted token bags
    def _weighted_bag(tokens: list[str]) -> dict[str, float]:
        bag: dict[str, float] = {}
        for t in tokens:
            weight = 2.0 if t in _MEDICAL_TERMS else 1.0
            bag[t] = bag.get(t, 0.0) + weight
        return bag

    resp_bag = _weighted_bag(response_tokens)
    src_bag = _weighted_bag(source_tokens)

    all_keys = set(resp_bag) | set(src_bag)
    intersection = sum(min(resp_bag.get(k, 0), src_bag.get(k, 0)) for k in all_keys)
    union = sum(max(resp_bag.get(k, 0), src_bag.get(k, 0)) for k in all_keys)

    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Grader 3: Citation Accuracy (from existing verification signals)
# ---------------------------------------------------------------------------


def grade_citation_accuracy(
    verification_result: dict[str, Any] | None,
    has_citations: bool = False,
) -> float:
    """Score citation accuracy from existing claim_verifier output.

    If verification ran during the session, uses ``overall_score``.
    Otherwise falls back: 1.0 if citations present, 0.5 if none.
    """
    if verification_result:
        score = verification_result.get("overall_score")
        if score is not None:
            return float(score)
        # Fallback: ratio of supported claims
        total = verification_result.get("total_claims", 0)
        supported = verification_result.get("supported_count", 0)
        if total > 0:
            return supported / total

    return 1.0 if has_citations else 0.5


# ---------------------------------------------------------------------------
# Grader 4: Composite Quality (heuristic)
# ---------------------------------------------------------------------------


def grade_composite_quality(
    specialist_response: str,
    coverage_contribution: float = 0.0,
    avg_grade_score: float = 0.0,
) -> float:
    """Heuristic quality score from session signals.

    Combines:
      - Coverage contribution (0.0-1.0)
      - Evidence grade average (0.0-5.0, normalised to 0.0-1.0)
      - Response substantiveness (word count / 200, capped at 1.0)
    """
    if not specialist_response:
        return 0.0

    coverage = max(0.0, min(1.0, coverage_contribution))
    grade_norm = max(0.0, min(1.0, avg_grade_score / 5.0))
    word_count = len(specialist_response.split())
    substantiveness = min(1.0, word_count / 200.0)

    return 0.4 * coverage + 0.3 * grade_norm + 0.3 * substantiveness


# ---------------------------------------------------------------------------
# Grader 5: LLM-as-Judge (async — called by llm_grader_worker, not here)
# ---------------------------------------------------------------------------

LLM_JUDGE_SYSTEM = (
    "You are a medical research quality evaluator.\n"
    "Score the specialist's response on a scale of 0.0 to 1.0 based on:\n"
    "- Faithfulness to source evidence (no hallucinated claims)\n"
    "- Completeness (key findings from sources are represented)\n"
    "- Medical safety (no dangerous misinformation)\n"
    "- Citation quality (claims are attributed to specific sources)\n\n"
    "Respond with ONLY a single decimal number between 0.0 and 1.0."
)

LLM_JUDGE_USER_TEMPLATE = (
    "Question: {question}\n\n"
    "Source evidence:\n{evidence}\n\n"
    "Specialist response:\n{response}\n\n"
    "Score (0.0-1.0):"
)


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def calculate_composite(scores: dict[str, float | None]) -> float:
    """Weighted average across available graders.

    Handles missing LLM judge (None) gracefully by redistributing
    its weight proportionally across available graders.
    """
    available = {
        k: v for k, v in scores.items() if v is not None and k in GRADER_WEIGHTS
    }
    if not available:
        return 0.0
    weight_sum = sum(GRADER_WEIGHTS[k] for k in available)
    if weight_sum <= 0:
        return 0.0
    return sum(available[k] * (GRADER_WEIGHTS[k] / weight_sum) for k in available)


def is_lenient_pass(scores: dict[str, float | None], composite: float) -> bool:
    """Check if a specialist passes the lenient threshold.

    Passes if 75% of available graders exceed their individual thresholds
    OR the composite score is >= 0.65.
    """
    available = {
        k: v for k, v in scores.items() if v is not None and k in GRADER_THRESHOLDS
    }
    if not available:
        return False
    passed_count = sum(1 for k, v in available.items() if v >= GRADER_THRESHOLDS[k])
    required = max(1, int(len(available) * 0.75))
    return (passed_count >= required) or (composite >= 0.65)


# ---------------------------------------------------------------------------
# Convenience: score a specialist from session signals
# ---------------------------------------------------------------------------


def score_specialist(
    agent_name: str,
    session_signals: dict[str, Any],
) -> dict[str, Any]:
    """Compute all synchronous grader scores for a specialist from session data.

    The ``session_signals`` dict is the cold-worker flush payload with:
      - sources: [{source_name, evidence_count, avg_grade_score, ...}]
      - agent_coverage: {agent_name: float}
      - verification_verdict, verification_score
      - query_text
      - specialist_responses: {agent_name: str}  (if available)

    Returns a dict suitable for ``cold_store.write_prompt_score()``.
    """
    response_text = (session_signals.get("specialist_responses") or {}).get(
        agent_name, ""
    )
    sources = session_signals.get("sources") or []
    coverage = (session_signals.get("agent_coverage") or {}).get(agent_name, 0.0)
    verification = session_signals.get("verification_result")
    has_cites = bool(response_text and "[[cite:" in response_text)

    # Compute per-source avg grade for this session
    avg_grade = 0.0
    if sources:
        grade_scores = [s.get("avg_grade_score", 0.0) for s in sources]
        avg_grade = sum(grade_scores) / len(grade_scores) if grade_scores else 0.0

    # Build source evidence dicts for entity/fidelity graders.
    # Sources in the flush payload are MCP metadata dicts (source_name,
    # evidence_count, avg_grade_score) — NOT raw evidence text.  Extract
    # any text-like fields that may be present, falling back to source_name.
    source_dicts = [
        {
            "text": s.get("text", "") or s.get("abstract", "") or "",
            "abstract": s.get("abstract", ""),
            "title": s.get("title", "") or s.get("source_name", ""),
        }
        for s in sources
        if isinstance(s, dict)
    ]

    entity = grade_entity_preservation(response_text, source_dicts)
    fidelity = grade_evidence_fidelity(response_text, source_dicts)
    citation = grade_citation_accuracy(verification, has_cites)
    quality = grade_composite_quality(response_text, coverage, avg_grade)

    scores = {
        "entity": round(entity, 4),
        "fidelity": round(fidelity, 4),
        "citation": round(citation, 4),
        "quality": round(quality, 4),
        "llm_judge": None,  # Backfilled async by llm_grader_worker
    }
    scores["composite"] = round(calculate_composite(scores), 4)
    return scores
