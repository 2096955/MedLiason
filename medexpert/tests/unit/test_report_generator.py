"""Tests for ReportGeneratorTool — multi-format research report output."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lifesci_tools.report_generator import ReportGeneratorTool


@pytest.fixture
def tool():
    """Tool without model config — template only."""
    return ReportGeneratorTool()


@pytest.fixture
def llm_tool():
    """Tool with model config — LLM narrative enabled."""
    return ReportGeneratorTool(tool_config={
        "model": "openai/gemini-2.5-flash-001",
        "temperature": 0.3,
    })


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.session = MagicMock()
    mock.session.id = "test-session-789"
    return mock


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


# ═══════════════════════════════════════════════════════════════
# LLM narrative synthesis tests
# ═══════════════════════════════════════════════════════════════


def _mock_narrative_response(text: str):
    """Build a mock litellm response for narrative synthesis."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = text
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 300
    mock_resp.usage.completion_tokens = 400
    return mock_resp


@patch("lifesci_tools.report_generator._litellm_acompletion")
async def test_llm_full_synthesis(mock_llm, llm_tool, ctx, sample_evidence):
    """LLM generates narrative synthesis for full_synthesis mode."""
    mock_llm.return_value = _mock_narrative_response(
        "Strong evidence suggests that metformin is effective as first-line therapy "
        "for type 2 diabetes [[cite:s0r0]]. A recent Phase 3 trial also demonstrated "
        "the efficacy of SGLT2 inhibitors [[cite:s0r1]].\n\n"
        "The evidence is largely consistent, with both metformin and newer agents "
        "showing meaningful HbA1c reductions. However, long-term comparative data "
        "remains limited.\n\n"
        "**Bottom line:** Metformin remains the strongest first-line option, with "
        "SGLT2 inhibitors as a viable complement."
    )
    result = await llm_tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "What is the best treatment for diabetes?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["method"] == "llm"
    assert "metformin" in result["report"].lower()
    assert "[[cite:s0r0]]" in result["report"]
    assert "## Evidence Summary Table" in result["report"]
    assert "medical advice" in result["report"].lower()  # Disclaimer
    mock_llm.assert_called_once()


@patch("lifesci_tools.report_generator._litellm_acompletion")
async def test_llm_advisory_board_report(mock_llm, llm_tool, ctx, sample_evidence, sample_advisory):
    """LLM narrative + advisory perspectives in advisory_board_report mode."""
    mock_llm.return_value = _mock_narrative_response(
        "Evidence synthesis narrative for the advisory report."
    )
    result = await llm_tool._run_async_impl(
        {
            "mode": "advisory_board_report",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
            "advisory_perspectives_json": json.dumps(sample_advisory),
        },
        ctx,
    )
    assert result["method"] == "llm"
    assert "## Advisory Board Perspectives" in result["report"]
    assert "Clinical Pragmatist" in result["report"]


@patch("lifesci_tools.report_generator._litellm_acompletion")
async def test_llm_failure_falls_back_to_template(mock_llm, llm_tool, ctx, sample_evidence):
    """LLM failure → template fallback for full_synthesis."""
    mock_llm.side_effect = Exception("LLM timeout")
    result = await llm_tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["method"] == "template"
    assert "## Executive Summary" in result["report"]  # Template structure


@patch("lifesci_tools.report_generator._litellm_acompletion")
async def test_llm_with_verification(mock_llm, llm_tool, ctx, sample_evidence, sample_verification):
    """LLM synthesis includes verification section when provided."""
    mock_llm.return_value = _mock_narrative_response("Evidence synthesis text.")
    result = await llm_tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
            "verification_result_json": json.dumps(sample_verification),
        },
        ctx,
    )
    assert result["method"] == "llm"
    assert "## Verification Results" in result["report"]
    assert result["has_verification"] is True


@patch("lifesci_tools.report_generator._litellm_acompletion")
async def test_quick_answer_ignores_llm(mock_llm, llm_tool, ctx, sample_evidence):
    """quick_answer mode uses template regardless of model config."""
    result = await llm_tool._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "What is metformin?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["method"] == "template"
    assert "[[cite:" in result["report"]
    mock_llm.assert_not_called()


@patch("lifesci_tools.report_generator._litellm_acompletion")
async def test_research_brief_ignores_llm(mock_llm, llm_tool, ctx, sample_evidence):
    """research_brief mode uses template regardless of model config."""
    result = await llm_tool._run_async_impl(
        {
            "mode": "research_brief",
            "question": "Efficacy of metformin?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["method"] == "template"
    assert "## Background" in result["report"]
    mock_llm.assert_not_called()


async def test_no_model_config_uses_template(tool, ctx, sample_evidence):
    """Tool without model config uses template for all modes."""
    result = await tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["method"] == "template"
    assert "## Executive Summary" in result["report"]


@patch("lifesci_tools.report_generator._litellm_acompletion")
async def test_llm_empty_response_falls_back(mock_llm, llm_tool, ctx, sample_evidence):
    """Empty LLM response triggers template fallback."""
    mock_llm.return_value = _mock_narrative_response("")
    result = await llm_tool._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Test?",
            "evidence_json": json.dumps(sample_evidence),
        },
        ctx,
    )
    assert result["method"] == "template"


def test_init_with_dict_model():
    """YAML anchor expansion: model config as dict."""
    tool = ReportGeneratorTool(tool_config={
        "model": {
            "model": "vertex_ai/gemini-2.5-flash",
            "vertex_project": "my-project",
            "vertex_location": "us-central1",
        }
    })
    assert tool.model == "vertex_ai/gemini-2.5-flash"
    assert tool._vertex_kwargs == {"vertex_project": "my-project", "vertex_location": "us-central1"}


def test_init_without_model():
    """No model config → empty model string."""
    tool = ReportGeneratorTool()
    assert tool.model == ""


# ── research_brief conclusion quality ────────────────────────


async def test_research_brief_conclusion_not_generic(tool, ctx, sample_evidence):
    """research_brief conclusion should NOT be the generic 'Further investigation' text."""
    result = await tool._run_async_impl(
        {
            "question": "What is the safest anesthesia for hemophilia patients?",
            "evidence_json": json.dumps(sample_evidence),
            "mode": "research_brief",
        },
        ctx,
    )
    report = result["report"]
    assert "preliminary findings are presented above" not in report
    assert "Further investigation may be warranted" not in report
    assert "## Conclusion" in report
    assert "clinical" in report.lower()  # Should reference clinical context


async def test_research_brief_single_source_conclusion(tool, ctx):
    """Single source should get a cautionary conclusion."""
    single = [{"title": "Only Study", "snippet": "Only finding", "cite_id": "s0r0"}]
    result = await tool._run_async_impl(
        {
            "question": "Test question?",
            "evidence_json": json.dumps(single),
            "mode": "research_brief",
        },
        ctx,
    )
    report = result["report"]
    assert "single source" in report.lower()
    assert "caution" in report.lower()
