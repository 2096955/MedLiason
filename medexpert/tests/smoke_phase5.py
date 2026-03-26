"""Phase 5 Reasoning Depth — Live Smoke Test.

Instantiates each upgraded tool with a real model config and fires
actual LLM calls against Vertex AI.  Requires GOOGLE_APPLICATION_CREDENTIALS
or ADC configured.

Usage:
    cd medexpert
    python tests/smoke_phase5.py
"""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from unittest.mock import MagicMock

# Ensure src/ is on the path (matches pyproject.toml pythonpath)
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))

# Load .env for GEMINI_API_KEY
_env_path = _root / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Tool configs matching shared_config.yaml / shared_config_pro.yaml / shared_config_opus.yaml
MODEL_CONFIGS = {
    "gemini-api": {
        "model": "gemini/gemini-2.5-flash-preview-05-20",
        "temperature": 0.2,
    },
    "flash": {
        "model": {
            "model": "vertex_ai/gemini-2.5-flash",
            "vertex_project": "gbg-neuro",
            "vertex_location": "us-central1",
        },
        "temperature": 0.2,
    },
    "pro": {
        "model": {
            "model": "vertex_ai/gemini-3.1-pro-preview",
            "vertex_project": "gbg-neuro",
            "vertex_location": "global",
        },
        "temperature": 0.2,
    },
    "opus": {
        "model": {
            "model": "vertex_ai/claude-opus-4-6",
            "vertex_project": "gbg-neuro",
            "vertex_location": "global",
        },
        "temperature": 0.2,
    },
}

# Default — overridden by CLI arg
TOOL_CONFIG = MODEL_CONFIGS["flash"]

SAMPLE_EVIDENCE = [
    {
        "cite_id": "s0r0",
        "title": "Metformin Efficacy Meta-Analysis (2023)",
        "snippet": "A meta-analysis of 42 RCTs found metformin reduces HbA1c by 1.0-1.5% as monotherapy.",
        "source": "pubmed",
        "study_type": "meta_analysis",
        "grade": "High",
        "domain": "drugs",
    },
    {
        "cite_id": "s0r1",
        "title": "SGLT2 Inhibitor Cardiovascular Outcomes Trial",
        "snippet": "Empagliflozin reduced cardiovascular death by 38% in patients with T2D and established CVD.",
        "source": "clinicaltrials",
        "study_type": "rct",
        "grade": "High",
        "domain": "clinical_trials",
    },
    {
        "cite_id": "s0r2",
        "title": "GLP-1 RA Weight Loss in Obesity (2024)",
        "snippet": "Semaglutide 2.4mg achieved 15% body weight loss vs 2.4% with placebo over 68 weeks.",
        "source": "pubmed",
        "study_type": "rct",
        "grade": "High",
        "domain": "drugs",
    },
]

SAMPLE_PERSPECTIVES = [
    {"persona": "Clinical Pragmatist", "perspective_text": "Metformin remains the pragmatic first-line choice due to decades of safety data and low cost. SGLT2 inhibitors should be added for patients with cardiovascular risk."},
    {"persona": "Research Methodologist", "perspective_text": "The evidence quality for metformin is high but most trials are older. Newer agents like GLP-1 RAs have more rigorous modern trial designs with cardiovascular endpoints."},
    {"persona": "Health Economist", "perspective_text": "Generic metformin is by far the most cost-effective option. Brand-name GLP-1 RAs cost 50-100x more per month and may not be sustainable for health systems."},
]

QUESTION = "What is the best first-line treatment for type 2 diabetes with cardiovascular risk?"


def mock_ctx():
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.id = "smoke-test-phase5"
    return ctx


async def smoke_completeness_checker():
    from lifesci_tools.completeness_checker import CompletenessCheckerTool
    tool = CompletenessCheckerTool(tool_config=TOOL_CONFIG)
    plan = {
        "sub_questions": [
            {"question": "What is the efficacy of metformin for T2D?", "domain": "drugs", "target_agent": "DrugSpecialist", "priority": 1},
            {"question": "What cardiovascular outcomes exist for SGLT2 inhibitors?", "domain": "clinical_trials", "target_agent": "ClinicalTrialsSpecialist", "priority": 2},
            {"question": "What are the genomic markers for diabetes treatment response?", "domain": "genomics", "target_agent": "GenomicsSpecialist", "priority": 3},
        ]
    }
    result = await tool._run_async_impl(
        {"decomposition_plan_json": json.dumps(plan), "collected_evidence_json": json.dumps(SAMPLE_EVIDENCE)},
        mock_ctx(),
    )
    assert result["method"] in ("llm", "mixed"), f"Expected LLM method, got {result['method']}"
    assert "proceed" in result
    return result


