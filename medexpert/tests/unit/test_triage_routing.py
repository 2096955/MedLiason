"""Tests for the Triage Routing Tool — specialist selection by keyword matching."""

import json
from unittest.mock import MagicMock

import pytest

import lifesci_tools.triage_routing as routing_mod
from lifesci_tools.triage_routing import TriageRoutingTool


@pytest.fixture(autouse=True)
def _clear_routing_cache():
    """Clear the module-level routing config cache between tests."""
    routing_mod._routing_cache = None
    yield
    routing_mod._routing_cache = None


@pytest.fixture
def routing_config_path(tmp_path):
    """Write a minimal specialist_routing.json for tests and return its path."""
    config = {
        "routing_rules": [
            {
                "specialist": "cardiologist",
                "keywords": ["chest pain", "heart", "palpitations"],
            },
            {
                "specialist": "neurologist",
                "keywords": ["headache", "seizure", "numbness", "dizziness"],
            },
            {
                "specialist": "gastroenterologist",
                "keywords": ["stomach", "nausea", "vomiting", "abdominal pain"],
            },
            {
                "specialist": "dermatologist",
                "keywords": ["rash", "skin", "itching"],
            },
            {
                "specialist": "pulmonologist",
                "keywords": ["cough", "breathing", "wheeze"],
            },
        ],
        "max_tier2_specialists": 3,
        "match_threshold": 1,
    }
    path = tmp_path / "specialist_routing.json"
    path.write_text(json.dumps(config))
    return str(path)


@pytest.fixture
def tool(routing_config_path):
    t = TriageRoutingTool()
    t.tool_config = {"routing_config_path": routing_config_path}
    return t


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.state = {}
    return ctx


def _clinical_note(**kwargs):
    """Build a clinical note JSON string from keyword arguments."""
    note = {
        "chief_complaint": kwargs.get("chief_complaint", ""),
        "symptoms": kwargs.get("symptoms", []),
        "medical_history": kwargs.get("medical_history"),
        "additional_notes": kwargs.get("additional_notes"),
    }
    return json.dumps(note)


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_routing_always_includes_tier1(tool, mock_ctx):
    """Tier 1 generalists are always included regardless of symptoms."""
    result = await tool._run_async_impl(
        {"clinical_note": _clinical_note(chief_complaint="mild fatigue")},
        mock_ctx,
    )
    from lifesci_common.constants import TRIAGE_TIER1_SPECIALISTS

    for spec in TRIAGE_TIER1_SPECIALISTS:
        assert spec in result["all_specialists"]
    assert result["tier1"] == TRIAGE_TIER1_SPECIALISTS


@pytest.mark.asyncio
async def test_routing_selects_tier2_by_keywords(tool, mock_ctx):
    """Keywords in chief complaint and symptoms select correct Tier 2 specialists."""
    note = _clinical_note(
        chief_complaint="severe headache and dizziness",
        symptoms=[
            {"symptom": "headache", "duration": "3 days"},
            {"symptom": "nausea", "duration": "1 day"},
        ],
    )
    result = await tool._run_async_impl({"clinical_note": note}, mock_ctx)

    # Neurologist should match (headache, dizziness)
    assert "neurologist" in result["tier2"]
    # Gastroenterologist should match (nausea)
    assert "gastroenterologist" in result["tier2"]


@pytest.mark.asyncio
async def test_routing_max_3_tier2(tool, mock_ctx):
    """Even with many keyword matches, at most 3 tier2 specialists are selected."""
    note = _clinical_note(
        chief_complaint="chest pain with headache and stomach pain",
        symptoms=[
            {"symptom": "chest pain"},
            {"symptom": "headache"},
            {"symptom": "stomach"},
            {"symptom": "rash"},
            {"symptom": "cough"},
        ],
    )
    result = await tool._run_async_impl({"clinical_note": note}, mock_ctx)

    assert len(result["tier2"]) <= 3


@pytest.mark.asyncio
async def test_routing_no_tier2_matches(tool, mock_ctx):
    """When no keywords match, only tier1 is returned."""
    note = _clinical_note(chief_complaint="general malaise")
    result = await tool._run_async_impl({"clinical_note": note}, mock_ctx)

    assert result["tier2"] == []
    from lifesci_common.constants import TRIAGE_TIER1_SPECIALISTS

    assert result["all_specialists"] == TRIAGE_TIER1_SPECIALISTS


@pytest.mark.asyncio
async def test_routing_handles_empty_clinical_note(tool, mock_ctx):
    """Empty clinical note still returns tier1 and no errors."""
    result = await tool._run_async_impl(
        {"clinical_note": "{}"},
        mock_ctx,
    )
    assert result["tier2"] == []
    assert result["total"] == len(result["tier1"])
