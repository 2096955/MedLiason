"""Tests for CompletenessCheckerTool — gap validation against decomposition plan."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lifesci_tools.completeness_checker import CompletenessCheckerTool


@pytest.fixture
def tool():
    """Tool without model config — keyword fallback only."""
    return CompletenessCheckerTool()


@pytest.fixture
def llm_tool():
    """Tool with model config — LLM semantic coverage enabled."""
    return CompletenessCheckerTool(tool_config={
        "model": "openai/gemini-2.5-flash-001",
        "temperature": 0.2,
    })


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.session = MagicMock()
    mock.session.id = "test-session-123"
    return mock


def _mock_llm_response(coverage_list: list[dict]):
    """Build a mock litellm acompletion response."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json.dumps({"coverage": coverage_list})
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    return mock_resp


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


# ═══════════════════════════════════════════════════════════════
# LLM semantic coverage tests
# ═══════════════════════════════════════════════════════════════


@patch("lifesci_tools.completeness_checker._litellm_acompletion")
async def test_llm_coverage_answered(mock_llm, llm_tool, ctx):
    """LLM determines that the sub-question is meaningfully answered."""
    mock_llm.return_value = _mock_llm_response([
        {
            "question": "What is the efficacy of metformin?",
            "answered": True,
            "confidence": 0.9,
            "matching_evidence_title": "Metformin efficacy study",
            "gap_reason": None,
        },
    ])
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy study", "snippet": "Metformin is effective as first-line therapy.", "grade": "High"},
    ])
    result = await llm_tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is True
    assert result["structural_coverage"] == 1.0
    assert result["answered"][0]["method"] == "llm"
    assert result["answered"][0]["coverage_score"] == 0.9
    assert result["method"] == "llm"
    mock_llm.assert_called_once()


@patch("lifesci_tools.completeness_checker._litellm_acompletion")
async def test_llm_coverage_gap(mock_llm, llm_tool, ctx):
    """LLM determines that the sub-question is NOT answered."""
    mock_llm.return_value = _mock_llm_response([
        {
            "question": "What is the efficacy of metformin?",
            "answered": True,
            "confidence": 0.85,
            "matching_evidence_title": "Metformin study",
            "gap_reason": None,
        },
        {
            "question": "What are the genomic markers for diabetes?",
            "answered": False,
            "confidence": 0.1,
            "matching_evidence_title": None,
            "gap_reason": "No evidence about genomic markers in the collected data",
        },
    ])
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "What are the genomic markers for diabetes?", "domain": "genomics", "target_agent": "GenomicsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Metformin study", "snippet": "Metformin reduces blood glucose."},
    ])
    result = await llm_tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is False
    assert len(result["answered"]) == 1
    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["gap_reason"] == "No evidence about genomic markers in the collected data"
    assert result["gaps"][0]["method"] == "llm"


@patch("lifesci_tools.completeness_checker._litellm_acompletion")
async def test_llm_failure_falls_back_to_keyword(mock_llm, llm_tool, ctx):
    """When LLM call fails, tool falls back to keyword matching."""
    mock_llm.side_effect = Exception("LLM timeout")
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy in diabetes treatment", "snippet": "Metformin is effective as first-line therapy."},
    ])
    result = await llm_tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    # Should still work via keyword fallback
    assert result["complete"] is True
    assert result["method"] == "keyword"
    assert result["answered"][0]["method"] == "keyword"


@patch("lifesci_tools.completeness_checker._litellm_acompletion")
async def test_llm_markdown_fence_stripping(mock_llm, llm_tool, ctx):
    """LLM response wrapped in markdown fences is still parsed correctly."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = (
        "```json\n"
        '{"coverage": [{"question": "What is metformin?", "answered": true, '
        '"confidence": 0.8, "matching_evidence_title": "Study", "gap_reason": null}]}'
        "\n```"
    )
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 80
    mock_resp.usage.completion_tokens = 40
    mock_llm.return_value = mock_resp

    plan = _make_plan([
        {"question": "What is metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Study", "snippet": "Metformin works."},
    ])
    result = await llm_tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is True
    assert result["answered"][0]["method"] == "llm"


@patch("lifesci_tools.completeness_checker._litellm_acompletion")
async def test_llm_malformed_json_falls_back(mock_llm, llm_tool, ctx):
    """Malformed JSON from LLM triggers keyword fallback."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "not valid json at all"
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 50
    mock_resp.usage.completion_tokens = 10
    mock_llm.return_value = mock_resp

    plan = _make_plan([
        {"question": "What is metformin efficacy?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy study", "snippet": "Metformin is effective."},
    ])
    result = await llm_tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["method"] == "keyword"


