"""Tests for ReflectionAnalyzerTool — multi-hop gap analysis."""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools.reflection_analyzer import (
    ReflectionAnalyzerTool,
    _find_contradictions,
    _find_missing_perspectives,
    _identify_gaps,
)


@pytest.fixture
def tool():
    return ReflectionAnalyzerTool()


@pytest.fixture
def ctx():
    return MagicMock()


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
