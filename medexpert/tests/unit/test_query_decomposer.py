"""Tests for the QueryDecomposerTool — question decomposition + domain routing."""

from unittest.mock import MagicMock

import pytest

from lifesci_tools.query_decomposer import (
    QueryDecomposerTool,
    _route_question,
    _split_question,
)


@pytest.fixture
def tool():
    return QueryDecomposerTool()


@pytest.fixture
def ctx():
    return MagicMock()


# ── _route_question unit tests (now returns list of dicts) ───


def test_route_pubmed_question():
    routes = _route_question(
        "What does the PubMed literature say about metformin?"
    )
    assert routes[0]["domain"] == "literature"
    assert routes[0]["agent"] == "LiteratureSpecialist"
    assert routes[0]["role"] == "primary"
    assert routes[0]["confidence"] > 0.2


def test_route_clinical_trials_question():
    routes = _route_question(
        "What clinical trials are recruiting for phase 3 randomized studies?"
    )
    assert routes[0]["domain"] == "clinical_trials"
    assert routes[0]["agent"] == "ClinicalTrialsSpecialist"


def test_route_drug_question():
    routes = _route_question(
        "What are the adverse events and side effects of this medication?"
    )
    assert routes[0]["domain"] == "drugs"
    assert routes[0]["agent"] == "DrugSpecialist"


def test_route_regulatory_question():
    routes = _route_question(
        "What 510k clearance has the FDA granted for this device?"
    )
    assert routes[0]["domain"] == "regulatory"
    assert routes[0]["agent"] == "RegulatorySpecialist"


def test_route_epidemiology_question():
    routes = _route_question(
        "What is the mortality and prevalence of this disease according to CDC surveillance?"
    )
    assert routes[0]["domain"] == "epidemiology"
    assert routes[0]["agent"] == "EpidemiologySpecialist"


def test_route_genomics_question():
    routes = _route_question(
        "What genetic variants and mutations are pathogenic in ClinVar?"
    )
    assert routes[0]["domain"] == "genomics"
    assert routes[0]["agent"] == "GenomicsSpecialist"


def test_route_environmental_question():
    routes = _route_question(
        "What is the air quality and pollution level in this area?"
    )
    assert routes[0]["domain"] == "environmental"
    assert routes[0]["agent"] == "EnvironmentalSpecialist"


def test_route_provider_question():
    routes = _route_question(
        "What providers and physicians have CMS open payments?"
    )
    assert routes[0]["domain"] == "provider_intel"
    assert routes[0]["agent"] == "ProviderIntelSpecialist"


def test_route_ambiguous_defaults_to_literature():
    # Query with no domain-specific keywords → falls to literature default
    routes = _route_question("Hello there.")
    assert routes[0]["domain"] == "literature"
    assert routes[0]["agent"] == "LiteratureSpecialist"
    assert routes[0]["confidence"] == 0.2


def test_route_tell_me_about_drug_routes_to_drugs():
    """'Tell me about aspirin' should route to drugs (has 'tell me about' keyword)."""
    routes = _route_question("Tell me about aspirin")
    assert routes[0]["domain"] == "drugs"
    assert routes[0]["agent"] == "DrugSpecialist"


def test_route_drug_alternatives_not_genomics():
    """'alternatives' contains substring 'rna' — must NOT route to genomics.

    Regression test: short keywords like 'rna' must use word-boundary matching
    to prevent false positives from substrings.
    """
    routes = _route_question(
        "What alternatives are there to betamethasone dipropionate (Eleuphrat)?"
    )
    assert routes[0]["domain"] == "drugs", (
        f"Expected drugs as primary, got {routes[0]['domain']}"
    )
    # Genomics should NOT be primary (it was before the word-boundary fix)
    assert routes[0]["agent"] != "GenomicsSpecialist"


def test_route_returns_secondary_agents():
    """A treatment question should route to clinical_trials + drug as secondary."""
    routes = _route_question(
        "What are the treatments for endometriosis?"
    )
    agents = {r["agent"] for r in routes}
    # Should have at least 2 agents (primary + secondary)
    assert len(routes) >= 2
    # Literature should be one of them since it always gets included
    assert "LiteratureSpecialist" in agents


def test_route_always_includes_literature():
    """Even when primary is non-literature, LiteratureSpecialist appears as secondary."""
    routes = _route_question(
        "What genetic variants cause hereditary breast cancer?"
    )
    assert routes[0]["agent"] == "GenomicsSpecialist"
    agents = {r["agent"] for r in routes}
    assert "LiteratureSpecialist" in agents


def test_route_prevalence_hits_epidemiology():
    """Clinical terms like 'prevalence' and 'risk factor' should route to epidemiology."""
    routes = _route_question(
        "What is the prevalence of diabetes and what are the risk factors?"
    )
    agents = {r["agent"] for r in routes}
    assert "EpidemiologySpecialist" in agents


# ── _split_question unit tests ────────────────────────────────