async def smoke_reflection_analyzer():
    from lifesci_tools.reflection_analyzer import ReflectionAnalyzerTool
    tool = ReflectionAnalyzerTool(tool_config=TOOL_CONFIG)
    result = await tool._run_async_impl(
        {"question": QUESTION, "evidence_json": json.dumps(SAMPLE_EVIDENCE), "analysis_depth": "deep"},
        mock_ctx(),
    )
    assert result["method"] == "llm", f"Expected LLM method, got {result['method']}"
    assert "confidence_statement" in result
    return result


async def smoke_report_generator():
    from lifesci_tools.report_generator import ReportGeneratorTool
    tool = ReportGeneratorTool(tool_config=TOOL_CONFIG)
    result = await tool._run_async_impl(
        {"mode": "full_synthesis", "question": QUESTION, "evidence_json": json.dumps(SAMPLE_EVIDENCE)},
        mock_ctx(),
    )
    assert result["method"] == "llm", f"Expected LLM method, got {result['method']}"
    assert "[[cite:" in result["report"], "Expected citation markers in report"
    return result


async def smoke_deliberation_synthesizer():
    from lifesci_tools.deliberation_synthesizer import DeliberationSynthesizerTool
    tool = DeliberationSynthesizerTool(tool_config=TOOL_CONFIG)
    result = await tool._run_async_impl(
        {"perspectives_json": json.dumps(SAMPLE_PERSPECTIVES)},
        mock_ctx(),
    )
    assert result["method"] == "llm", f"Expected LLM method, got {result['method']}"
    assert "contested_points" in result
    return result


async def smoke_evidence_grader():
    from lifesci_tools.evidence_grader import EvidenceGraderTool
    tool = EvidenceGraderTool(tool_config=TOOL_CONFIG)
    result = await tool._run_async_impl(
        {
            "study_type": "rct",
            "study_description": (
                "A double-blind, placebo-controlled randomized trial of 10,000 patients "
                "with type 2 diabetes and established cardiovascular disease. Funded by "
                "Boehringer Ingelheim. Primary endpoint: 3-point MACE. Median follow-up 3.1 years."
            ),
        },
        mock_ctx(),
    )
    assert result["method"] == "llm_enhanced", f"Expected llm_enhanced, got {result['method']}"
    assert "confidence_note" in result
    return result


async def smoke_uncertainty_quantifier():
    from lifesci_tools.uncertainty_quantifier import UncertaintyQuantifierTool
    tool = UncertaintyQuantifierTool(tool_config=TOOL_CONFIG)
    result = await tool._run_async_impl(
        {"question": QUESTION, "evidence_json": json.dumps(SAMPLE_EVIDENCE)},
        mock_ctx(),
    )
    assert result["method"] == "llm", f"Expected LLM method, got {result['method']}"
    assert "per_claim_confidence" in result
    assert len(result["per_claim_confidence"]) > 0, "Expected at least one per-claim entry"
    return result


import logging
logging.basicConfig(level=logging.WARNING)
# Show our tool warnings (which include the LLM response parse errors)
logging.getLogger("medexpert").setLevel(logging.DEBUG)

SMOKE_TESTS = [
    ("R2 — Completeness Checker", smoke_completeness_checker),
    ("R4 — Reflection Analyzer", smoke_reflection_analyzer),
    ("R5 — Report Generator", smoke_report_generator),
    ("R3 — Deliberation Synthesizer", smoke_deliberation_synthesizer),
    ("R1 — Evidence Grader", smoke_evidence_grader),
    ("R7 — Uncertainty Quantifier", smoke_uncertainty_quantifier),
]


async def run_suite(model_name: str):
    """Run all smoke tests for a given model config."""
    global TOOL_CONFIG
    TOOL_CONFIG = MODEL_CONFIGS[model_name]
    raw = TOOL_CONFIG["model"]
    model_id = raw["model"] if isinstance(raw, dict) else raw

    print(f"\n{'=' * 60}")
    print(f"  Model: {model_name} ({model_id})")
    print(f"{'=' * 60}")

    passed = 0
    failed = 0

    for name, test_fn in SMOKE_TESTS:
        print(f"  {name}...", end=" ", flush=True)
        try:
            result = await test_fn()
            method = result.get("method", "?")
            print(f"PASS (method={method})")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"FAIL: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n  {model_name}: {passed}/{len(SMOKE_TESTS)} passed")
    return passed, failed


async def main():
    print("=" * 60)
    print("Phase 5 Reasoning Depth — Live Smoke Test (All Models)")
    print("=" * 60)

    # Parse CLI args: python smoke_phase5.py [flash|pro|opus|all]
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    models = list(MODEL_CONFIGS.keys()) if target == "all" else [target]

    total_passed = 0
    total_failed = 0

    for model_name in models:
        if model_name not in MODEL_CONFIGS:
            print(f"Unknown model: {model_name}. Choose from: {list(MODEL_CONFIGS.keys())}")
            return 1
        p, f = await run_suite(model_name)
        total_passed += p
        total_failed += f

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total_passed} passed, {total_failed} failed across {len(models)} model(s)")
    print(f"{'=' * 60}")

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
