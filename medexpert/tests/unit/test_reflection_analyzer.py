"""Tests for ReflectionAnalyzerTool — multi-hop gap analysis."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lifesci_tools.reflection_analyzer import (
    ReflectionAnalyzerTool,
    _find_contradictions,
    _find_missing_perspectives,
    _identify_gaps,
)


@pytest.fixture
def tool():
    """Tool without model config — keyword heuristics only."""
    return ReflectionAnalyzerTool()


@pytest.fixture
def llm_tool():
    """Tool with model config — LLM reflection enabled."""
    return ReflectionAnalyzerTool(tool_config={
        "model": "openai/gemini-2.5-flash-001",
        "temperature": 0.2,
    })


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.session = MagicMock()
    mock.session.id = "test-session-456"
    return mock


def _mock_llm_response(result_dict: dict):
    """Build a mock litellm acompletion response."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json.dumps(result_dict)
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 200
    mock_resp.usage.completion_tokens = 150
    return mock_resp


# ── _find_contradictions ──────────────────────────────────────


def test_contradiction_detected():
    evidence = [
        {"title": "Study A", "snippet": "The treatment was effective and safe."},
        {"title": "Study B", "snippet": "The treatment was found to be ineffective."},
    ]
    contradictions = _find_contradictions(evidence)
    assert len(contradictions) >= 1
    assert "effective vs ineffective" in contradictions[0]["signal"]


def test_no_contradiction_when_agreeing():
    evidence = [
        {"title": "Study A", "snippet": "Metformin is effective for diabetes."},
        {"title": "Study B", "snippet": "Metformin shows strong efficacy in glucose control."},
    ]
    contradictions = _find_contradictions(evidence)
    assert len(contradictions) == 0


def test_multiple_contradictions():
    evidence = [
        {"title": "Study A", "snippet": "The drug is safe and shows increase in survival."},
        {"title": "Study B", "snippet": "The drug is unsafe and shows decrease in survival."},
    ]
    contradictions = _find_contradictions(evidence)
    assert len(contradictions) >= 1


# ── _find_missing_perspectives ────────────────────────────────


def test_all_perspectives_covered():
    evidence = [
        {"title": "E1", "snippet": "Drug efficacy and safety profile with adverse reactions."},
        {"title": "E2", "snippet": "Cost economic value analysis of treatment."},
        {"title": "E3", "snippet": "Patient quality of life and survival outcomes."},
        {"title": "E4", "snippet": "Mechanism pathway receptor binding target."},
        {"title": "E5", "snippet": "Elderly pediatric subgroup analysis."},
        {"title": "E6", "snippet": "Long-term chronic follow-up durability results."},
        {"title": "E7", "snippet": "Guideline recommendation standard of care consensus."},
    ]
    missing = _find_missing_perspectives(evidence)
    assert len(missing) == 0


def test_missing_perspectives_detected():
    evidence = [
        {"title": "E1", "snippet": "Drug efficacy outcomes results."},
    ]
    missing = _find_missing_perspectives(evidence)
    assert len(missing) >= 3  # Many perspectives missing
    assert "cost_effectiveness" in missing
    assert "mechanism_of_action" in missing


# ── _identify_gaps ────────────────────────────────────────────


def test_gap_limited_evidence():
    question = "What treatments exist for diabetes?"
    evidence = [
        {"title": "One study", "snippet": "Metformin works."},
    ]
    gaps = _identify_gaps(question, evidence)
    assert any("fewer than 3" in g.lower() for g in gaps)


def test_gap_single_domain():
    question = "What treatments exist?"
    evidence = [
        {"source": "pubmed", "title": "Study 1", "snippet": "Result 1."},
        {"source": "pubmed", "title": "Study 2", "snippet": "Result 2."},
    ]
    gaps = _identify_gaps(question, evidence)
    assert any("single source" in g.lower() for g in gaps)


# ── full tool tests ───────────────────────────────────────────


async def test_basic_analysis(tool, ctx):
    evidence = [
        {"title": "Metformin Study", "snippet": "Metformin reduces HbA1c effectively."},
        {"title": "Safety Review", "snippet": "Metformin has a good safety profile."},
    ]
    result = await tool._run_async_impl(
        {
            "question": "What are the effects of metformin?",
            "evidence_json": json.dumps(evidence),
        },
        ctx,
    )
    assert "gaps" in result
    assert "contradictions" in result
    assert "missing_perspectives" in result
    assert "follow_up_queries" in result
    assert result["evidence_count"] == 2