async def test_no_model_config_uses_keyword_only(tool, ctx):
    """Tool without model config uses keyword matching (no LLM call)."""
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
    ])
    evidence = _make_evidence([
        {"title": "Metformin efficacy in diabetes treatment", "snippet": "Metformin is effective."},
    ])
    result = await tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is True
    assert result["method"] == "keyword"


@patch("lifesci_tools.completeness_checker._litellm_acompletion")
async def test_llm_partial_coverage_mixed_method(mock_llm, llm_tool, ctx):
    """LLM answers some questions; keyword handles the rest (mixed method)."""
    # LLM only returns result for first question
    mock_llm.return_value = _mock_llm_response([
        {
            "question": "What is the efficacy of metformin?",
            "answered": True,
            "confidence": 0.85,
            "matching_evidence_title": "Metformin study",
            "gap_reason": None,
        },
        # Second question missing from LLM output
    ])
    plan = _make_plan([
        {"question": "What is the efficacy of metformin?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
        {"question": "Clinical trials for metformin?", "domain": "clinical_trials", "target_agent": "ClinicalTrialsSpecialist", "priority": 2},
    ])
    evidence = _make_evidence([
        {"title": "Metformin study", "snippet": "Metformin is effective."},
        {"title": "Clinical trials of metformin for T2D", "snippet": "Multiple clinical trials demonstrate metformin outcomes."},
    ])
    result = await llm_tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    assert result["complete"] is True
    assert result["method"] == "mixed"  # LLM + keyword


@patch("lifesci_tools.completeness_checker._litellm_acompletion")
async def test_llm_proceed_thresholds_preserved(mock_llm, llm_tool, ctx):
    """proceed flag uses same thresholds (structural >= 0.70, quality >= 0.40)."""
    mock_llm.return_value = _mock_llm_response([
        {
            "question": "Q1?",
            "answered": True,
            "confidence": 0.9,
            "matching_evidence_title": "Study A",
            "gap_reason": None,
        },
        {
            "question": "Q2?",
            "answered": False,
            "confidence": 0.1,
            "matching_evidence_title": None,
            "gap_reason": "No relevant evidence",
        },
        {
            "question": "Q3?",
            "answered": False,
            "confidence": 0.05,
            "matching_evidence_title": None,
            "gap_reason": "No relevant evidence",
        },
    ])
    plan = _make_plan([
        {"question": "Q1?", "domain": "d1", "target_agent": "A1", "priority": 1},
        {"question": "Q2?", "domain": "d2", "target_agent": "A2", "priority": 2},
        {"question": "Q3?", "domain": "d3", "target_agent": "A3", "priority": 3},
    ])
    evidence = _make_evidence([
        {"title": "Study A", "snippet": "Relevant findings.", "grade": "High"},
    ])
    result = await llm_tool._run_async_impl(
        {"decomposition_plan_json": plan, "collected_evidence_json": evidence}, ctx
    )
    # 1 of 3 answered = structural 0.333 < 0.70 → proceed=False
    assert result["structural_coverage"] < 0.70
    assert result["proceed"] is False


def test_init_with_dict_model():
    """YAML anchor expansion: model config as dict extracts model string + vertex kwargs."""
    tool = CompletenessCheckerTool(tool_config={
        "model": {
            "model": "vertex_ai/gemini-2.5-flash",
            "vertex_project": "my-project",
            "vertex_location": "us-central1",
        }
    })
    assert tool.model == "vertex_ai/gemini-2.5-flash"
    assert tool._vertex_kwargs == {"vertex_project": "my-project", "vertex_location": "us-central1"}


def test_init_without_model():
    """No model config → empty model string (keyword-only mode)."""
    tool = CompletenessCheckerTool()
    assert tool.model == ""
    assert tool._vertex_kwargs == {}
