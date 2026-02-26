"""Tests for the Triage NBA Tool — deterministic patient routing rules."""

from unittest.mock import MagicMock

import pytest

from lifesci_tools.triage_nba import TriageNBATool


@pytest.fixture
def tool():
    t = TriageNBATool()
    t.tool_config = {}  # No cold_db_path → dispatch is silently skipped
    return t


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.state = {}
    return ctx


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nba_emergency_override(tool, mock_ctx):
    """emergency_override=true → emergency_services, immediate urgency."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Chest pain",
            "mean_confidence": 0,
            "eval_confidence": 0,
            "emergency_override": True,
        },
        mock_ctx,
    )
    assert result["route"] == "emergency_services"
    assert result["urgency"] == "immediate"


@pytest.mark.asyncio
async def test_nba_flag_life_threatening_to_hospital(tool, mock_ctx):
    """Flagged + life-threatening diagnosis → hospital, immediate."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Myocardial Infarction",
            "mean_confidence": 60,
            "eval_confidence": 70,
            "flag_for_review": True,
            "flag_reason": "Dissenting specialist at 75% confidence",
        },
        mock_ctx,
    )
    assert result["route"] == "hospital"
    assert result["urgency"] == "immediate"


@pytest.mark.asyncio
async def test_nba_flag_low_confidence_to_hospital(tool, mock_ctx):
    """Flagged + mean_confidence < 40 → hospital, urgent."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Unknown condition",
            "mean_confidence": 30,
            "eval_confidence": 35,
            "flag_for_review": True,
            "flag_reason": "Low confidence",
        },
        mock_ctx,
    )
    assert result["route"] == "hospital"
    assert result["urgency"] == "urgent"


@pytest.mark.asyncio
async def test_nba_flag_to_gp(tool, mock_ctx):
    """Flagged but not life-threatening and confidence >= 40 → gp_visit, urgent."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Gastritis",
            "mean_confidence": 55,
            "eval_confidence": 60,
            "flag_for_review": True,
            "flag_reason": "Symptom mismatch",
        },
        mock_ctx,
    )
    assert result["route"] == "gp_visit"
    assert result["urgency"] == "urgent"


@pytest.mark.asyncio
async def test_nba_inconclusive_to_gp_urgent(tool, mock_ctx):
    """INCONCLUSIVE diagnosis → gp_visit, urgent."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "INCONCLUSIVE",
            "mean_confidence": 0,
            "eval_confidence": 0,
        },
        mock_ctx,
    )
    assert result["route"] == "gp_visit"
    assert result["urgency"] == "urgent"
    assert "consensus" in result["reasoning"].lower()


@pytest.mark.asyncio
async def test_nba_high_confidence_self_care(tool, mock_ctx):
    """High eval_confidence + self-care eligible condition → self_care."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Common Cold",
            "mean_confidence": 80,
            "eval_confidence": 80,
        },
        mock_ctx,
    )
    assert result["route"] == "self_care"
    assert result["urgency"] == "low"
    assert result["self_care_instructions"] is not None
    assert result["escalation_criteria"] is not None


@pytest.mark.asyncio
async def test_nba_pharmacist_route(tool, mock_ctx):
    """eval_confidence >= 70 + OTC-eligible condition → pharmacist."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Mild Eczema",
            "mean_confidence": 75,
            "eval_confidence": 75,
        },
        mock_ctx,
    )
    assert result["route"] == "pharmacist"
    assert result["urgency"] == "low"


@pytest.mark.asyncio
async def test_nba_specialist_referral(tool, mock_ctx):
    """eval_confidence >= 60 + specialist_type present → specialist_referral."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Rheumatoid Arthritis",
            "mean_confidence": 70,
            "eval_confidence": 70,
            "specialist_type": "rheumatologist",
        },
        mock_ctx,
    )
    assert result["route"] == "specialist_referral"
    assert result["urgency"] == "routine"
    assert result["specialist_type"] == "rheumatologist"
    assert result["follow_up_timeframe"] is not None


@pytest.mark.asyncio
async def test_nba_default_to_gp(tool, mock_ctx):
    """Low eval_confidence with no special conditions → gp_visit, urgent (default)."""
    result = await tool._run_async_impl(
        {
            "consensus_diagnosis": "Unclear presentation",
            "mean_confidence": 40,
            "eval_confidence": 45,
        },
        mock_ctx,
    )
    assert result["route"] == "gp_visit"
    assert result["urgency"] == "urgent"
    assert "disclaimer" in result
