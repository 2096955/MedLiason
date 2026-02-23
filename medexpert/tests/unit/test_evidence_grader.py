"""Tests for the EvidenceGraderTool — GRADE methodology scoring."""

from unittest.mock import MagicMock

import pytest

from lifesci_tools.evidence_grader import EvidenceGraderTool


@pytest.fixture
def tool():
    return EvidenceGraderTool()


@pytest.fixture
def ctx():
    return MagicMock()


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
