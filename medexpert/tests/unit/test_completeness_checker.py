"""Tests for CompletenessCheckerTool — gap validation against decomposition plan."""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools.completeness_checker import CompletenessCheckerTool


@pytest.fixture
def tool():
    return CompletenessCheckerTool()


@pytest.fixture
def ctx():
    return MagicMock()


def _make_plan(sub_questions):
    return json.dumps({"sub_questions": sub_questions})


def _make_evidence(items):
    return json.dumps(items)


# ── full coverage ─────────────────────────────────────────────


async def test_full_coverage(tool, ctx):
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "What clinical trials exist for metformin?", "domain": "clinical_trials", "target_agent": "ClinicalTrialsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy in diabetes treatment", "snippet": "Metformin is effective as first-line therapy."},
        {"title": "Clinical trials of metformin for T2D", "snippet": "Multiple clinical trials demonstrate metformin outcomes."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is True
    assert result["coverage_pct"] == 100.0
    assert len(result["gaps"]) == 0
    assert len(result["answered"]) == 2


async def test_partial_coverage(tool, ctx):
    plan = _make_plan([
        {"question": "What is the safety of aspirin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "What are the genomic markers for breast cancer susceptibility?", "domain": "genomics", "target_agent": "GenomicsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Safety profile of aspirin", "snippet": "Aspirin has well-known safety characteristics."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is False
    assert result["coverage_pct"] == 50.0
    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["domain"] == "genomics"


async def test_zero_coverage(tool, ctx):
    plan = _make_plan([
        {"question": "What is the environmental impact on asthma?", "domain": "environmental", "target_agent": "EnvironmentalSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Cooking recipes for healthy meals", "snippet": "Use olive oil for Mediterranean diet."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is False
    assert len(result["gaps"]) == 1


# ── empty inputs ──────────────────────────────────────────────


async def test_empty_evidence(tool, ctx):
    plan = _make_plan([
        {"question": "What treatments exist?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": "[]"}, ctx
    )
    assert result["complete"] is False
    assert result["coverage_pct"] == 0.0


async def test_no_sub_questions(tool, ctx):
    result = await tool._run_async_impl(
        {"decomposition_plan_json": "{}", "collected_evidence_json": "[]"}, ctx
    )
    assert "error" in result


# ── suggestions ───────────────────────────────────────────────


async def test_suggestions_generated_for_gaps(tool, ctx):
    plan = _make_plan([
        {"question": "What are the pharmacological effects of ibuprofen?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "What are the genomic biomarkers for lung cancer?", "domain": "genomics", "target_agent": "GenomicsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Pharmacological effects of ibuprofen", "snippet": "Ibuprofen inhibits COX enzymes, reducing inflammation."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert len(result["suggestions"]) >= 1
    assert "GenomicsSpecialist" in result["suggestions"][0]


# ── error handling ────────────────────────────────────────────


async def test_invalid_plan_json(tool, ctx):
    result = await tool._run_async_impl(
        {"decomposition_plan_json": "not json", "collected_evidence_json": "[]"}, ctx
    )
    assert "error" in result


async def test_invalid_evidence_json(tool, ctx):
    result = await tool._run_async_impl(
        {"decomposition_plan_json": '{"sub_questions": []}', "collected_evidence_json": "bad"}, ctx
    )
    assert "error" in result


# ── coverage scoring ──────────────────────────────────────────


async def test_coverage_score_attached_to_answers(tool, ctx):
    plan = _make_plan([
        {"question": "What is metformin treatment efficacy?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Metformin treatment outcomes efficacy study", "snippet": "Metformin treatment shows strong efficacy."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert len(result["answered"]) == 1
    assert result["answered"][0]["coverage_score"] > 0


async def test_total_questions_count(tool, ctx):
    plan = _make_plan([
        {"question": "Q1?", "domain": "d1", "target_agent": "A1", "priority": 1},
        {"question": "Q2?", "domain": "d2", "target_agent": "A2", "priority": 2},
        {"question": "Q3?", "domain": "d3", "target_agent": "A3", "priority": 3},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": "[]"}, ctx
    )
    assert result["total_questions"] == 3


# ── domain matching ───────────────────────────────────────────


async def test_mismatched_domains(tool, ctx):
    plan = _make_plan([
        {"question": "What are the cancer genomic variants?", "domain": "genomics", "target_agent": "GenomicsSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Diabetes diet recommendations", "snippet": "Low carb diets for diabetes management."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is False


async def test_multiple_evidence_match_best(tool, ctx):
    plan = _make_plan([
        {"question": "What drugs treat hypertension?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Cooking oils", "snippet": "Olive oil is healthy."},
        {"title": "Antihypertensive drugs treatment", "snippet": "ACE inhibitors treat hypertension effectively."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is True
    assert "Antihypertensive" in result["answered"][0]["matched_evidence"]


# ═══════════════════════════════════════════════════════════════
# New coverage metrics tests
# ═══════════════════════════════════════════════════════════════


async def test_structural_coverage_metric(tool, ctx):
    """structural_coverage = answered / total as a fraction."""
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "What are the genomic markers?", "domain": "genomics", "target_agent": "GenomicsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy", "snippet": "Metformin is effective."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["structural_coverage"] == 0.5
    assert result["coverage_pct"] == 50.0


async def test_quality_coverage_with_grades(tool, ctx):
    """quality_coverage counts answered questions whose matched evidence has a grade."""
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "Clinical trials for metformin?", "domain": "clinical_trials", "target_agent": "ClinicalTrialsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy study", "snippet": "Metformin is effective.", "grade": "High"},
        {"title": "Clinical trials of metformin", "snippet": "Multiple clinical trials show outcomes."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["structural_coverage"] == 1.0
    # One of two answered has a grade
    assert result["quality_coverage"] == 0.5


async def test_proceed_false_when_low_structural(tool, ctx):
    """proceed=False when structural_coverage < 0.70."""
    plan = _make_plan([
        {"question": "Q1 about drugs?", "domain": "drugs", "target_agent": "Drug", "priority": 1},
        {"question": "Q2 about genomics?", "domain": "genomics", "target_agent": "Genomics", "priority": 2},
        {"question": "Q3 about epidemiology?", "domain": "epi", "target_agent": "Epi", "priority": 3},
    ])
    # Only 1 of 3 answered → structural = 0.333
    evidence = _make_evidence([
        {"title": "Drug treatment outcomes", "snippet": "Drugs treat diseases.", "grade": "High"},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["structural_coverage"] < 0.70
    assert result["proceed"] is False


async def test_proceed_true_when_high_coverage(tool, ctx):
    """proceed=True when structural >= 0.70 AND quality >= 0.40."""
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "Clinical trials for metformin?", "domain": "clinical_trials", "target_agent": "ClinicalTrialsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy study", "snippet": "Metformin is effective.", "grade": "High"},
        {"title": "Clinical trials of metformin", "snippet": "Multiple clinical trials show outcomes.", "grade": "Moderate"},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["structural_coverage"] >= 0.70
    assert result["quality_coverage"] >= 0.40
    assert result["proceed"] is True


async def test_domain_matching_boost(tool, ctx):
    """Domain match adds 0.3 to overlap, potentially pulling a gap into answered."""
    plan = _make_plan([
        {"question": "Environmental health impacts?", "domain": "environmental", "target_agent": "Env", "priority": 1},
    ])
    # Evidence with weak keyword overlap but matching domain
    evidence = _make_evidence([
        {"title": "Pollution exposure data", "snippet": "Air quality monitoring results.", "domain": "environmental"},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    # With domain boost, it should be answered even with lower keyword overlap
    assert len(result["answered"]) == 1
    assert result["answered"][0]["domain_matched"] is True


async def test_domain_match_count(tool, ctx):
    """domain_match_count tracks how many answered questions had domain matches."""
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "What clinical trials exist?", "domain": "clinical_trials", "target_agent": "ClinicalTrialsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy treatment", "snippet": "Metformin is effective.", "domain": "drugs"},
        {"title": "Clinical trials of metformin for T2D", "snippet": "Multiple clinical trials demonstrate metformin outcomes.", "domain": "genomics"},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["domain_match_count"] >= 1  # At least the drugs match


async def test_has_quality_grade_flag(tool, ctx):
    """Answered entries report has_quality_grade correctly."""
    plan = _make_plan([
        {"question": "What is metformin treatment efficacy?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Metformin treatment efficacy study", "snippet": "Metformin effective.", "grade": "High"},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["answered"][0]["has_quality_grade"] is True


async def test_no_grade_flag(tool, ctx):
    """Answered entries without grade have has_quality_grade=False."""
    plan = _make_plan([
        {"question": "What is metformin treatment efficacy?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Metformin treatment efficacy study", "snippet": "Metformin effective."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["answered"][0]["has_quality_grade"] is False
