"""Tests for the EvidenceGraderTool — GRADE methodology scoring."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lifesci_tools.evidence_grader import EvidenceGraderTool


@pytest.fixture
def tool():
    """Tool without model config — pure heuristic."""
    return EvidenceGraderTool()


@pytest.fixture
def llm_tool():
    """Tool with model config — LLM contextual assessment enabled."""
    return EvidenceGraderTool(tool_config={
        "model": "openai/gemini-2.5-flash-001",
        "temperature": 0.1,
    })


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.session = MagicMock()
    mock.session.id = "test-session-grader"
    return mock


def _mock_llm_response(result_dict: dict):
    """Build a mock litellm acompletion response."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json.dumps(result_dict)
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    return mock_resp


# ── study type base scores ────────────────────────────────────


async def test_meta_analysis_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "meta_analysis"}, ctx)
    assert result["score"] == 4.0
    assert result["grade"] == "High"


async def test_systematic_review_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "systematic_review"}, ctx)
    assert result["score"] == 4.0
    assert result["grade"] == "High"


async def test_rct_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "rct"}, ctx)
    assert result["score"] == 3.0
    assert result["grade"] == "High"


async def test_cohort_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "cohort"}, ctx)
    assert result["score"] == 2.0
    assert result["grade"] == "Moderate"


async def test_case_control_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "case_control"}, ctx)
    assert result["score"] == 1.0
    assert result["grade"] == "Low"


async def test_case_report_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "case_report"}, ctx)
    assert result["score"] == 0.5
    assert result["grade"] == "Very Low"


async def test_expert_opinion_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "expert_opinion"}, ctx)
    assert result["score"] == 0.25
    assert result["grade"] == "Very Low"


async def test_unknown_study_type_returns_error(tool, ctx):
    result = await tool._run_async_impl({"study_type": "not_a_type"}, ctx)
    assert "error" in result
    assert "valid_types" in result


# ── downgrade factors ─────────────────────────────────────────


async def test_single_downgrade(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "rct", "methodology_descriptors": ["no_blinding"]}, ctx
    )
    assert result["score"] == 2.0  # 3.0 - 1.0
    assert result["grade"] == "Moderate"
    assert len(result["factors_applied"]) == 1
    assert result["factors_applied"][0]["type"] == "downgrade"


async def test_multiple_downgrades(tool, ctx):
    result = await tool._run_async_impl(
        {
            "study_type": "rct",
            "methodology_descriptors": ["no_blinding", "high_dropout"],
        },
        ctx,
    )
    assert result["score"] == 1.0  # 3.0 - 1.0 - 1.0
    assert result["grade"] == "Low"


async def test_half_point_downgrades(tool, ctx):
    result = await tool._run_async_impl(
        {
            "study_type": "rct",
            "methodology_descriptors": ["inconsistency", "indirectness"],
        },
        ctx,
    )
    assert result["score"] == 2.0  # 3.0 - 0.5 - 0.5
    assert result["grade"] == "Moderate"


# ── upgrade factors ───────────────────────────────────────────


async def test_large_effect_upgrade(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "cohort", "methodology_descriptors": ["large_effect"]}, ctx
    )
    assert result["score"] == 3.0  # 2.0 + 1.0
    assert result["grade"] == "High"


async def test_dose_response_upgrade(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "cohort", "methodology_descriptors": ["dose_response"]}, ctx
    )
    assert result["score"] == 2.5  # 2.0 + 0.5
    assert result["grade"] == "Moderate"


# ── combined factors ──────────────────────────────────────────


async def test_mixed_upgrade_and_downgrade(tool, ctx):
    result = await tool._run_async_impl(
        {
            "study_type": "rct",
            "methodology_descriptors": ["no_blinding", "large_effect"],
        },
        ctx,
    )
    assert result["score"] == 3.0  # 3.0 - 1.0 + 1.0
    factors = {f["factor"] for f in result["factors_applied"]}
    assert "no_blinding" in factors
    assert "large_effect" in factors


# ── automatic detection ───────────────────────────────────────


