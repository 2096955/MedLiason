"""Tests for the Triage Consensus Engine — diagnosis clustering and ranking."""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools.triage_consensus import TriageConsensusTool


@pytest.fixture
def tool():
    t = TriageConsensusTool()
    t.tool_config = {}
    return t


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.state = {}
    return ctx


def _verdicts_json(*verdict_dicts):
    """Helper: serialize a list of verdict dicts to a JSON string."""
    return json.dumps(list(verdict_dicts))


def _verdict(specialist, diagnosis, confidence):
    """Helper: build a single verdict dict."""
    return {
        "specialist": specialist,
        "diagnosis": diagnosis,
        "confidence": confidence,
        "thinking": f"Analysis by {specialist}",
        "tier": "tier1",
    }


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consensus_exact_match_grouping(tool, mock_ctx):
    """Two specialists with identical (case-insensitive) diagnoses form one cluster."""
    result = await tool._run_async_impl(
        {
            "verdicts": _verdicts_json(
                _verdict("family_physician", "Tension Headache", 75),
                _verdict("internist", "tension headache", 80),
                _verdict("neurologist", "Migraine", 60),
            ),
        },
        mock_ctx,
    )
    assert result["consensus_diagnosis"].lower() in ("tension headache",)
    assert result["cluster_count"] == 2  # headache cluster + migraine cluster
    assert len(result["supporting_specialists"]) == 2
    assert len(result["dissenting_specialists"]) == 1


@pytest.mark.asyncio
async def test_consensus_jaccard_grouping(tool, mock_ctx):
    """Jaccard similarity merges 'Type 2 Diabetes Mellitus' and 'diabetes mellitus type 2'."""
    result = await tool._run_async_impl(
        {
            "verdicts": _verdicts_json(
                _verdict("family_physician", "Type 2 Diabetes Mellitus", 80),
                _verdict("internist", "diabetes mellitus type 2", 75),
                _verdict("endocrinologist", "Type 2 Diabetes Mellitus", 85),
            ),
        },
        mock_ctx,
    )
    # All three should be in the same cluster
    assert len(result["supporting_specialists"]) == 3
    assert result["dissenting_specialists"] == []
    assert result["cluster_count"] == 1


@pytest.mark.asyncio
async def test_consensus_all_insufficient_returns_inconclusive(tool, mock_ctx):
    """All specialists returning 'Insufficient information' → INCONCLUSIVE."""
    result = await tool._run_async_impl(
        {
            "verdicts": _verdicts_json(
                _verdict("family_physician", "Insufficient information", 0),
                _verdict("internist", "Insufficient information", 0),
            ),
        },
        mock_ctx,
    )
    assert result["consensus_diagnosis"] == "INCONCLUSIVE"
    assert result["mean_confidence"] == 0
    assert result["insufficient_count"] == 2


@pytest.mark.asyncio
async def test_consensus_single_verdict(tool, mock_ctx):
    """A single valid verdict becomes the consensus."""
    result = await tool._run_async_impl(
        {
            "verdicts": _verdicts_json(
                _verdict("emergency_medicine", "Acute Appendicitis", 90),
            ),
        },
        mock_ctx,
    )
    assert result["consensus_diagnosis"] == "Acute Appendicitis"
    assert result["mean_confidence"] == 90
    assert result["cluster_count"] == 1
    assert len(result["supporting_specialists"]) == 1


@pytest.mark.asyncio
async def test_consensus_tie_breaking_by_confidence(tool, mock_ctx):
    """When clusters have equal size, higher avg confidence wins."""
    result = await tool._run_async_impl(
        {
            "verdicts": _verdicts_json(
                _verdict("family_physician", "Gastritis", 60),
                _verdict("internist", "Peptic Ulcer", 85),
            ),
        },
        mock_ctx,
    )
    # Peptic Ulcer has higher confidence (85 vs 60)
    assert result["consensus_diagnosis"] == "Peptic Ulcer"
    assert result["mean_confidence"] == 85


@pytest.mark.asyncio
async def test_consensus_discard_zero_confidence(tool, mock_ctx):
    """Verdicts with confidence 0 are discarded from clustering."""
    result = await tool._run_async_impl(
        {
            "verdicts": _verdicts_json(
                _verdict("family_physician", "Common Cold", 70),
                _verdict("internist", "Insufficient information", 0),
                _verdict("emergency_medicine", "Common Cold", 65),
            ),
        },
        mock_ctx,
    )
    # The insufficient-info verdict is discarded
    assert result["insufficient_count"] == 1
    assert result["consensus_diagnosis"] == "Common Cold"
    assert len(result["supporting_specialists"]) == 2


@pytest.mark.asyncio
async def test_consensus_dissenting_specialists(tool, mock_ctx):
    """Specialists in non-winning clusters appear as dissenting."""
    result = await tool._run_async_impl(
        {
            "verdicts": _verdicts_json(
                _verdict("family_physician", "Pneumonia", 80),
                _verdict("internist", "Pneumonia", 75),
                _verdict("pulmonologist", "Bronchitis", 70),
                _verdict("emergency_medicine", "Asthma Exacerbation", 50),
            ),
        },
        mock_ctx,
    )
    assert result["consensus_diagnosis"] == "Pneumonia"
    assert len(result["dissenting_specialists"]) == 2  # bronchitis + asthma
    dissent_names = [d["specialist"] for d in result["dissenting_specialists"]]
    assert "pulmonologist" in dissent_names
    assert "emergency_medicine" in dissent_names
