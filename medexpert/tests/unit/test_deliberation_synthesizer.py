"""Tests for DeliberationSynthesizerTool — advisory board consensus analysis."""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools.deliberation_synthesizer import DeliberationSynthesizerTool


@pytest.fixture
def tool():
    return DeliberationSynthesizerTool()


@pytest.fixture
def ctx():
    return MagicMock()


def _make_perspectives(items):
    return json.dumps(items)


# ── unanimous consensus ───────────────────────────────────────


async def test_high_consensus(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "Clinical Pragmatist", "perspective_text": "Metformin is effective and safe for diabetes treatment with strong evidence."},
        {"persona": "Research Methodologist", "perspective_text": "The evidence for metformin effectiveness and safety in diabetes is well-supported."},
        {"persona": "Patient Advocate", "perspective_text": "Metformin treatment for diabetes is effective with acceptable safety profile."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["consensus_score"] > 0.0
    assert len(result["consensus_themes"]) > 0
    assert result["perspective_count"] == 3


async def test_identical_perspectives_max_consensus(tool, ctx):
    text = "Metformin is the best first-line treatment for diabetes."
    perspectives = _make_perspectives([
        {"persona": "Persona A", "perspective_text": text},
        {"persona": "Persona B", "perspective_text": text},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["consensus_score"] == 1.0
    assert len(result["minority_dissent"]) == 0


# ── split opinions ────────────────────────────────────────────


async def test_divergent_perspectives(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "Clinical Pragmatist", "perspective_text": "Surgery is the optimal treatment with fast recovery and high success rate."},
        {"persona": "Health Economist", "perspective_text": "Conservative medication therapy provides better cost-effectiveness and value."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["consensus_score"] < 0.5
    assert len(result["disagreement_themes"]) > 0


# ── minority dissent ──────────────────────────────────────────


async def test_minority_dissent_detected(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Drug therapy is effective for chronic disease management."},
        {"persona": "B", "perspective_text": "Drug therapy shows positive results in disease management."},
        {"persona": "C", "perspective_text": "Drug therapy is recommended for chronic conditions."},
        {"persona": "Dissenter", "perspective_text": "Quantum computing will revolutionize space exploration."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    dissenting_personas = [d["persona"] for d in result["minority_dissent"]]
    assert "Dissenter" in dissenting_personas


# ── edge cases ────────────────────────────────────────────────


async def test_minimum_two_perspectives_required(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "Solo", "perspective_text": "Single opinion."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert "error" in result


async def test_empty_perspectives_error(tool, ctx):
    result = await tool._run_async_impl({"perspectives_json": "[]"}, ctx)
    assert "error" in result


async def test_invalid_json_error(tool, ctx):
    result = await tool._run_async_impl({"perspectives_json": "not json"}, ctx)
    assert "error" in result


# ── synthesis text ────────────────────────────────────────────


async def test_synthesis_text_generated(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Metformin is effective."},
        {"persona": "B", "perspective_text": "Metformin shows efficacy."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert len(result["synthesis"]) > 0
    assert "Consensus" in result["synthesis"] or "perspectives" in result["synthesis"]


async def test_no_consensus_synthesis(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Alpha beta gamma delta epsilon."},
        {"persona": "B", "perspective_text": "Zeta theta iota kappa lambda."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert "synthesis" in result


# ── six advisory board personas ───────────────────────────────


async def test_full_advisory_board(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "Clinical Pragmatist", "perspective_text": "From a clinical perspective, the evidence supports treatment with monitoring."},
        {"persona": "Research Methodologist", "perspective_text": "The research methodology has limitations but evidence quality is moderate."},
        {"persona": "Patient Advocate", "perspective_text": "Patient outcomes and quality of life should guide treatment decisions."},
        {"persona": "Health Economist", "perspective_text": "Cost-effectiveness analysis favors generic medications over branded alternatives."},
        {"persona": "Bioethicist", "perspective_text": "Ethical considerations around informed consent and treatment equity."},
        {"persona": "Global Health Specialist", "perspective_text": "Global health disparities affect treatment access and outcomes."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["perspective_count"] == 6
    assert "consensus_score" in result
    assert isinstance(result["consensus_themes"], list)
    assert isinstance(result["disagreement_themes"], list)


# ── theme counts ──────────────────────────────────────────────


async def test_consensus_themes_limited_to_20(tool, ctx):
    long_text = " ".join([f"unique_word_{i}" for i in range(100)])
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": long_text},
        {"persona": "B", "perspective_text": long_text},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert len(result["consensus_themes"]) <= 20


async def test_dissent_unique_themes_limited(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Medication treatment therapy evidence."},
        {"persona": "B", "perspective_text": "Medication treatment therapy evidence."},
        {"persona": "Outlier", "perspective_text": " ".join([f"divergent_{i}" for i in range(50)])},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    for d in result["minority_dissent"]:
        assert len(d["unique_themes"]) <= 10


# ── disclaimer fields ────────────────────────────────────────


async def test_output_includes_disclaimer_fields(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Metformin is effective treatment."},
        {"persona": "B", "perspective_text": "Metformin is effective treatment."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["analysis_method"] == "single_model_simulation"
    assert "single LLM" in result["limitation"]