def test_split_simple_question():
    parts = _split_question("What causes diabetes?", 5)
    assert len(parts) == 1
    assert parts[0].endswith("?")


def test_split_conjunction():
    parts = _split_question(
        "What are the treatments for diabetes and what clinical trials are available?",
        5,
    )
    assert len(parts) >= 2


def test_split_semicolons():
    parts = _split_question(
        "What is the drug safety profile; what do clinical trials show; what is the epidemiology?",
        5,
    )
    assert len(parts) == 3


def test_split_respects_max():
    parts = _split_question(
        "A; B; C; D; E; F; G",
        3,
    )
    assert len(parts) <= 3


def test_split_adds_question_mark():
    parts = _split_question("What is X; What is Y", 5)
    for p in parts:
        assert p.endswith("?")


# ── full tool integration tests ───────────────────────────────


async def test_simple_question(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "What are the side effects of metformin?"}, ctx
    )
    assert result["original_question"] == "What are the side effects of metformin?"
    assert result["count"] >= 1
    assert len(result["sub_questions"]) >= 1
    sq = result["sub_questions"][0]
    assert "question" in sq
    assert "domain" in sq
    assert "target_agent" in sq
    assert "secondary_agents" in sq


async def test_compound_question(tool, ctx):
    result = await tool._run_async_impl(
        {
            "question": (
                "What does the literature say about GLP-1 agonists and "
                "what clinical trials are recruiting for semaglutide?"
            )
        },
        ctx,
    )
    assert result["count"] >= 2
    agents_used = {sq["target_agent"] for sq in result["sub_questions"]}
    assert len(agents_used) >= 1


async def test_multi_domain_question(tool, ctx):
    result = await tool._run_async_impl(
        {
            "question": (
                "What is the FDA regulatory status of device X; "
                "what adverse events has OpenFDA recorded; "
                "what are the CDC mortality statistics?"
            )
        },
        ctx,
    )
    assert result["count"] == 3


async def test_all_agents_field(tool, ctx):
    """The all_agents field should list every unique agent across all routes."""
    result = await tool._run_async_impl(
        {"question": "What are the treatments for endometriosis?"}, ctx
    )
    assert "all_agents" in result
    assert isinstance(result["all_agents"], list)
    assert len(result["all_agents"]) >= 2


async def test_secondary_agents_in_sub_questions(tool, ctx):
    """Each sub-question should have a secondary_agents list."""
    result = await tool._run_async_impl(
        {"question": "What is the prevalence and treatment of type 2 diabetes?"}, ctx
    )
    for sq in result["sub_questions"]:
        assert "secondary_agents" in sq
        assert isinstance(sq["secondary_agents"], list)


async def test_max_sub_questions_default(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "A; B; C; D; E; F; G; H"},
        ctx,
    )
    assert result["count"] <= 5


async def test_max_sub_questions_custom(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "A; B; C; D; E; F; G", "max_sub_questions": 3},
        ctx,
    )
    assert result["count"] <= 3


async def test_empty_question_returns_error(tool, ctx):
    result = await tool._run_async_impl({"question": ""}, ctx)
    assert "error" in result


async def test_routing_confidence_returned(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "What PubMed studies exist for metformin?"}, ctx
    )
    assert "routing_confidence" in result
    assert 0 <= result["routing_confidence"] <= 1


async def test_priority_assigned_sequentially(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "What is A; What is B; What is C"}, ctx
    )
    priorities = [sq["priority"] for sq in result["sub_questions"]]
    assert priorities == list(range(1, len(priorities) + 1))


async def test_max_sub_questions_clamp_high(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "A; B; C", "max_sub_questions": 100},
        ctx,
    )
    assert result["count"] <= 10


async def test_max_sub_questions_clamp_low(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "What is X?", "max_sub_questions": 0},
        ctx,
    )
    assert result["count"] >= 1


def test_split_preserves_relative_clause_with_which():
    """Comma + 'which' in a relative clause should NOT split the question."""
    q = "When considering an ERPC for a patient with haemophilia, which plan for anesthesia would likely be safest?"
    parts = _split_question(q, 5)
    assert len(parts) == 1
    assert "haemophilia" in parts[0]
    assert "anesthesia" in parts[0]


def test_split_preserves_relative_clause_with_what():
    """Comma + 'what' in a dependent clause should NOT split."""
    q = "For patients with chronic kidney disease, what medication adjustments are recommended for metformin?"
    parts = _split_question(q, 5)
    assert len(parts) == 1
    assert "kidney" in parts[0]
    assert "metformin" in parts[0]


def test_split_does_split_genuine_long_multi_topic():
    """Genuine long multi-topic questions with comma+question word SHOULD still split if both parts are substantial."""
    q = "What are the cardiovascular side effects of long-term statin therapy in elderly patients with diabetes, what alternative lipid-lowering approaches exist for patients who cannot tolerate statins due to myopathy?"
    parts = _split_question(q, 5)
    # This is >150 chars and both fragments are >40 chars
    assert len(parts) >= 2
