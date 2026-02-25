"""Tests for prompt_graders — medical domain graders for specialist prompt evolution.

Covers all 6 public functions:
  - grade_entity_preservation
  - grade_evidence_fidelity
  - grade_citation_accuracy
  - grade_composite_quality
  - calculate_composite
  - is_lenient_pass
  - score_specialist
"""

import pytest

from lifesci_tools.prompt_graders import (
    calculate_composite,
    grade_citation_accuracy,
    grade_composite_quality,
    grade_entity_preservation,
    grade_evidence_fidelity,
    is_lenient_pass,
    score_specialist,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def evidence_with_entities():
    """Source evidence containing PMIDs, NCT IDs, DOIs, gene symbols, and dosages."""
    return [
        {
            "text": (
                "Study PMID 12345678 (NCT01234567) demonstrated that BRCA1 mutations "
                "increase risk. Patients received 500 mg daily of the study drug. "
                "DOI: 10.1016/j.cell.2024.01.001"
            ),
            "abstract": "KRAS G12D variant detected in 23% of subjects.",
            "title": "Phase III trial of TP53 targeted therapy",
        },
    ]


@pytest.fixture
def evidence_no_entities():
    """Source evidence with no recognisable medical entities."""
    return [
        {
            "text": "The study was conducted at a large academic centre.",
            "abstract": "Results were statistically significant.",
            "title": "A recent investigation",
        },
    ]


@pytest.fixture
def medical_text_pair():
    """Matched source and response texts sharing medical vocabulary."""
    source = [
        {
            "text": (
                "Pharmacokinetics of the inhibitor showed improved bioavailability. "
                "Hepatotoxic effects were not observed. The receptor agonist had "
                "high efficacy in the randomized cohort."
            ),
            "abstract": "",
            "title": "",
        },
    ]
    response = (
        "The inhibitor demonstrated improved bioavailability and pharmacokinetics. "
        "No hepatotoxic signals were found. The receptor agonist showed high efficacy "
        "in a randomized cohort study."
    )
    return source, response


@pytest.fixture
def disjoint_text_pair():
    """Source and response with completely different vocabulary."""
    source = [
        {
            "text": "Alpha beta gamma delta epsilon zeta eta theta iota kappa.",
            "abstract": "",
            "title": "",
        },
    ]
    response = "Lorem ipsum dolor sit amet consectetur adipiscing elit tempor."
    return source, response


# ---------------------------------------------------------------------------
# Grader 1: Entity Preservation
# ---------------------------------------------------------------------------


class TestEntityPreservation:
    def test_entity_preservation_all_found(self, evidence_with_entities):
        """All entities present in source also appear in response -> 1.0."""
        # Build a response that contains every entity from the evidence.
        # The gene_symbol regex also captures short uppercase tokens like G12D, III,
        # DOI, PMID — so we must include them all in the response.
        response = (
            "According to PMID 12345678 from Phase III trial NCT01234567, BRCA1 "
            "mutations were confirmed. The G12D variant of KRAS and TP53 were noted. "
            "Patients received 500 mg daily. "
            "See DOI: 10.1016/j.cell.2024.01.001."
        )
        score = grade_entity_preservation(response, evidence_with_entities)
        assert score == 1.0

    def test_entity_preservation_partial(self, evidence_with_entities):
        """Only some entities present -> score between 0.0 and 1.0 exclusive."""
        # Include only the PMID and gene symbol BRCA1 but omit NCT ID, DOI, dosage
        response = "Study 12345678 found that BRCA1 is important."
        score = grade_entity_preservation(response, evidence_with_entities)
        assert 0.0 < score < 1.0

    def test_entity_preservation_no_entities_in_source(self, evidence_no_entities):
        """No extractable entities in source -> 1.0 (nothing to preserve)."""
        response = "This is a generic response about the study."
        score = grade_entity_preservation(response, evidence_no_entities)
        assert score == 1.0

    def test_entity_preservation_empty_response(self, evidence_with_entities):
        """Empty response string -> 0.0."""
        score = grade_entity_preservation("", evidence_with_entities)
        assert score == 0.0

    def test_entity_preservation_empty_evidence(self):
        """Empty evidence list -> 0.0."""
        score = grade_entity_preservation("Some response text.", [])
        assert score == 0.0

    def test_entity_preservation_none_evidence(self):
        """None-ish evidence (empty list) -> 0.0."""
        score = grade_entity_preservation("Some response text.", [])
        assert score == 0.0


# ---------------------------------------------------------------------------
# Grader 2: Evidence Fidelity
# ---------------------------------------------------------------------------


class TestEvidenceFidelity:
    def test_evidence_fidelity_identical(self):
        """Same text as both source and response -> score close to 1.0."""
        text = (
            "The randomized controlled trial demonstrated that the pharmacokinetics "
            "of the drug were improved with the new formulation showing bioavailability."
        )
        evidence = [{"text": text, "abstract": "", "title": ""}]
        score = grade_evidence_fidelity(text, evidence)
        assert score > 0.5

    def test_evidence_fidelity_no_overlap(self, disjoint_text_pair):
        """Completely different vocabularies -> score near 0.0."""
        source, response = disjoint_text_pair
        score = grade_evidence_fidelity(response, source)
        assert score < 0.1

    def test_evidence_fidelity_medical_terms_weighted(self, medical_text_pair):
        """Medical terms boost weighted Jaccard score above simple overlap."""
        source, response = medical_text_pair
        score_with_medical = grade_evidence_fidelity(response, source)

        # Compare with a response that swaps medical terms for generic ones
        generic_response = (
            "The compound demonstrated improved characteristics and behaviour. "
            "No harmful signals were found. The compound showed high results "
            "in a large study."
        )
        score_generic = grade_evidence_fidelity(generic_response, source)
        assert score_with_medical > score_generic

    def test_evidence_fidelity_empty_input(self):
        """Empty response or evidence -> 0.0."""
        evidence = [{"text": "Some source text here.", "abstract": "", "title": ""}]
        assert grade_evidence_fidelity("", evidence) == 0.0
        assert grade_evidence_fidelity("Some response.", []) == 0.0

    def test_evidence_fidelity_short_tokens_filtered(self):
        """Tokens under 4 characters are filtered out by _tokenise."""
        # "is" and "a" and "the" should all be stripped; only 4+ char tokens count
        evidence = [{"text": "the long word therapy", "abstract": "", "title": ""}]
        response = "the long word therapy"
        score = grade_evidence_fidelity(response, evidence)
        # "long", "word", "therapy" survive tokenisation -> high overlap
        assert score > 0.5


# ---------------------------------------------------------------------------
# Grader 3: Citation Accuracy
# ---------------------------------------------------------------------------


class TestCitationAccuracy:
    def test_citation_accuracy_from_verification(self):
        """Uses overall_score from verification_result when present."""
        result = {"overall_score": 0.85, "total_claims": 10, "supported_count": 7}
        score = grade_citation_accuracy(result, has_citations=True)
        assert score == 0.85

    def test_citation_accuracy_from_ratio(self):
        """Falls back to supported/total ratio when overall_score is absent."""
        result = {"total_claims": 10, "supported_count": 7}
        score = grade_citation_accuracy(result, has_citations=True)
        assert score == pytest.approx(0.7)

    def test_citation_accuracy_ratio_all_supported(self):
        """All claims supported -> 1.0 via ratio."""
        result = {"total_claims": 5, "supported_count": 5}
        score = grade_citation_accuracy(result)
        assert score == pytest.approx(1.0)

    def test_citation_accuracy_ratio_zero_total(self):
        """Zero total claims with no overall_score -> fallback path."""
        result = {"total_claims": 0, "supported_count": 0}
        score = grade_citation_accuracy(result, has_citations=True)
        # total_claims is 0, so ratio branch skipped -> fallback 1.0
        assert score == 1.0

    def test_citation_accuracy_fallback_with_citations(self):
        """No verification result at all, but citations present -> 1.0."""
        score = grade_citation_accuracy(None, has_citations=True)
        assert score == 1.0

    def test_citation_accuracy_fallback_no_citations(self):
        """No verification result, no citations -> 0.5."""
        score = grade_citation_accuracy(None, has_citations=False)
        assert score == 0.5

    def test_citation_accuracy_empty_dict(self):
        """Empty verification dict (truthy but no useful keys) -> fallback."""
        # Empty dict is falsy in Python — BUT dict with irrelevant keys is truthy
        # An empty dict {} is falsy, so it hits the fallback
        score = grade_citation_accuracy({}, has_citations=False)
        # {} is falsy in the `if verification_result:` check
        assert score == 0.5

    def test_citation_accuracy_overall_score_zero(self):
        """overall_score of 0.0 (falsy float but not None) should still be used."""
        result = {"overall_score": 0.0}
        score = grade_citation_accuracy(result)
        assert score == 0.0


# ---------------------------------------------------------------------------
# Grader 4: Composite Quality
# ---------------------------------------------------------------------------


class TestCompositeQuality:
    def test_composite_quality_full_scores(self):
        """All signals present -> weighted combination."""
        # 200 words for substantiveness = 1.0
        response = " ".join(["word"] * 200)
        score = grade_composite_quality(
            response,
            coverage_contribution=0.8,
            avg_grade_score=4.0,  # normalised to 0.8
        )
        # 0.4*0.8 + 0.3*0.8 + 0.3*1.0 = 0.32 + 0.24 + 0.30 = 0.86
        assert score == pytest.approx(0.86)

    def test_composite_quality_empty_response(self):
        """Empty response -> 0.0 regardless of other signals."""
        score = grade_composite_quality("", coverage_contribution=1.0, avg_grade_score=5.0)
        assert score == 0.0

    def test_composite_quality_minimum_signals(self):
        """Zero coverage and grade but non-empty short response."""
        response = "Short answer."  # 2 words -> substantiveness = 2/200 = 0.01
        score = grade_composite_quality(response, coverage_contribution=0.0, avg_grade_score=0.0)
        # 0.4*0 + 0.3*0 + 0.3*0.01 = 0.003
        assert score == pytest.approx(0.003)

    def test_composite_quality_coverage_clamped(self):
        """Coverage contribution > 1.0 is clamped to 1.0."""
        response = " ".join(["word"] * 200)
        score_clamped = grade_composite_quality(response, coverage_contribution=1.5)
        score_at_max = grade_composite_quality(response, coverage_contribution=1.0)
        assert score_clamped == pytest.approx(score_at_max)

    def test_composite_quality_grade_normalisation(self):
        """avg_grade_score is divided by 5.0 and clamped to [0, 1]."""
        response = " ".join(["word"] * 200)
        # grade_score=5.0 -> normalised 1.0; grade_score=10.0 -> clamped to 1.0
        score_five = grade_composite_quality(response, avg_grade_score=5.0)
        score_ten = grade_composite_quality(response, avg_grade_score=10.0)
        assert score_five == pytest.approx(score_ten)

    def test_composite_quality_substantiveness_cap(self):
        """Word count beyond 200 is capped at 1.0 substantiveness."""
        response_200 = " ".join(["word"] * 200)
        response_500 = " ".join(["word"] * 500)
        score_200 = grade_composite_quality(response_200)
        score_500 = grade_composite_quality(response_500)
        assert score_200 == pytest.approx(score_500)


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


class TestCalculateComposite:
    def test_calculate_composite_all_present(self):
        """All 5 graders present -> standard weighted average."""
        scores = {
            "entity": 0.9,
            "fidelity": 0.7,
            "citation": 0.8,
            "quality": 0.6,
            "llm_judge": 0.85,
        }
        composite = calculate_composite(scores)
        # Weights: entity=0.20, fidelity=0.20, citation=0.25, quality=0.15, llm_judge=0.20
        # Total weight = 1.0
        expected = (
            0.9 * 0.20 + 0.7 * 0.20 + 0.8 * 0.25 + 0.6 * 0.15 + 0.85 * 0.20
        )
        assert composite == pytest.approx(expected)

    def test_calculate_composite_missing_llm_judge(self):
        """4 graders present, llm_judge=None -> weight redistributed."""
        scores = {
            "entity": 0.9,
            "fidelity": 0.7,
            "citation": 0.8,
            "quality": 0.6,
            "llm_judge": None,
        }
        composite = calculate_composite(scores)
        # Available weights: entity=0.20, fidelity=0.20, citation=0.25, quality=0.15
        # weight_sum = 0.80
        # Redistributed: entity=0.20/0.80, fidelity=0.20/0.80, etc.
        expected = (
            0.9 * (0.20 / 0.80)
            + 0.7 * (0.20 / 0.80)
            + 0.8 * (0.25 / 0.80)
            + 0.6 * (0.15 / 0.80)
        )
        assert composite == pytest.approx(expected)

    def test_calculate_composite_all_none(self):
        """All graders None -> 0.0."""
        scores = {
            "entity": None,
            "fidelity": None,
            "citation": None,
            "quality": None,
            "llm_judge": None,
        }
        assert calculate_composite(scores) == 0.0

    def test_calculate_composite_empty_dict(self):
        """Empty scores dict -> 0.0."""
        assert calculate_composite({}) == 0.0

    def test_calculate_composite_single_grader(self):
        """Single grader present -> its score is the composite."""
        scores = {
            "entity": 0.75,
            "fidelity": None,
            "citation": None,
            "quality": None,
            "llm_judge": None,
        }
        assert calculate_composite(scores) == pytest.approx(0.75)

    def test_calculate_composite_ignores_unknown_keys(self):
        """Keys not in GRADER_WEIGHTS are ignored."""
        scores = {
            "entity": 0.9,
            "fidelity": 0.7,
            "citation": 0.8,
            "quality": 0.6,
            "llm_judge": 0.85,
            "unknown_grader": 0.5,  # Should be ignored
        }
        # Should produce the same result as the all-present test
        scores_clean = {k: v for k, v in scores.items() if k != "unknown_grader"}
        assert calculate_composite(scores) == pytest.approx(
            calculate_composite(scores_clean)
        )


# ---------------------------------------------------------------------------
# Lenient pass
# ---------------------------------------------------------------------------


class TestIsLenientPass:
    def test_is_lenient_pass_by_grader_count(self):
        """4 out of 5 graders pass their thresholds -> True (80% >= 75%)."""
        # Thresholds: entity=0.8, fidelity=0.3, citation=0.7, quality=0.6, llm_judge=0.75
        scores = {
            "entity": 0.9,      # pass (>= 0.8)
            "fidelity": 0.5,    # pass (>= 0.3)
            "citation": 0.8,    # pass (>= 0.7)
            "quality": 0.7,     # pass (>= 0.6)
            "llm_judge": 0.3,   # fail (< 0.75)
        }
        # composite deliberately low to ensure it's the grader count that passes
        assert is_lenient_pass(scores, composite=0.40) is True

    def test_is_lenient_pass_by_composite(self):
        """Low grader pass count but composite >= 0.65 -> True."""
        # Only 1 grader passes its threshold
        scores = {
            "entity": 0.5,      # fail
            "fidelity": 0.5,    # pass (>= 0.3)
            "citation": 0.3,    # fail
            "quality": 0.3,     # fail
            "llm_judge": 0.4,   # fail
        }
        # 1 out of 5 pass -> 20% < 75%, but composite >= 0.65 saves it
        assert is_lenient_pass(scores, composite=0.65) is True

    def test_is_lenient_pass_fails(self):
        """1 out of 5 graders pass and composite < 0.65 -> False."""
        scores = {
            "entity": 0.1,      # fail
            "fidelity": 0.5,    # pass (>= 0.3)
            "citation": 0.1,    # fail
            "quality": 0.1,     # fail
            "llm_judge": 0.1,   # fail
        }
        assert is_lenient_pass(scores, composite=0.30) is False

    def test_is_lenient_pass_all_pass(self):
        """All 5 graders pass -> True."""
        scores = {
            "entity": 1.0,
            "fidelity": 1.0,
            "citation": 1.0,
            "quality": 1.0,
            "llm_judge": 1.0,
        }
        assert is_lenient_pass(scores, composite=1.0) is True

    def test_is_lenient_pass_with_none_graders(self):
        """None graders are excluded from the available count."""
        # 3 available graders, all pass -> 100% >= 75% -> True
        scores = {
            "entity": 0.9,
            "fidelity": 0.5,
            "citation": 0.8,
            "quality": None,
            "llm_judge": None,
        }
        assert is_lenient_pass(scores, composite=0.40) is True

    def test_is_lenient_pass_all_none(self):
        """All graders None -> False (no available graders)."""
        scores = {
            "entity": None,
            "fidelity": None,
            "citation": None,
            "quality": None,
            "llm_judge": None,
        }
        assert is_lenient_pass(scores, composite=0.90) is False

    def test_is_lenient_pass_exact_75_percent(self):
        """Exactly 75% of 4 available graders pass -> required = int(4*0.75) = 3."""
        # 3 out of 4 pass -> passes the 75% threshold
        scores = {
            "entity": 0.9,      # pass
            "fidelity": 0.5,    # pass
            "citation": 0.8,    # pass
            "quality": 0.1,     # fail
            "llm_judge": None,  # excluded
        }
        assert is_lenient_pass(scores, composite=0.40) is True


# ---------------------------------------------------------------------------
# score_specialist convenience function
# ---------------------------------------------------------------------------


class TestScoreSpecialist:
    def test_score_specialist_returns_all_keys(self):
        """Return dict contains entity, fidelity, citation, quality, llm_judge, composite."""
        signals = {
            "specialist_responses": {
                "LiteratureSpecialist": (
                    "BRCA1 mutations were found in PMID 12345678. Dosage was 500 mg. "
                    "[[cite:1]] The study NCT01234567 confirmed results."
                ),
            },
            "sources": [
                {
                    "source_name": "pubmed",
                    "evidence_count": 5,
                    "avg_grade_score": 3.5,
                },
            ],
            "agent_coverage": {"LiteratureSpecialist": 0.7},
            "verification_result": {"overall_score": 0.8},
        }
        result = score_specialist("LiteratureSpecialist", signals)

        expected_keys = {"entity", "fidelity", "citation", "quality", "llm_judge", "composite"}
        assert set(result.keys()) == expected_keys

    def test_score_specialist_llm_judge_is_none(self):
        """llm_judge is always None (backfilled async)."""
        signals = {
            "specialist_responses": {"TestAgent": "Some response text."},
            "sources": [],
            "agent_coverage": {"TestAgent": 0.5},
            "verification_result": None,
        }
        result = score_specialist("TestAgent", signals)
        assert result["llm_judge"] is None

    def test_score_specialist_missing_agent(self):
        """Agent not in specialist_responses -> empty response -> low scores."""
        signals = {
            "specialist_responses": {"OtherAgent": "Some response."},
            "sources": [{"source_name": "pubmed", "avg_grade_score": 3.0}],
            "agent_coverage": {},
            "verification_result": None,
        }
        result = score_specialist("MissingAgent", signals)
        # Empty response causes entity=0.0, fidelity=0.0, quality=0.0
        assert result["entity"] == 0.0
        assert result["fidelity"] == 0.0
        assert result["quality"] == 0.0

    def test_score_specialist_values_are_rounded(self):
        """All numeric scores are rounded to 4 decimal places."""
        signals = {
            "specialist_responses": {
                "DrugSpecialist": "The efficacy of the inhibitor at 250 mg was significant.",
            },
            "sources": [
                {"source_name": "openfda", "avg_grade_score": 2.7},
            ],
            "agent_coverage": {"DrugSpecialist": 0.333},
            "verification_result": {"overall_score": 0.777},
        }
        result = score_specialist("DrugSpecialist", signals)
        for key in ("entity", "fidelity", "citation", "quality", "composite"):
            value = result[key]
            # Check that rounding to 4 decimal places is idempotent
            assert value == round(value, 4), f"{key} not rounded to 4 decimals"

    def test_score_specialist_has_citations_detection(self):
        """[[cite: marker in response sets has_citations=True for citation grader."""
        # Without verification result, has_citations=True -> 1.0, False -> 0.5
        signals_with_cite = {
            "specialist_responses": {"TestAgent": "Result is positive [[cite:1]]."},
            "sources": [],
            "agent_coverage": {},
            "verification_result": None,
        }
        signals_no_cite = {
            "specialist_responses": {"TestAgent": "Result is positive."},
            "sources": [],
            "agent_coverage": {},
            "verification_result": None,
        }
        result_cite = score_specialist("TestAgent", signals_with_cite)
        result_no_cite = score_specialist("TestAgent", signals_no_cite)
        assert result_cite["citation"] == 1.0
        assert result_no_cite["citation"] == 0.5

    def test_score_specialist_empty_signals(self):
        """Completely empty signals dict -> all zeros except citation fallback."""
        result = score_specialist("AnyAgent", {})
        assert result["entity"] == 0.0
        assert result["fidelity"] == 0.0
        assert result["quality"] == 0.0
        assert result["citation"] == 0.5  # Fallback: no verification, no citations
        assert result["llm_judge"] is None
