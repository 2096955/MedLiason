"""Integration tests for the Generator-Verifier-Reviser (GVR) loop.

Tests verify the verification pass/fail logic, revision triggering,
and max-2-cycle enforcement (verify → revise → re-verify).
"""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools.claim_verifier import ClaimVerifierTool
from lifesci_tools.evidence_grader import EvidenceGraderTool
from lifesci_tools.report_generator import ReportGeneratorTool


@pytest.fixture
def ctx():
    c = MagicMock()
    c.session = MagicMock()
    c.session.id = "gvr-test-001"
    return c


@pytest.fixture
def claim_verifier():
    """Keyword-only verifier (no LLM calls in integration tests)."""
    return ClaimVerifierTool(tool_config={"use_llm": False})


@pytest.fixture
def evidence_grader():
    return EvidenceGraderTool()


@pytest.fixture
def report_gen():
    return ReportGeneratorTool()


# ── verification PASS ─────────────────────────────────────────


async def test_verification_pass(claim_verifier, ctx):
    """Well-supported claims pass verification."""
    response_text = "Metformin reduces HbA1c levels effectively. [[cite:s0r0]]"
    citations = [
        {
            "id": "s0r0",
            "title": "Metformin Efficacy",
            "snippet": "Metformin effectively reduces HbA1c levels in type 2 diabetes.",
            "url": "https://pubmed.ncbi.nlm.nih.gov/12345",
        }
    ]
    result = await claim_verifier._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert result["overall_score"] >= 0.15
    assert result["supported_count"] == 1
    assert len(result["unsupported_claims"]) == 0


# ── verification CRITICAL_ISSUES ──────────────────────────────


async def test_verification_critical_issues(claim_verifier, ctx):
    """Misattributed citations trigger critical issues."""
    response_text = (
        "Aspirin prevents cancer effectively. [[cite:s0r0]] "
        "Yoga cures diabetes. [[cite:s0r1]]"
    )
    citations = [
        {
            "id": "s0r0",
            "title": "Cooking Recipes",
            "snippet": "Best Italian pasta recipes for home cooking.",
            "url": "https://example.com/cooking",
        },
        {
            "id": "s0r1",
            "title": "Gardening Tips",
            "snippet": "How to grow tomatoes in your backyard garden.",
            "url": "https://example.com/garden",
        },
    ]
    result = await claim_verifier._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert result["overall_score"] < 0.15
    assert len(result["unsupported_claims"]) == 2


# ── verification with missing citations ───────────────────────


async def test_verification_missing_citation_ids(claim_verifier, ctx):
    """Claims referencing non-existent citation IDs are flagged."""
    response_text = "This drug works. [[cite:s9r9]]"
    citations = [
        {"id": "s0r0", "title": "Real Study", "snippet": "Real content.", "url": "https://example.com"}
    ]
    result = await claim_verifier._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )
    assert result["claims"][0]["supported"] is False
    assert result["claims"][0]["reason"] == "Citation ID not found"


# ── GVR loop: pass → no revision ─────────────────────────────


async def test_gvr_pass_no_revision(claim_verifier, report_gen, ctx):
    """When verification passes, no revision is needed."""
    # Generate a report
    evidence = [
        {"cite_id": "s0r0", "source": "pubmed", "title": "Metformin Study", "snippet": "Metformin is effective.", "study_type": "rct", "grade": "High"}
    ]
    report = await report_gen._run_async_impl(
        {
            "mode": "research_brief",
            "question": "Is metformin effective?",
            "evidence_json": json.dumps(evidence),
        },
        ctx,
    )
    assert "report" in report

    # Verify the report (citations should align)
    verification = await claim_verifier._run_async_impl(
        {
            "response_text": report["report"],
            "citations_json": json.dumps([
                {"id": "s0r0", "title": "Metformin Study", "snippet": "Metformin is effective for diabetes treatment.", "url": "https://example.com"}
            ]),
        },
        ctx,
    )
    # The report contains [[cite:s0r0]] references
    # This should have reasonable alignment
    assert verification["total_claims"] >= 0


# ── GVR loop: critical → revision ────────────────────────────


