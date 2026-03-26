"""Tests for DeliberationSynthesizerTool — advisory board consensus analysis."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lifesci_tools.deliberation_synthesizer import DeliberationSynthesizerTool


@pytest.fixture
def tool():
    """Tool without model config — keyword fallback only."""
    return DeliberationSynthesizerTool()


@pytest.fixture
def llm_tool():
    """Tool with model config — LLM deliberation enabled."""
    return DeliberationSynthesizerTool(tool_config={
        "model": "openai/gemini-2.5-flash-001",
        "temperature": 0.3,
    })


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.session = MagicMock()
    mock.session.id = "test-session-delib"
    return mock


def _make_perspectives(items):
    return json.dumps(items)


def _mock_llm_response(result_dict: dict):
    """Build a mock litellm acompletion response."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json.dumps(result_dict)
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 250
    mock_resp.usage.completion_tokens = 300
    return mock_resp


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
    assert len(result.get("minority_dissent", [])) == 0


# ── split opinions ────────────────────────────────────────────


async def test_divergent_perspectives(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "Clinical Pragmatist", "perspective_text": "Surgery is the optimal treatment with fast recovery and high success rate."},
        {"persona": "Health Economist", "perspective_text": "Conservative medication therapy provides better cost-effectiveness and value."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["consensus_score"] < 0.5
    assert len(result.get("disagreement_themes", [])) > 0


# ── minority dissent ──────────────────────────────────────────


async def test_minority_dissent_detected(tool, ctx):
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Drug therapy is effective for chronic disease management."},
        {"persona": "B", "perspective_text": "Drug therapy shows positive results in disease management."},
        {"persona": "C", "perspective_text": "Drug therapy is recommended for chronic conditions."},
        {"persona": "Dissenter", "perspective_text": "Quantum computing will revolutionize space exploration."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    dissenting_personas = [d["persona"] for d in result.get("minority_dissent", [])]
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
    for d in result.get("minority_dissent", []):
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


# ═══════════════════════════════════════════════════════════════
# LLM deliberation tests
# ═══════════════════════════════════════════════════════════════


@patch("lifesci_tools.deliberation_synthesizer._litellm_acompletion")
async def test_llm_genuine_deliberation(mock_llm, llm_tool, ctx):
    """LLM produces genuine multi-perspective synthesis."""
    mock_llm.return_value = _mock_llm_response({
        "consensus_points": ["Metformin is effective first-line therapy"],
        "contested_points": [
            {
                "point": "Whether SGLT2 inhibitors should be co-prescribed",
                "positions": [
                    {"persona": "Clinical Pragmatist", "position": "Yes, for cardiac benefit", "strength": "strong"},
                    {"persona": "Health Economist", "position": "Only if cost-effective", "strength": "moderate"},
                ],
                "resolution_note": "Cardiac benefit data is strong; cost depends on formulary",
            }
        ],
        "blind_spots": ["No discussion of patient adherence barriers"],
        "synthesis": "The advisory board agrees on metformin as first-line. The key debate is around SGLT2 co-prescription.",
        "confidence_level": "moderate",
        "key_uncertainty": "Long-term cardiovascular outcome data for combination therapy",
    })
    perspectives = _make_perspectives([
        {"persona": "Clinical Pragmatist", "perspective_text": "Metformin plus SGLT2 for cardiac benefit."},
        {"persona": "Health Economist", "perspective_text": "Metformin alone is most cost-effective."},
    ])
    result = await llm_tool._run_async_impl({"perspectives_json": perspectives}, ctx)

    assert result["method"] == "llm"
    assert result["analysis_method"] == "llm_deliberation"
    assert len(result["consensus_points"]) == 1
    assert len(result["contested_points"]) == 1
    assert result["contested_points"][0]["resolution_note"] != ""
    assert len(result["blind_spots"]) == 1
    assert result["confidence_level"] == "moderate"
    assert result["key_uncertainty"] != ""
    # Backward-compat keyword metrics still present
    assert "consensus_score" in result
    assert "consensus_themes" in result
    # LLM path should NOT include the "single LLM simulation" disclaimer
    assert "limitation" not in result
    mock_llm.assert_called_once()


@patch("lifesci_tools.deliberation_synthesizer._litellm_acompletion")
async def test_llm_failure_falls_back_to_keyword(mock_llm, llm_tool, ctx):
    """LLM failure → keyword fallback."""
    mock_llm.side_effect = Exception("LLM timeout")
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Metformin is effective."},
        {"persona": "B", "perspective_text": "Metformin shows efficacy."},
    ])
    result = await llm_tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["method"] == "keyword"
    assert result["analysis_method"] == "single_model_simulation"
    assert "single LLM" in result["limitation"]


@patch("lifesci_tools.deliberation_synthesizer._litellm_acompletion")
async def test_llm_markdown_fence_stripping(mock_llm, llm_tool, ctx):
    """LLM response in markdown fences is parsed correctly."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = (
        "```json\n"
        '{"consensus_points": ["agree"], "contested_points": [], '
        '"blind_spots": [], "synthesis": "All agree.", '
        '"confidence_level": "high", "key_uncertainty": "none"}'
        "\n```"
    )
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50
    mock_llm.return_value = mock_resp

    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Treatment works."},
        {"persona": "B", "perspective_text": "Treatment is effective."},
    ])
    result = await llm_tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["method"] == "llm"
    assert result["confidence_level"] == "high"


@patch("lifesci_tools.deliberation_synthesizer._litellm_acompletion")
async def test_llm_malformed_json_falls_back(mock_llm, llm_tool, ctx):
    """Malformed JSON from LLM triggers keyword fallback."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "Not valid JSON"
    mock_resp.usage = MagicMock()
    mock_resp.usage.prompt_tokens = 50
    mock_resp.usage.completion_tokens = 10
    mock_llm.return_value = mock_resp

    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Treatment works."},
        {"persona": "B", "perspective_text": "Treatment works too."},
    ])
    result = await llm_tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["method"] == "keyword"


async def test_no_model_config_uses_keyword(tool, ctx):
    """Tool without model config uses keyword analysis."""
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "Metformin is effective."},
        {"persona": "B", "perspective_text": "Metformin shows efficacy."},
    ])
    result = await tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["method"] == "keyword"


@patch("lifesci_tools.deliberation_synthesizer._litellm_acompletion")
async def test_llm_perspective_count_correct(mock_llm, llm_tool, ctx):
    """perspective_count reflects actual input count on LLM path."""
    mock_llm.return_value = _mock_llm_response({
        "consensus_points": [],
        "contested_points": [],
        "blind_spots": [],
        "synthesis": "Diverse views.",
        "confidence_level": "low",
        "key_uncertainty": "Everything",
    })
    perspectives = _make_perspectives([
        {"persona": "A", "perspective_text": "View A."},
        {"persona": "B", "perspective_text": "View B."},
        {"persona": "C", "perspective_text": "View C."},
    ])
    result = await llm_tool._run_async_impl({"perspectives_json": perspectives}, ctx)
    assert result["perspective_count"] == 3


def test_init_with_dict_model():
    """YAML anchor expansion: model config as dict."""
    tool = DeliberationSynthesizerTool(tool_config={
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
    tool = DeliberationSynthesizerTool()
    assert tool.model == ""
