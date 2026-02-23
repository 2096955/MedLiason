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


# ── _route_question unit tests ────────────────────────────────


def test_route_pubmed_question():
    domain, agent, conf = _route_question(
        "What does the PubMed literature say about metformin?"
    )
    assert domain == "literature"
    assert agent == "LiteratureSpecialist"
    assert conf > 0.2


def test_route_clinical_trials_question():
    domain, agent, conf = _route_question(
        "What clinical trials are recruiting for phase 3 randomized studies?"
    )
    assert domain == "clinical_trials"
    assert agent == "ClinicalTrialsSpecialist"


def test_route_drug_question():
    domain, agent, conf = _route_question(
        "What are the adverse events and side effects of this medication?"
    )
    assert domain == "drugs"
    assert agent == "DrugSpecialist"


def test_route_regulatory_question():
    domain, agent, conf = _route_question(
        "What 510k clearance has the FDA granted for this device?"
    )
    assert domain == "regulatory"
    assert agent == "RegulatorySpecialist"


def test_route_epidemiology_question():
    domain, agent, conf = _route_question(
        "What is the mortality and prevalence of this disease according to CDC surveillance?"
    )
    assert domain == "epidemiology"
    assert agent == "EpidemiologySpecialist"


def test_route_genomics_question():
    domain, agent, conf = _route_question(
        "What genetic variants and mutations are pathogenic in ClinVar?"
    )
    assert domain == "genomics"
    assert agent == "GenomicsSpecialist"


def test_route_environmental_question():
    domain, agent, conf = _route_question(
        "What is the air quality and pollution level in this area?"
    )
    assert domain == "environmental"
    assert agent == "EnvironmentalSpecialist"


def test_route_provider_question():
    domain, agent, conf = _route_question(
        "What providers and physicians have CMS open payments?"
    )
    assert domain == "provider_intel"
    assert agent == "ProviderIntelSpecialist"


def test_route_ambiguous_defaults_to_literature():
    domain, agent, conf = _route_question("Tell me about aspirin")
    assert domain == "literature"
    assert agent == "LiteratureSpecialist"
    assert conf == 0.2


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
    assert "question" in result["sub_questions"][0]
    assert "domain" in result["sub_questions"][0]
    assert "target_agent" in result["sub_questions"][0]


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
