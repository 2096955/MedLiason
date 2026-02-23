"""Integration tests for the 12-step orchestration pipeline.

Tests verify the protocol execution order, evidence accumulation,
and report generation with mocked specialist peer responses.
"""

import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from lifesci_tools.query_decomposer import QueryDecomposerTool
from lifesci_tools.completeness_checker import CompletenessCheckerTool
from lifesci_tools.reflection_analyzer import ReflectionAnalyzerTool
from lifesci_tools.deliberation_synthesizer import DeliberationSynthesizerTool
from lifesci_tools.report_generator import ReportGeneratorTool
from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend
from lifesci_tools.phi_redactor import PhiRedactorTool


@pytest.fixture
def ctx():
    c = MagicMock()
    c.session = MagicMock()
    c.session.id = "integration-test-001"
    return c


@pytest.fixture
def memory_plane():
    tool = MemoryPlaneTool()
    MemoryPlaneTool._backend = DictBackend()
    return tool


@pytest.fixture
def decomposer():
    return QueryDecomposerTool()


@pytest.fixture
def completeness():
    return CompletenessCheckerTool()


@pytest.fixture
def reflection():
    return ReflectionAnalyzerTool()


@pytest.fixture
def deliberation():
    return DeliberationSynthesizerTool()


@pytest.fixture
def report_gen():
    return ReportGeneratorTool()


@pytest.fixture
def phi_tool():
    return PhiRedactorTool()


# ── step 1: DECOMPOSE ────────────────────────────────────────


async def test_step1_decompose(decomposer, ctx):
    result = await decomposer._run_async_impl(
        {"question": "What is the efficacy and safety of GLP-1 agonists for type 2 diabetes and what clinical trials are ongoing?"},
        ctx,
    )
    assert result["count"] >= 2
    assert all("target_agent" in sq for sq in result["sub_questions"])


# ── step 3: COLLECT (store evidence) ─────────────────────────


async def test_step3_store_evidence(memory_plane, ctx):
    evidence = {
        "source": "pubmed",
        "pmid": "12345678",
        "title": "GLP-1 Agonist Efficacy",
        "snippet": "Semaglutide showed 1.5% HbA1c reduction.",
        "study_type": "rct",
    }
    result = await memory_plane._run_async_impl(
        {
            "operation": "store",
            "key": "evidence_item_1",
            "value": json.dumps(evidence),
            "namespace": "evidence",
        },
        ctx,
    )
    assert result["success"] is True

    # Retrieve
    retrieved = await memory_plane._run_async_impl(
        {"operation": "retrieve", "key": "evidence_item_1", "namespace": "evidence"},
        ctx,
    )
    assert retrieved["found"] is True
    assert json.loads(retrieved["value"])["pmid"] == "12345678"


# ── step 4: REFLECT ──────────────────────────────────────────


async def test_step4_reflect_on_evidence(reflection, ctx):
    evidence = [
        {"title": "GLP-1 Agonist Efficacy RCT", "snippet": "Effective HbA1c reduction.", "source": "pubmed"},
        {"title": "GLP-1 Safety Profile", "snippet": "GI side effects are common.", "source": "pubmed"},
    ]
    result = await reflection._run_async_impl(
        {
            "question": "What is the efficacy and safety of GLP-1 agonists?",
            "evidence_json": json.dumps(evidence),
            "analysis_depth": "deep",
        },
        ctx,
    )
    assert "gaps" in result
    assert "missing_perspectives" in result


# ── step 6: VALIDATE ─────────────────────────────────────────


async def test_step6_validate_completeness(decomposer, completeness, ctx):
    # First decompose
    decomp = await decomposer._run_async_impl(
        {"question": "What are the treatments for diabetes and what are the side effects?"},
        ctx,
    )

    # Mock evidence that covers first sub-question
    evidence = [
        {"title": "Diabetes treatment options guide", "snippet": "Metformin is first-line treatment for diabetes."},
    ]

    result = await completeness._run_async_impl(
        {
            "decomposition_plan_json": json.dumps(decomp),
            "collected_evidence_json": json.dumps(evidence),
        },
        ctx,
    )
    assert "coverage_pct" in result
    assert "gaps" in result