async def test_deep_analysis_includes_contradictions(tool, ctx):
    evidence = [
        {"title": "Study A", "snippet": "Treatment is effective."},
        {"title": "Study B", "snippet": "Treatment is ineffective."},
    ]
    result = await tool._run_async_impl(
        {
            "question": "Is the treatment effective?",
            "evidence_json": json.dumps(evidence),
            "analysis_depth": "deep",
        },
        ctx,
    )
    assert len(result["contradictions"]) >= 1


async def test_shallow_analysis_skips_contradictions(tool, ctx):
    evidence = [
        {"title": "Study A", "snippet": "Treatment is effective."},
        {"title": "Study B", "snippet": "Treatment is ineffective."},
    ]
    result = await tool._run_async_impl(
        {
            "question": "Is the treatment effective?",
            "evidence_json": json.dumps(evidence),
            "analysis_depth": "shallow",
        },
        ctx,
    )
    assert result["contradictions"] == []
    assert result["analysis_depth"] == "shallow"


async def test_follow_up_queries_generated(tool, ctx):
    evidence = [
        {"title": "Basic Study", "snippet": "Some results about treatment."},
    ]
    result = await tool._run_async_impl(
        {
            "question": "What is the comprehensive profile of drug X?",
            "evidence_json": json.dumps(evidence),
        },
        ctx,
    )
    assert len(result["follow_up_queries"]) >= 1
    assert "target_agent" in result["follow_up_queries"][0]


async def test_empty_question_error(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "", "evidence_json": "[]"}, ctx
    )
    assert "error" in result


async def test_invalid_evidence_json_error(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "Test?", "evidence_json": "not json"}, ctx
    )
    assert "error" in result


async def test_evidence_not_array_error(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "Test?", "evidence_json": '{"key": "val"}'}, ctx
    )
    assert "error" in result


async def test_empty_evidence_identifies_gaps(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "What treatments exist for cancer?", "evidence_json": "[]"}, ctx
    )
    assert result["evidence_count"] == 0
    assert len(result["missing_perspectives"]) >= 1


# ═══════════════════════════════════════════════════════════════
# LLM reflection tests
# ═══════════════════════════════════════════════════════════════


@patch("lifesci_tools.reflection_analyzer._litellm_acompletion")
async def test_llm_detects_nuanced_contradiction(mock_llm, llm_tool, ctx):
    """LLM detects magnitude difference that keyword matching cannot."""
    mock_llm.return_value = _mock_llm_response({
        "contradictions": [
            {
                "claim_a": "Drug X shows 30% efficacy",
                "source_a": "Study A",
                "claim_b": "Drug X shows 80% efficacy",
                "source_b": "Study B",
                "nature": "magnitude_difference",
                "significance": "high",
                "explanation": "Same endpoint, vastly different efficacy rates",
            }
        ],
        "gaps": [
            {"description": "No long-term safety data", "importance": "critical", "what_would_fill_it": "5-year follow-up study"}
        ],
        "limitations": ["Both studies had small sample sizes"],
        "confidence_statement": "Low confidence due to conflicting results",
        "strongest_evidence": "Both studies agree Drug X has some effect",
        "weakest_evidence": "Magnitude of effect is highly uncertain",
    })
    evidence = [
        {"title": "Study A", "snippet": "Drug X shows 30% efficacy in 50 patients."},
        {"title": "Study B", "snippet": "Drug X shows 80% efficacy in 45 patients."},
    ]
    result = await llm_tool._run_async_impl(
        {"question": "How effective is Drug X?", "evidence_json": json.dumps(evidence)},
        ctx,
    )
    assert result["method"] == "llm"
    assert len(result["contradictions"]) == 1
    assert result["contradictions"][0]["nature"] == "magnitude_difference"
    assert result["confidence_statement"] == "Low confidence due to conflicting results"
    assert result["strongest_evidence"] != ""
    assert result["weakest_evidence"] != ""
    assert len(result["limitations"]) >= 1
    mock_llm.assert_called_once()


@patch("lifesci_tools.reflection_analyzer._litellm_acompletion")
async def test_llm_gaps_merged_with_keyword_gaps(mock_llm, llm_tool, ctx):
    """LLM gaps are merged with keyword-detected gaps (e.g., 'fewer than 3')."""
    mock_llm.return_value = _mock_llm_response({
        "contradictions": [],
        "gaps": [
            {"description": "No cost-effectiveness analysis", "importance": "important", "what_would_fill_it": "Economic study"}
        ],
        "limitations": [],
        "confidence_statement": "Moderate confidence",
        "strongest_evidence": "Treatment works",
        "weakest_evidence": "Unknown cost",
    })
    evidence = [
        {"title": "Study A", "snippet": "Treatment is effective."},
    ]
    result = await llm_tool._run_async_impl(
        {"question": "What is the full profile of treatment Y?", "evidence_json": json.dumps(evidence)},
        ctx,
    )
    # LLM gap + keyword "fewer than 3" gap should both be present
    gap_text = " ".join(result["gaps"]).lower()
    assert "cost" in gap_text
    assert "fewer than 3" in gap_text


