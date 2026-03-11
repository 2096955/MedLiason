"""Tests for the QueryDecomposerTool — question decomposition + domain routing."""

import json
from unittest.mock import MagicMock, patch

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


def test_route_haemophilia_erpc_gets_multiple_specialists():
    """ERPC + haemophilia should route to Literature + ClinicalTrials + Drug."""
    routes = _route_question(
        "When considering an ERPC for a patient with haemophilia, "
        "which plan for anesthesia would likely be safest?"
    )
    agents = {r["agent"] for r in routes}
    assert "LiteratureSpecialist" in agents
    assert len(agents) >= 2, f"Expected >= 2 specialists, got {agents}"
    assert "ClinicalTrialsSpecialist" in agents or "DrugSpecialist" in agents


def test_route_coagulation_disorder_gets_drug_specialist():
    """Coagulation/bleeding disorder questions need DrugSpecialist for factor replacement."""
    routes = _route_question(
        "What factor replacement protocol is recommended for hemophilia A patients undergoing surgery?"
    )
    agents = {r["agent"] for r in routes}
    assert "DrugSpecialist" in agents


def test_split_preserves_relative_clause_with_which():
    """Comma + 'which' in a context clause should NOT split the question.

    The first fragment ('When considering an ERPC for a patient with
    haemophilia') starts with a context opener and has no question word,
    so the context-clause guard prevents splitting.
    """
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
    # Both fragments are substantial and the first starts with a question word
    assert len(parts) >= 2


def test_split_context_clause_guard_independent():
    """Context-clause guard prevents split even when both fragments are substantial.

    This question is >100 chars and both fragments are >= 30 chars, but the
    first fragment is a context clause (starts with 'Given' and has no question
    word), so the split must be suppressed.
    """
    q = (
        "Given the high prevalence of antibiotic resistance in hospital settings, "
        "what infection control strategies have proven most effective in reducing MRSA transmission rates?"
    )
    assert len(q) > 100  # sanity-check: length gate passes
    parts = _split_question(q, 5)
    assert len(parts) == 1
    assert "antibiotic resistance" in parts[0]
    assert "MRSA" in parts[0]


def test_split_genuine_compound_in_100_150_range():
    """A genuine compound question between 100-150 chars that SHOULD split.

    Both fragments are substantial and the first fragment contains a question
    word ('What'), so it is NOT a context clause.
    """
    q = (
        "What are the primary risk factors for pancreatic cancer in adults, "
        "how effective is early screening for high-risk individuals?"
    )
    length = len(q)
    assert 100 < length < 150, f"Expected 100-150 chars, got {length}"
    parts = _split_question(q, 5)
    assert len(parts) >= 2
    assert any("risk factors" in p for p in parts)
    assert any("screening" in p for p in parts)


def test_split_preserves_context_with_relative_pronoun():
    """Relative pronoun 'who' in context clause should NOT trigger question-word detection."""
    q = "In patients with severe asthma who are refractory to standard therapy, what biologic agents have demonstrated efficacy in reducing exacerbation rates?"
    parts = _split_question(q, 5)
    assert len(parts) == 1
    assert "asthma" in parts[0]
    assert "biologic" in parts[0]


# ── LLM decomposition tests ─────────────────────────────────