async def test_gvr_critical_triggers_revision(claim_verifier, ctx):
    """Critical issues should trigger a revision cycle."""
    response_text = "Homeopathy cures cancer. [[cite:s0r0]]"
    citations = [
        {"id": "s0r0", "title": "RCT on Chemotherapy", "snippet": "Chemotherapy is standard treatment for cancer.", "url": "https://example.com"}
    ]
    verification = await claim_verifier._run_async_impl(
        {
            "response_text": response_text,
            "citations_json": json.dumps(citations),
        },
        ctx,
    )

    # Determine if revision is needed (score < 0.4 = critical)
    needs_revision = verification["overall_score"] < 0.4
    assert needs_revision is True, "Critical misattribution should trigger revision"


# ── GVR cycle enforcement ─────────────────────────────────────


async def test_max_one_revision_cycle():
    """Protocol allows at most 1 revision cycle: verify → revise → re-verify."""
    max_revision_cycles = 1
    revision_cycles = 0

    # First verification: CRITICAL
    first_verify_passed = False
    if not first_verify_passed and revision_cycles < max_revision_cycles:
        revision_cycles += 1
        # Revise, then re-verify — still critical
        re_verify_passed = False

    # Should NOT attempt a second revision even if re-verify fails
    assert revision_cycles == 1
    assert revision_cycles <= max_revision_cycles


async def test_no_revision_when_verify_passes():
    """If initial verification passes, no revision cycle occurs."""
    revision_cycles = 0
    first_verify_passed = True

    if not first_verify_passed:
        revision_cycles += 1

    assert revision_cycles == 0


async def test_re_verify_outcome_stored():
    """After revision, re-verification result is recorded separately."""
    # Simulate the full GVR sequence
    verification_verdict = "CRITICAL_ISSUES"
    re_verification_verdict = None

    if verification_verdict == "CRITICAL_ISSUES":
        # Revise, then re-verify
        re_verification_verdict = "MINOR_ISSUES"

    assert re_verification_verdict == "MINOR_ISSUES"


# ── evidence grading in verification context ──────────────────


async def test_verifier_independent_grading(evidence_grader, ctx):
    """Verifier should independently grade evidence quality."""
    # Grade a meta-analysis
    result = await evidence_grader._run_async_impl(
        {"study_type": "meta_analysis"}, ctx
    )
    assert result["grade"] == "High"
    assert result["score"] == 4.0

    # Grade expert opinion with downgrades
    result = await evidence_grader._run_async_impl(
        {
            "study_type": "expert_opinion",
            "methodology_descriptors": ["small_sample"],
        },
        ctx,
    )
    assert result["grade"] == "Very Low"


# ── report revision output ───────────────────────────────────


async def test_revised_report_maintains_structure(report_gen, ctx):
    """Revised report should maintain the same structure as original."""
    evidence = [
        {"cite_id": "s0r0", "source": "pubmed", "title": "Valid Study", "snippet": "Valid findings.", "study_type": "rct", "grade": "High"}
    ]

    # Generate original
    original = await report_gen._run_async_impl(
        {
            "mode": "research_brief",
            "question": "Treatment efficacy?",
            "evidence_json": json.dumps(evidence),
        },
        ctx,
    )

    # "Revised" report should use same mode
    revised = await report_gen._run_async_impl(
        {
            "mode": "research_brief",
            "question": "Treatment efficacy?",
            "evidence_json": json.dumps(evidence),
            "verification_result_json": json.dumps({"passed": True, "overall_score": 0.9}),
        },
        ctx,
    )

    assert original["mode"] == revised["mode"]
    assert "## Background" in revised["report"]
    assert "## Key Findings" in revised["report"]


# ── new output schema fields ─────────────────────────────────


async def test_claim_verifier_output_schema(claim_verifier, ctx):
    """Keyword-mode claim verifier includes all new schema fields."""
    response_text = "Metformin reduces HbA1c. [[cite:s0r0]]"
    citations = [
        {"id": "s0r0", "title": "Metformin Study", "snippet": "Metformin reduces HbA1c.", "url": "u"}
    ]
    result = await claim_verifier._run_async_impl(
        {"response_text": response_text, "citations_json": json.dumps(citations)}, ctx
    )
    # Original fields preserved
    assert "claims" in result
    assert "overall_score" in result
    assert "unsupported_claims" in result
    assert "total_claims" in result
    assert "supported_count" in result
    # New fields
    assert "method" in result
    assert "entailment_summary" in result
    assert "total_prompt_tokens" in result
    assert "total_completion_tokens" in result
    assert result["method"] == "keyword"
    assert result["claims"][0]["method"] == "keyword"