# ── step 7: ADVISORY BOARD ───────────────────────────────────


async def test_step7_advisory_board(deliberation, ctx):
    perspectives = [
        {"persona": "Clinical Pragmatist", "perspective_text": "GLP-1 agonists are practical for clinical diabetes management with demonstrated efficacy."},
        {"persona": "Research Methodologist", "perspective_text": "The RCT evidence for GLP-1 agonists is strong with appropriate methodology."},
        {"persona": "Patient Advocate", "perspective_text": "Patient quality of life improves with GLP-1 treatment but GI side effects must be discussed."},
        {"persona": "Health Economist", "perspective_text": "Cost-effectiveness of GLP-1 agonists varies by market and insurance coverage."},
        {"persona": "Bioethicist", "perspective_text": "Equitable access to GLP-1 agonists is an ethical concern given high costs."},
        {"persona": "Global Health Specialist", "perspective_text": "GLP-1 availability in low-income countries remains limited."},
    ]
    result = await deliberation._run_async_impl(
        {"perspectives_json": json.dumps(perspectives)}, ctx
    )
    assert result["perspective_count"] == 6
    assert result["consensus_score"] >= 0


# ── step 8: SYNTHESIZE ───────────────────────────────────────


async def test_step8_synthesize_report(report_gen, ctx):
    evidence = [
        {"cite_id": "s0r0", "source": "pubmed", "title": "GLP-1 Meta-Analysis", "snippet": "GLP-1 agonists reduce HbA1c.", "study_type": "meta_analysis", "grade": "High"},
    ]
    result = await report_gen._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "What is the efficacy of GLP-1 agonists?",
            "evidence_json": json.dumps(evidence),
        },
        ctx,
    )
    assert "## Executive Summary" in result["report"]
    assert "[[cite:" in result["report"]


# ── PHI redaction pre-output ──────────────────────────────────


async def test_phi_redaction_before_output(phi_tool, ctx):
    text = "Patient John Smith (SSN 123-45-6789) showed improvement with metformin."
    result = await phi_tool._run_async_impl(
        {"text": text, "return_redacted": True}, ctx
    )
    assert result["entity_count"] >= 1
    assert "123-45-6789" not in result.get("redacted_text", text)


# ── full pipeline simulation ─────────────────────────────────


async def test_full_pipeline_simulation(decomposer, memory_plane, reflection, completeness, deliberation, report_gen, ctx):
    """Simulate the full 12-step protocol with local tools."""
    # Step 1: Decompose
    decomp = await decomposer._run_async_impl(
        {"question": "What is the safety profile of metformin for diabetes treatment?"},
        ctx,
    )
    assert decomp["count"] >= 1

    # Step 3: Collect (simulate specialist response)
    evidence_items = [
        {"title": "Metformin Safety Review", "snippet": "Metformin has favorable safety profile with GI side effects.", "source": "pubmed"},
        {"title": "Metformin Adverse Events", "snippet": "Lactic acidosis is rare but serious adverse event.", "source": "openfda"},
    ]
    for i, ev in enumerate(evidence_items):
        await memory_plane._run_async_impl(
            {"operation": "store", "key": f"ev_{i}", "value": json.dumps(ev), "namespace": "evidence"},
            ctx,
        )

    # Step 4: Reflect
    reflect_result = await reflection._run_async_impl(
        {"question": "Metformin safety profile?", "evidence_json": json.dumps(evidence_items)},
        ctx,
    )
    assert "gaps" in reflect_result

    # Step 6: Validate
    validate_result = await completeness._run_async_impl(
        {
            "decomposition_plan_json": json.dumps(decomp),
            "collected_evidence_json": json.dumps(evidence_items),
        },
        ctx,
    )
    assert "coverage_pct" in validate_result

    # Step 7: Advisory
    perspectives = [
        {"persona": "Clinical Pragmatist", "perspective_text": "Metformin safety is well-established in clinical practice."},
        {"persona": "Patient Advocate", "perspective_text": "Patients should be informed about GI side effects and rare lactic acidosis risk."},
    ]
    advisory_result = await deliberation._run_async_impl(
        {"perspectives_json": json.dumps(perspectives)}, ctx
    )
    assert advisory_result["consensus_score"] >= 0

    # Step 8: Synthesize
    report = await report_gen._run_async_impl(
        {
            "mode": "advisory_board_report",
            "question": "Metformin safety profile?",
            "evidence_json": json.dumps(evidence_items),
            "advisory_perspectives_json": json.dumps(perspectives),
        },
        ctx,
    )
    assert "report" in report
    assert len(report["report"]) > 100


