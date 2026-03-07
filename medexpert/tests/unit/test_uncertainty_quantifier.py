"""Tests for UncertaintyQuantifierTool — per-claim confidence profiling."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lifesci_tools.uncertainty_quantifier import UncertaintyQuantifierTool


@pytest.fixture
def tool():
    """Tool without model config — heuristic only."""
    return UncertaintyQuantifierTool()


@pytest.fixture
def llm_tool():
    """Tool with model config — LLM uncertainty enabled."""
    return UncertaintyQuantifierTool(tool_config={
        "model": "openai/gemini-2.5-flash-001",
        "temperature": 0.1,
    })


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.session = MagicMock()
    mock.session.id = "test-session-uq"
    return mock


@pytest.fixture
def high_grade_evidence():
    return [
        {"title": "Meta-analysis of metformin", "snippet": "Strong evidence.", "grade": "High"},
        {"title": "Large RCT", "snippet": "Confirms findings.", "grade": "High"},
        {"title": "Systematic review", "snippet": "Consistent results.", "grade": "High"},
    ]


@pytest.fixture
def mixed_grade_evidence():
    return [
        {"title": "RCT", "snippet": "Positive results.", "grade": "Moderate"},
        {"title": "Case report", "snippet": "Limited data.", "grade": "Very Low"},
    ]


def _mock_llm_response(result_dict: dict):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json.dumps(result_dict)
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 200
    mock_resp.usage.completion_tokens = 150
    return mock_resp


# ── heuristic tests ──────────────────────────────────────────


async def test_heuristic_high_confidence(tool, ctx, high_grade_evidence):
    result = await tool._run_async_impl(
        {"question": "Is metformin effective?", "evidence_json": json.dumps(high_grade_evidence)},
        ctx,
    )
    assert result["method"] == "heuristic"
    assert result["confidence_level"] == "high"
    assert result["overall_confidence"] > 0.7


async def test_heuristic_low_confidence(tool, ctx, mixed_grade_evidence):
    result = await tool._run_async_impl(
        {"question": "Is treatment X effective?", "evidence_json": json.dumps(mixed_grade_evidence)},
        ctx,
    )
    assert result["method"] == "heuristic"
    assert result["overall_confidence"] < 0.7


async def test_heuristic_no_evidence(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "Is treatment X effective?", "evidence_json": "[]"},
        ctx,
    )
    assert result["confidence_level"] == "insufficient"
    assert result["overall_confidence"] == 0.0


async def test_heuristic_returns_empty_per_claim(tool, ctx, high_grade_evidence):
    """Heuristic path returns empty per_claim_confidence and what_would_change_this."""
    result = await tool._run_async_impl(
        {"question": "Q?", "evidence_json": json.dumps(high_grade_evidence)}, ctx
    )
    assert result["per_claim_confidence"] == []
    assert result["what_would_change_this"] == []


async def test_heuristic_source_bonus(tool, ctx):
    """More sources increase confidence slightly."""
    one = [{"title": "Study", "snippet": "Result.", "grade": "Moderate"}]
    five = [{"title": f"Study {i}", "snippet": "Result.", "grade": "Moderate"} for i in range(5)]

    r1 = await tool._run_async_impl(
        {"question": "Q?", "evidence_json": json.dumps(one)}, ctx
    )
    r5 = await tool._run_async_impl(
        {"question": "Q?", "evidence_json": json.dumps(five)}, ctx
    )
    assert r5["overall_confidence"] > r1["overall_confidence"]


# ── error handling ────────────────────────────────────────────


async def test_empty_question_error(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "", "evidence_json": "[]"}, ctx
    )
    assert "error" in result


async def test_invalid_json_error(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "Q?", "evidence_json": "not json"}, ctx
    )
    assert "error" in result


async def test_non_array_evidence_error(tool, ctx):
    result = await tool._run_async_impl(
        {"question": "Q?", "evidence_json": '{"key": "val"}'}, ctx
    )
    assert "error" in result


# ── LLM tests ────────────────────────────────────────────────


@patch("lifesci_tools.uncertainty_quantifier._litellm_acompletion")
async def test_llm_per_claim_confidence(mock_llm, llm_tool, ctx, high_grade_evidence):
    """LLM produces per-claim confidence assessment."""
    mock_llm.return_value = _mock_llm_response({
        "overall_confidence": 0.85,
        "confidence_level": "high",
        "rationale": "Multiple high-quality sources agree on efficacy.",
        "per_claim_confidence": [
            {
                "claim": "Metformin reduces HbA1c by 1.0-1.5%",
                "confidence": 0.9,
                "basis": "meta_analysis",
                "caveats": ["Based primarily on Western populations"],
            },
            {
                "claim": "SGLT2 inhibitors provide cardiovascular benefit",
                "confidence": 0.75,
                "basis": "multiple_rcts",
                "caveats": ["Long-term data still emerging"],
            },
        ],
        "what_would_change_this": [
            "A large RCT showing no cardiovascular benefit for SGLT2 inhibitors",
        ],
    })
    result = await llm_tool._run_async_impl(
        {"question": "Is metformin effective?", "evidence_json": json.dumps(high_grade_evidence)},
        ctx,
    )
    assert result["method"] == "llm"
    assert result["overall_confidence"] == 0.85
    assert result["confidence_level"] == "high"
    assert len(result["per_claim_confidence"]) == 2
    assert result["per_claim_confidence"][0]["basis"] == "meta_analysis"
    assert len(result["what_would_change_this"]) >= 1
    mock_llm.assert_called_once()


@patch("lifesci_tools.uncertainty_quantifier._litellm_acompletion")
async def test_llm_failure_falls_back(mock_llm, llm_tool, ctx, high_grade_evidence):
    """LLM failure → heuristic fallback."""
    mock_llm.side_effect = Exception("LLM timeout")
    result = await llm_tool._run_async_impl(
        {"question": "Q?", "evidence_json": json.dumps(high_grade_evidence)},
        ctx,
    )
    assert result["method"] == "heuristic"
    assert result["confidence_level"] == "high"


@patch("lifesci_tools.uncertainty_quantifier._litellm_acompletion")
async def test_llm_markdown_fence_stripping(mock_llm, llm_tool, ctx, mixed_grade_evidence):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = (
        "```json\n"
        '{"overall_confidence": 0.5, "confidence_level": "moderate", '
        '"rationale": "Mixed evidence.", "per_claim_confidence": [], '
        '"what_would_change_this": []}'
        "\n```"
    )
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    mock_llm.return_value = mock_resp

    result = await llm_tool._run_async_impl(
        {"question": "Q?", "evidence_json": json.dumps(mixed_grade_evidence)},
        ctx,
    )
    assert result["method"] == "llm"
    assert result["overall_confidence"] == 0.5


@patch("lifesci_tools.uncertainty_quantifier._litellm_acompletion")
async def test_llm_malformed_json_falls_back(mock_llm, llm_tool, ctx, high_grade_evidence):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "Not JSON"
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 50
    mock_resp.usage.completion_tokens = 10
    mock_llm.return_value = mock_resp

    result = await llm_tool._run_async_impl(
        {"question": "Q?", "evidence_json": json.dumps(high_grade_evidence)},
        ctx,
    )
    assert result["method"] == "heuristic"


async def test_no_model_config_uses_heuristic(tool, ctx, high_grade_evidence):
    result = await tool._run_async_impl(
        {"question": "Q?", "evidence_json": json.dumps(high_grade_evidence)},
        ctx,
    )
    assert result["method"] == "heuristic"


def test_init_with_dict_model():
    tool = UncertaintyQuantifierTool(tool_config={
        "model": {"model": "vertex_ai/gemini-2.5-flash", "vertex_project": "p", "vertex_location": "l"}
    })
    assert tool.model == "vertex_ai/gemini-2.5-flash"


def test_init_without_model():
    tool = UncertaintyQuantifierTool()
    assert tool.model == ""