@patch("lifesci_tools.reflection_analyzer._litellm_acompletion")
async def test_llm_failure_falls_back_to_keyword(mock_llm, llm_tool, ctx):
    """When LLM fails, tool falls back to keyword heuristics."""
    mock_llm.side_effect = Exception("LLM timeout")
    evidence = [
        {"title": "Study A", "snippet": "Treatment is effective."},
        {"title": "Study B", "snippet": "Treatment is ineffective."},
    ]
    result = await llm_tool._run_async_impl(
        {"question": "Is treatment effective?", "evidence_json": json.dumps(evidence)},
        ctx,
    )
    assert result["method"] == "keyword"
    assert len(result["contradictions"]) >= 1  # Keyword still detects effective/ineffective


@patch("lifesci_tools.reflection_analyzer._litellm_acompletion")
async def test_llm_markdown_fence_stripping(mock_llm, llm_tool, ctx):
    """LLM response wrapped in markdown fences is parsed correctly."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = (
        "```json\n"
        '{"contradictions": [], "gaps": [], "limitations": [], '
        '"confidence_statement": "High", "strongest_evidence": "X", "weakest_evidence": "Y"}'
        "\n```"
    )
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    mock_llm.return_value = mock_resp

    evidence = [
        {"title": "Study", "snippet": "Good results."},
        {"title": "Study 2", "snippet": "Also good."},
        {"title": "Study 3", "snippet": "Confirmed."},
    ]
    result = await llm_tool._run_async_impl(
        {"question": "Is it effective?", "evidence_json": json.dumps(evidence)},
        ctx,
    )
    assert result["method"] == "llm"
    assert result["confidence_statement"] == "High"


@patch("lifesci_tools.reflection_analyzer._litellm_acompletion")
async def test_llm_malformed_json_falls_back(mock_llm, llm_tool, ctx):
    """Malformed JSON from LLM triggers keyword fallback."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "This is not JSON"
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 50
    mock_resp.usage.completion_tokens = 10
    mock_llm.return_value = mock_resp

    evidence = [
        {"title": "Study", "snippet": "Results here."},
    ]
    result = await llm_tool._run_async_impl(
        {"question": "Test question?", "evidence_json": json.dumps(evidence)},
        ctx,
    )
    assert result["method"] == "keyword"


@patch("lifesci_tools.reflection_analyzer._litellm_acompletion")
async def test_shallow_analysis_skips_llm(mock_llm, llm_tool, ctx):
    """Shallow analysis does not call LLM even when model is configured."""
    evidence = [
        {"title": "Study A", "snippet": "Treatment is effective."},
        {"title": "Study B", "snippet": "Treatment is ineffective."},
    ]
    result = await llm_tool._run_async_impl(
        {
            "question": "Is treatment effective?",
            "evidence_json": json.dumps(evidence),
            "analysis_depth": "shallow",
        },
        ctx,
    )
    assert result["contradictions"] == []  # shallow skips contradictions
    assert result["method"] == "keyword"
    mock_llm.assert_not_called()


async def test_no_model_config_uses_keyword(tool, ctx):
    """Tool without model config uses keyword heuristics."""
    evidence = [
        {"title": "Study A", "snippet": "Treatment is effective."},
        {"title": "Study B", "snippet": "Treatment is ineffective."},
    ]
    result = await tool._run_async_impl(
        {"question": "Is treatment effective?", "evidence_json": json.dumps(evidence)},
        ctx,
    )
    assert result["method"] == "keyword"
    assert len(result["contradictions"]) >= 1


def test_init_with_dict_model():
    """YAML anchor expansion: model config as dict."""
    tool = ReflectionAnalyzerTool(tool_config={
        "model": {
            "model": "vertex_ai/gemini-2.5-flash",
            "vertex_project": "my-project",
            "vertex_location": "us-central1",
        }
    })
    assert tool.model == "vertex_ai/gemini-2.5-flash"
    assert tool._vertex_kwargs == {"vertex_project": "my-project", "vertex_location": "us-central1"}


def test_init_without_model():
    """No model config → empty model string."""
    tool = ReflectionAnalyzerTool()
    assert tool.model == ""