async def test_auto_small_sample_detection(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "rct", "sample_size": 50}, ctx
    )
    assert result["score"] == 2.0  # 3.0 - 1.0 (auto small_sample)
    assert any(f["factor"] == "small_sample" for f in result["factors_applied"])


async def test_large_sample_no_auto_downgrade(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "rct", "sample_size": 500}, ctx
    )
    assert result["score"] == 3.0
    assert not any(f["factor"] == "small_sample" for f in result["factors_applied"])


async def test_auto_large_effect_from_effect_size(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "cohort", "effect_size": 3.5}, ctx
    )
    assert any(f["factor"] == "large_effect" for f in result["factors_applied"])


async def test_small_effect_size_no_auto_upgrade(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "cohort", "effect_size": 1.2}, ctx
    )
    assert not any(f["factor"] == "large_effect" for f in result["factors_applied"])


# ── boundary / edge cases ────────────────────────────────────


async def test_score_clamps_to_zero(tool, ctx):
    result = await tool._run_async_impl(
        {
            "study_type": "expert_opinion",
            "methodology_descriptors": ["small_sample", "no_blinding", "high_dropout"],
        },
        ctx,
    )
    assert result["score"] == 0.0
    assert result["grade"] == "Very Low"


async def test_score_clamps_to_five(tool, ctx):
    result = await tool._run_async_impl(
        {
            "study_type": "meta_analysis",
            "methodology_descriptors": ["large_effect", "dose_response", "confounders_reduce_effect"],
        },
        ctx,
    )
    assert result["score"] == 5.0
    assert result["grade"] == "High"


async def test_case_insensitive_study_type(tool, ctx):
    result = await tool._run_async_impl({"study_type": "RCT"}, ctx)
    assert result["score"] == 3.0


async def test_empty_descriptors(tool, ctx):
    result = await tool._run_async_impl(
        {"study_type": "cohort", "methodology_descriptors": []}, ctx
    )
    assert result["score"] == 2.0
    assert result["factors_applied"] == []


async def test_rationale_contains_base_score(tool, ctx):
    result = await tool._run_async_impl({"study_type": "rct"}, ctx)
    assert "Base score" in result["rationale"]
    assert "rct" in result["rationale"]


# ═══════════════════════════════════════════════════════════════
# LLM contextual quality assessment tests
# ═══════════════════════════════════════════════════════════════


@patch("lifesci_tools.evidence_grader._litellm_acompletion")
async def test_llm_detects_open_label_study(mock_llm, llm_tool, ctx):
    """LLM detects open-label study → no_blinding downgrade."""
    mock_llm.return_value = _mock_llm_response({
        "sample_size_estimate": 200,
        "blinding": "open",
        "funding_bias_risk": "low",
        "generalizability": "broad",
        "confidence_note": "Open-label design limits confidence despite adequate sample size.",
    })
    result = await llm_tool._run_async_impl(
        {
            "study_type": "rct",
            "study_description": "An open-label randomized trial of 200 patients...",
        },
        ctx,
    )
    assert result["method"] == "llm_enhanced"
    assert result["score"] == 2.0  # 3.0 - 1.0 (no_blinding)
    assert any(f["factor"] == "no_blinding" for f in result["factors_applied"])
    assert result["confidence_note"] != ""
    mock_llm.assert_called_once()


@patch("lifesci_tools.evidence_grader._litellm_acompletion")
async def test_llm_detects_funding_bias(mock_llm, llm_tool, ctx):
    """LLM detects high funding bias → publication_bias downgrade."""
    mock_llm.return_value = _mock_llm_response({
        "sample_size_estimate": 500,
        "blinding": "double",
        "funding_bias_risk": "high",
        "generalizability": "broad",
        "confidence_note": "Industry-funded study with potential reporting bias.",
    })
    result = await llm_tool._run_async_impl(
        {
            "study_type": "rct",
            "study_description": "A double-blind RCT funded by PharmaCorp...",
        },
        ctx,
    )
    assert any(f["factor"] == "publication_bias" for f in result["factors_applied"])
    assert "funding_bias_risk=high" in result["rationale"]


