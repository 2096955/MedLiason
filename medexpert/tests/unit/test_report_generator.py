"""Tests for ReportGeneratorTool — multi-format research report output."""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools.report_generator import ReportGeneratorTool


@pytest.fixture
def tool():
    return ReportGeneratorTool()


@pytest.fixture
def ctx():
    return MagicMock()


@pytest.fixture
def sample_evidence():
    return [
        {
            "cite_id": "s0r0",
            "source": "pubmed",
            "title": "Metformin Efficacy Meta-Analysis",
            "snippet": "Metformin reduces HbA1c by 1.0-1.5%.",
            "study_type": "meta_analysis",
            "grade": "High",
        },
        {
            "cite_id": "s0r1",
            "source": "clinicaltrials",
            "title": "Phase 3 SGLT2 Inhibitor Trial",
            "snippet": "Novel SGLT2 inhibitor shows 0.8% HbA1c reduction.",
            "study_type": "rct",
            "grade": "High",
        },
    ]


@pytest.fixture
def sample_advisory():
    return [
        {"persona": "Clinical Pragmatist", "perspective_text": "Metformin remains the pragmatic first choice."},
        {"persona": "Research Methodologist", "perspective_text": "Evidence quality is high for metformin."},
    ]


@pytest.fixture
def sample_verification():
    return {"overall_score": 0.85, "passed": True, "unsupported_claims": []}


# ── quick_answer mode ─────────────────────────────────────────


async def test_quick_answer_basic(tool, ctx, sample_evidence):
    result = await tool._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "What is metformin?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["mode"] == "quick_answer"
    report = result["report"]
    assert "medical advice" in report.lower()  # Disclaimer present
    assert "[[cite:" in report  # Citations present


async def test_quick_answer_empty_evidence(tool, ctx):
    result = await tool._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "What is X?",
            "evidence_json": "[]",
        },
        ctx,
    )
    assert "No evidence" in result["report"]


async def test_quick_answer_limits_to_3(tool, ctx):
    evidence = [
        {"cite_id": f"s0r{i}", "title": f"Study {i}", "snippet": f"Result {i}"}
        for i in range(10)
    ]
    result = await tool._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "Test?",
            "evidence_json": json.dumps(evidence),
        },
        ctx,
    )
    # Should only include top 3 citations
    assert result["report"].count("[[cite:") <= 3


# ── research_brief mode ──────────────────────────────────────


async def test_research_brief_structure(tool, ctx, sample_evidence):
    result = await tool._run_async_impl(
        {
            "mode": "research_brief",
            "question": "Efficacy of metformin?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    report = result["report"]
    assert "## Background" in report
    assert "## Key Findings" in report
    assert "## Limitations" in report
    assert "## Conclusion" in report


async def test_research_brief_with_verification(tool, ctx, sample_evidence):
    verification = {"passed": False, "overall_score": 0.5}
    result = await tool._run_async_impl(
        {
            "mode": "research_brief",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
            "verification_result_json": json.dumps(verification),
        },
        ctx,
    )
    assert "verified" in result["report"].lower() or "verification" in result["report"].lower() or "claims" in result["report"].lower()


async def test_research_brief_empty_evidence(tool, ctx):
    result = await tool._run_async_impl(
        {
            "mode": "research_brief",
            "question": "Test?",
            "evidence_json": "[]",
        },
        ctx,
    )
    assert "Insufficient evidence" in result["report"] or "No evidence" in result["report"]


# ── full_synthesis mode ───────────────────────────────────────


async def test_full_synthesis_structure(tool, ctx, sample_evidence):
    result = await tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Comprehensive diabetes treatment review?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    report = result["report"]
    assert "## Executive Summary" in report
    assert "## Evidence Summary Table" in report
    assert "## Detailed Findings" in report
    assert "## Methodology" in report


async def test_full_synthesis_evidence_table(tool, ctx, sample_evidence):
    result = await tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    report = result["report"]
    assert "meta_analysis" in report
    assert "pubmed" in report


async def test_full_synthesis_with_verification(tool, ctx, sample_evidence, sample_verification):
    result = await tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
            "verification_result_json": json.dumps(sample_verification),
        },
        ctx,
    )
    assert "## Verification Results" in result["report"]
    assert result["has_verification"] is True


# ── advisory_board_report mode ────────────────────────────────


async def test_advisory_report_structure(tool, ctx, sample_evidence, sample_advisory):
    result = await tool._run_async_impl(
        {
            "mode": "advisory_board_report",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
            "advisory_perspectives_json": json.dumps(sample_advisory),
        },
        ctx,
    )
    report = result["report"]
    assert "## Advisory Board Perspectives" in report
    assert "## Consensus Analysis" in report
    assert "Clinical Pragmatist" in report


async def test_advisory_report_without_perspectives(tool, ctx, sample_evidence):
    result = await tool._run_async_impl(
        {
            "mode": "advisory_board_report",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert "No advisory board perspectives" in result["report"]
    assert result["has_advisory"] is False


# ── error handling ────────────────────────────────────────────


async def test_invalid_mode(tool, ctx):
    result = await tool._run_async_impl(
        {
            "mode": "invalid_mode",
            "question": "Test?",
            "evidence_json": "[]",
        },
        ctx,
    )
    assert "error" in result


async def test_empty_question(tool, ctx):
    result = await tool._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "",
            "evidence_json": "[]",
        },
        ctx,
    )
    assert "error" in result


async def test_invalid_evidence_json(tool, ctx):
    result = await tool._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "Test?",
            "evidence_json": "not json",
        },
        ctx,
    )
    assert "error" in result


async def test_invalid_verification_json_ignored(tool, ctx, sample_evidence):
    result = await tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
            "verification_result_json": "bad json",
        },
        ctx,
    )
    assert result["has_verification"] is False


# ── metadata in response ─────────────────────────────────────


async def test_evidence_count_returned(tool, ctx, sample_evidence):
    result = await tool._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["evidence_count"] == 2


async def test_disclaimer_in_all_modes(tool, ctx, sample_evidence):
    for mode in ["quick_answer", "research_brief", "full_synthesis", "advisory_board_report"]:
        result = await tool._run_async_impl(
            {
                "mode": mode,
                "question": "Test?",
                "evidence_json": json.dumps(sample_evidence),
            },
            ctx,
        )
        assert "medical advice" in result["report"].lower()