def _make_llm_response(content_dict):
    """Build a mock litellm response object from a dict."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(content_dict)
    resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    return resp


@pytest.mark.asyncio
async def test_llm_decompose_erpc_haemophilia():
    """LLM decomposition should route ERPC+haemophilia to multiple specialists."""
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})

    mock_response = _make_llm_response({
        "sub_questions": [{
            "question": (
                "When considering an ERPC for a patient with haemophilia, "
                "which plan for anesthesia would likely be safest?"
            ),
            "target_agent": "LiteratureSpecialist",
            "secondary_agents": ["ClinicalTrialsSpecialist", "DrugSpecialist"],
        }],
        "all_agents": [
            "LiteratureSpecialist",
            "ClinicalTrialsSpecialist",
            "DrugSpecialist",
        ],
        "reasoning": (
            "Haemophilia requires factor replacement (Drug), "
            "procedure evidence (ClinicalTrials), and guidelines (Literature)"
        ),
    })

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        return_value=mock_response,
    ):
        result = await tool._llm_decompose(
            "When considering an ERPC for a patient with haemophilia, "
            "which plan for anesthesia would likely be safest?",
            5,
        )

    assert result is not None
    assert "DrugSpecialist" in result["all_agents"]
    assert "ClinicalTrialsSpecialist" in result["all_agents"]
    assert "LiteratureSpecialist" in result["all_agents"]
    assert result["routing_method"] == "llm"
    assert result["routing_confidence"] == 0.85
    assert result["count"] == 1
    sq = result["sub_questions"][0]
    assert sq["target_agent"] == "LiteratureSpecialist"
    assert sq["domain"] == "literature"
    assert len(sq["secondary_agents"]) == 2


@pytest.mark.asyncio
async def test_llm_decompose_propagates_exception():
    """When LLM raises, _llm_decompose should propagate the exception.

    The caller (_run_async_impl) catches it and falls back to keywords.
    """
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        side_effect=Exception("LLM error"),
    ):
        with pytest.raises(Exception, match="LLM error"):
            await tool._llm_decompose("What causes diabetes?", 5)


@pytest.mark.asyncio
async def test_llm_decompose_validates_agent_names():
    """LLM decomposition should reject invalid agent names."""
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})

    mock_response = _make_llm_response({
        "sub_questions": [{
            "question": "test question",
            "target_agent": "FakeAgent",
            "secondary_agents": ["LiteratureSpecialist"],
        }],
        "all_agents": ["FakeAgent", "LiteratureSpecialist"],
    })

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        return_value=mock_response,
    ):
        result = await tool._llm_decompose("test question", 5)

    # FakeAgent should be filtered out of all_agents
    assert "FakeAgent" not in result["all_agents"]
    assert "LiteratureSpecialist" in result["all_agents"]
    # target_agent should fall back to LiteratureSpecialist
    assert result["sub_questions"][0]["target_agent"] == "LiteratureSpecialist"


@pytest.mark.asyncio
async def test_llm_decompose_returns_none_on_unparseable():
    """Unparseable LLM output should return None."""
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "This is not JSON at all."
    resp.usage = MagicMock(prompt_tokens=50, completion_tokens=20, total_tokens=70)

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        return_value=resp,
    ):
        result = await tool._llm_decompose("What is aspirin?", 5)

    assert result is None


@pytest.mark.asyncio
async def test_llm_decompose_returns_none_when_all_agents_invalid():
    """If every agent name is invalid, _llm_decompose should return None."""
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})

    mock_response = _make_llm_response({
        "sub_questions": [{
            "question": "test",
            "target_agent": "BogusAgent",
            "secondary_agents": ["AnotherFake"],
        }],
        "all_agents": ["BogusAgent", "AnotherFake"],
    })

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        return_value=mock_response,
    ):
        result = await tool._llm_decompose("test", 5)

    assert result is None


@pytest.mark.asyncio
async def test_run_async_uses_llm_when_model_configured():
    """_run_async_impl should use LLM path when a model is configured."""
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})
    ctx = MagicMock()

    mock_response = _make_llm_response({
        "sub_questions": [{
            "question": "What are the side effects of metformin?",
            "target_agent": "DrugSpecialist",
            "secondary_agents": ["LiteratureSpecialist"],
        }],
        "all_agents": ["DrugSpecialist", "LiteratureSpecialist"],
        "reasoning": "Drug safety question",
    })

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        return_value=mock_response,
    ):
        result = await tool._run_async_impl(
            {"question": "What are the side effects of metformin?"}, ctx
        )

    assert result["routing_method"] == "llm"
    assert "DrugSpecialist" in result["all_agents"]


@pytest.mark.asyncio
async def test_run_async_falls_back_to_keyword_on_llm_failure():
    """When LLM fails, _run_async_impl should fall back to keyword routing."""
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})
    ctx = MagicMock()

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        side_effect=Exception("LLM unavailable"),
    ):
        result = await tool._run_async_impl(
            {"question": "What are the side effects of metformin?"}, ctx
        )

    assert result["routing_method"] == "keyword"
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_run_async_keyword_when_no_model():
    """Without a model configured, should use keyword routing directly."""
    tool = QueryDecomposerTool()  # No tool_config → no model
    ctx = MagicMock()

    result = await tool._run_async_impl(
        {"question": "What are the side effects of metformin?"}, ctx
    )

    assert result["routing_method"] == "keyword"


@pytest.mark.asyncio
async def test_llm_decompose_respects_max_sub():
    """LLM decomposition should respect max_sub_questions limit."""
    tool = QueryDecomposerTool(tool_config={"model": "test-model"})

    mock_response = _make_llm_response({
        "sub_questions": [
            {"question": "Q1", "target_agent": "LiteratureSpecialist", "secondary_agents": []},
            {"question": "Q2", "target_agent": "DrugSpecialist", "secondary_agents": []},
            {"question": "Q3", "target_agent": "GenomicsSpecialist", "secondary_agents": []},
        ],
        "all_agents": ["LiteratureSpecialist", "DrugSpecialist", "GenomicsSpecialist"],
    })

    with patch(
        "lifesci_tools.query_decomposer._litellm_acompletion",
        return_value=mock_response,
    ):
        result = await tool._llm_decompose("complex multi-part question", 2)

    assert result is not None
    assert len(result["sub_questions"]) <= 2


def test_agent_to_domain_mapping():
    """_agent_to_domain should map known agents to their domain keys."""
    assert QueryDecomposerTool._agent_to_domain("LiteratureSpecialist") == "literature"
    assert QueryDecomposerTool._agent_to_domain("DrugSpecialist") == "drugs"
    assert QueryDecomposerTool._agent_to_domain("GenomicsSpecialist") == "genomics"
    assert QueryDecomposerTool._agent_to_domain("UnknownAgent") == "literature"


def test_init_with_model_string():
    """Constructor should accept a plain model string."""
    tool = QueryDecomposerTool(tool_config={"model": "vertex_ai/gemini-2.5-flash"})
    assert tool._model == "vertex_ai/gemini-2.5-flash"
    assert tool._vertex_kwargs == {}
    assert tool._temperature == 0.1


def test_init_with_model_dict():
    """Constructor should accept a model dict (from YAML anchor expansion)."""
    tool = QueryDecomposerTool(tool_config={
        "model": {
            "model": "vertex_ai/gemini-2.5-flash",
            "vertex_project": "my-project",
            "vertex_location": "us-central1",
        },
        "temperature": 0.2,
    })
    assert tool._model == "vertex_ai/gemini-2.5-flash"
    assert tool._vertex_kwargs == {
        "vertex_project": "my-project",
        "vertex_location": "us-central1",
    }
    assert tool._temperature == 0.2


def test_init_no_config():
    """Constructor with no config should set empty model (keyword-only mode)."""
    tool = QueryDecomposerTool()
    assert tool._model == ""
    assert tool._vertex_kwargs == {}
