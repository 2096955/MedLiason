"""Tests for the Triage Intake Tool — validation, emergency detection, completeness."""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools.triage_intake import TriageIntakeTool


@pytest.fixture
def tool():
    t = TriageIntakeTool()
    t.tool_config = {}
    return t


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.state = {}
    return ctx


def _symptoms_json(*symptom_dicts):
    """Helper: serialize a list of symptom dicts to a JSON string."""
    return json.dumps(list(symptom_dicts))


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intake_complete_with_sufficient_data(tool, mock_ctx):
    """Chief complaint + 2 detailed symptoms → status='complete'."""
    result = await tool._run_async_impl(
        {
            "chief_complaint": "persistent headache",
            "symptoms": _symptoms_json(
                {"symptom": "headache", "duration": "3 days", "severity": "7"},
                {"symptom": "nausea", "duration": "2 days", "severity": "4"},
            ),
        },
        mock_ctx,
    )
    assert result["status"] == "complete"
    assert result["symptom_count"] == 2
    assert result["clinical_note"]["chief_complaint"] == "persistent headache"


@pytest.mark.asyncio
async def test_intake_needs_more_info_missing_symptoms(tool, mock_ctx):
    """Chief complaint + only 1 symptom → needs_more_info."""
    result = await tool._run_async_impl(
        {
            "chief_complaint": "sore throat",
            "symptoms": _symptoms_json(
                {"symptom": "sore throat", "duration": "1 day", "severity": "5"},
            ),
        },
        mock_ctx,
    )
    assert result["status"] == "needs_more_info"
    assert "missing_fields" in result
    assert len(result["missing_fields"]) > 0


@pytest.mark.asyncio
async def test_intake_emergency_detection_chest_pain(tool, mock_ctx):
    """'chest pain' in chief complaint → status='emergency'."""
    result = await tool._run_async_impl(
        {
            "chief_complaint": "severe chest pain radiating to arm",
            "symptoms": _symptoms_json(
                {"symptom": "chest pain", "duration": "30 minutes", "severity": "9"},
                {"symptom": "arm numbness", "duration": "30 minutes", "severity": "6"},
            ),
        },
        mock_ctx,
    )
    assert result["status"] == "emergency"
    assert result["emergency_type"] == "chest pain"
    assert result["clinical_note"]["emergency_flag"] is True


@pytest.mark.asyncio
async def test_intake_emergency_detection_breathing(tool, mock_ctx):
    """'difficulty breathing' in symptoms → status='emergency'."""
    result = await tool._run_async_impl(
        {
            "chief_complaint": "feeling very unwell",
            "symptoms": _symptoms_json(
                {
                    "symptom": "difficulty breathing",
                    "duration": "1 hour",
                    "severity": "9",
                },
                {"symptom": "wheezing", "duration": "1 hour", "severity": "7"},
            ),
        },
        mock_ctx,
    )
    assert result["status"] == "emergency"
    assert result["emergency_type"] == "difficulty breathing"


@pytest.mark.asyncio
async def test_intake_no_chief_complaint(tool, mock_ctx):
    """Missing chief complaint → needs_more_info with 'chief_complaint' in missing."""
    result = await tool._run_async_impl(
        {
            "chief_complaint": "",
            "symptoms": _symptoms_json(
                {"symptom": "fatigue", "duration": "1 week", "severity": "5"},
            ),
        },
        mock_ctx,
    )
    assert result["status"] == "needs_more_info"
    assert "chief_complaint" in result["missing_fields"]


@pytest.mark.asyncio
async def test_intake_missing_severity_duration(tool, mock_ctx):
    """Symptoms without severity/duration are not counted as detailed."""
    result = await tool._run_async_impl(
        {
            "chief_complaint": "back pain",
            "symptoms": _symptoms_json(
                {"symptom": "back pain"},  # no duration or severity
                {"symptom": "stiffness"},  # no duration or severity
            ),
        },
        mock_ctx,
    )
    assert result["status"] == "needs_more_info"
    # Should request duration/severity details
    missing = result["missing_fields"]
    assert any("duration" in f or "severity" in f for f in missing)


@pytest.mark.asyncio
async def test_triage_intake_sets_complete_flag_in_session_state(tool, mock_ctx):
    """When intake returns status='complete', _triage_intake_complete is set in state."""
    mock_ctx.state = {}
    result = await tool._run_async_impl(
        {
            "chief_complaint": "extreme fatigue",
            "symptoms": _symptoms_json(
                {"symptom": "fatigue", "duration": "7 months", "severity": "10/10"},
                {"symptom": "brain fog", "duration": "7 months", "severity": "8/10"},
            ),
        },
        mock_ctx,
    )

    assert result["status"] == "complete"
    assert mock_ctx.state.get("_triage_intake_complete") is True


@pytest.mark.asyncio
async def test_triage_intake_does_not_set_flag_on_needs_more_info(tool, mock_ctx):
    """When intake returns needs_more_info, flag should NOT be set."""
    mock_ctx.state = {}
    result = await tool._run_async_impl(
        {
            "chief_complaint": "sore throat",
            "symptoms": _symptoms_json(
                {"symptom": "sore throat", "duration": "1 day", "severity": "5"},
            ),
        },
        mock_ctx,
    )

    assert result["status"] == "needs_more_info"
    assert "_triage_intake_complete" not in mock_ctx.state