# ── evidence accumulation ────────────────────────────────────


async def test_evidence_accumulation_across_steps(memory_plane, ctx):
    """Evidence stored in step 3 is retrievable in later steps."""
    # Store multiple items
    for i in range(5):
        await memory_plane._run_async_impl(
            {
                "operation": "append",
                "key": "all_evidence",
                "value": json.dumps({"id": i, "finding": f"Finding {i}"}),
                "namespace": "evidence",
            },
            ctx,
        )

    # Retrieve accumulated evidence
    result = await memory_plane._run_async_impl(
        {"operation": "retrieve", "key": "all_evidence", "namespace": "evidence"},
        ctx,
    )
    items = json.loads(result["value"])
    assert len(items) == 5


# ── report mode selection ────────────────────────────────────


async def test_quick_answer_for_simple_question(report_gen, ctx):
    result = await report_gen._run_async_impl(
        {
            "mode": "quick_answer",
            "question": "What is aspirin?",
            "evidence_json": json.dumps([{"cite_id": "s0r0", "snippet": "Aspirin is an NSAID."}]),
        },
        ctx,
    )
    assert result["mode"] == "quick_answer"
    assert len(result["report"]) < 1000


async def test_full_synthesis_for_complex_question(report_gen, ctx):
    evidence = [{"cite_id": f"s0r{i}", "source": "pubmed", "title": f"Study {i}", "snippet": f"Finding {i}.", "study_type": "rct", "grade": "High"} for i in range(5)]
    result = await report_gen._run_async_impl(
        {
            "mode": "full_synthesis",
            "question": "Comprehensive diabetes treatment review?",
            "evidence_json": json.dumps(evidence),
        },
        ctx,
    )
    assert "## Evidence Summary Table" in result["report"]
    assert result["evidence_count"] == 5


# ── memory plane session isolation in pipeline ────────────────


async def test_pipeline_session_isolation(memory_plane):
    ctx1 = MagicMock()
    ctx1.session = MagicMock()
    ctx1.session.id = "pipeline-session-1"

    ctx2 = MagicMock()
    ctx2.session = MagicMock()
    ctx2.session.id = "pipeline-session-2"

    await memory_plane._run_async_impl(
        {"operation": "store", "key": "data", "value": "session1_data", "namespace": "evidence"},
        ctx1,
    )
    await memory_plane._run_async_impl(
        {"operation": "store", "key": "data", "value": "session2_data", "namespace": "evidence"},
        ctx2,
    )

    r1 = await memory_plane._run_async_impl(
        {"operation": "retrieve", "key": "data", "namespace": "evidence"}, ctx1
    )
    r2 = await memory_plane._run_async_impl(
        {"operation": "retrieve", "key": "data", "namespace": "evidence"}, ctx2
    )
    assert r1["value"] == "session1_data"
    assert r2["value"] == "session2_data"