@patch("lifesci_tools.evidence_grader._litellm_acompletion")
async def test_llm_inferred_sample_size_only_when_caller_omits(mock_llm, llm_tool, ctx):
    """LLM sample_size_estimate only used when caller doesn't provide sample_size."""
    mock_llm.return_value = _mock_llm_response({
        "sample_size_estimate": 45,
        "blinding": "double",
        "funding_bias_risk": "low",
        "generalizability": "broad",
        "confidence_note": "Small subgroup analysis.",
    })
    # Caller provides sample_size=500 — LLM's 45 should be ignored
    result = await llm_tool._run_async_impl(
        {
            "study_type": "rct",
            "sample_size": 500,
            "study_description": "Analysis of 45 patients in a subgroup...",
        },
        ctx,
    )
    assert result["score"] == 3.0  # No small_sample downgrade (caller said 500)
    assert not any(f["factor"] == "small_sample" for f in result["factors_applied"])


@patch("lifesci_tools.evidence_grader._litellm_acompletion")
async def test_llm_inferred_sample_size_used_when_caller_omits(mock_llm, llm_tool, ctx):
    """LLM sample_size_estimate used when caller doesn't provide sample_size."""
    mock_llm.return_value = _mock_llm_response({
        "sample_size_estimate": 45,
        "blinding": "double",
        "funding_bias_risk": "low",
        "generalizability": "broad",
        "confidence_note": "Very small sample.",
    })
    result = await llm_tool._run_async_impl(
        {
            "study_type": "rct",
            "study_description": "A study of 45 patients...",
        },
        ctx,
    )
    # LLM inferred 45 → small_sample auto-detected
    assert any(f["factor"] == "small_sample" for f in result["factors_applied"])


@patch("lifesci_tools.evidence_grader._litellm_acompletion")
async def test_llm_failure_falls_back_to_heuristic(mock_llm, llm_tool, ctx):
    """LLM failure → pure heuristic scoring."""
    mock_llm.side_effect = Exception("LLM timeout")
    result = await llm_tool._run_async_impl(
        {
            "study_type": "rct",
            "study_description": "A randomized trial...",
        },
        ctx,
    )
    assert result["method"] == "heuristic"
    assert result["score"] == 3.0


async def test_no_description_skips_llm(llm_tool, ctx):
    """No study_description → LLM not called even if model configured."""
    result = await llm_tool._run_async_impl(
        {"study_type": "rct"},
        ctx,
    )
    assert result["method"] == "heuristic"
    assert result["score"] == 3.0


async def test_no_model_config_uses_heuristic(tool, ctx):
    """Tool without model config uses pure heuristic."""
    result = await tool._run_async_impl(
        {"study_type": "rct", "study_description": "A study..."},
        ctx,
    )
    assert result["method"] == "heuristic"


@patch("lifesci_tools.evidence_grader._litellm_acompletion")
async def test_llm_no_duplicate_factors(mock_llm, llm_tool, ctx):
    """LLM-detected factors don't duplicate caller-provided ones."""
    mock_llm.return_value = _mock_llm_response({
        "sample_size_estimate": None,
        "blinding": "open",
        "funding_bias_risk": "low",
        "generalizability": "broad",
        "confidence_note": "Open-label but well-designed.",
    })
    # Caller already passed no_blinding
    result = await llm_tool._run_async_impl(
        {
            "study_type": "rct",
            "methodology_descriptors": ["no_blinding"],
            "study_description": "An open-label trial...",
        },
        ctx,
    )
    # no_blinding should appear only once
    blinding_factors = [f for f in result["factors_applied"] if f["factor"] == "no_blinding"]
    assert len(blinding_factors) == 1


def test_init_with_dict_model():
    tool = EvidenceGraderTool(tool_config={
        "model": {"model": "vertex_ai/gemini-2.5-flash", "vertex_project": "p", "vertex_location": "l"}
    })
    assert tool.model == "vertex_ai/gemini-2.5-flash"


def test_init_without_model():
    tool = EvidenceGraderTool()
    assert tool.model == ""
