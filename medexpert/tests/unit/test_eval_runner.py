"""Unit tests for the eval runner."""

import json
import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from eval_runner import (
    EvalSAMClient,
    GoldenDatasetEvaluator,
    RedTeamEvaluator,
    _mock_query,
)

TESTS_DIR = Path(__file__).parent.parent
GOLDEN_PATH = TESTS_DIR / "golden_dataset.json"
RED_TEAM_PATH = TESTS_DIR / "red_team_prompts.json"


# ── EvalSAMClient ─────────────────────────────────────────────


def test_client_constructor_with_url():
    """Client stores gateway URL and timeout."""
    client = EvalSAMClient("http://localhost:8000", timeout=60.0)
    assert client._base_url == "http://localhost:8000"
    assert client._timeout == 60.0


def test_client_strips_trailing_slash():
    """Client strips trailing slash from URL."""
    client = EvalSAMClient("http://localhost:8000/")
    assert client._base_url == "http://localhost:8000"


# ── Mock query ─────────────────────────────────────────────────


async def test_mock_query_diabetes():
    """Mock query about diabetes returns diabetes-related response."""
    result = await _mock_query("What is the first-line treatment for type 2 diabetes?")
    assert "text" in result
    assert "metformin" in result["text"].lower()
    assert "agents_used" in result
    assert len(result["agents_used"]) > 0


async def test_mock_query_cancer():
    """Mock query about cancer returns cancer-related response."""
    result = await _mock_query("What is the evidence for immunotherapy in cancer?")
    assert "text" in result
    assert "immunotherapy" in result["text"].lower() or "checkpoint" in result["text"].lower()


async def test_mock_query_generic():
    """Generic mock query returns mock response text."""
    result = await _mock_query("What is the meaning of life?")
    assert "text" in result
    assert len(result["text"]) > 0


async def test_mock_query_returns_citations():
    """Mock query includes at least one citation."""
    result = await _mock_query("diabetes treatment")
    assert "citations" in result
    assert len(result["citations"]) >= 1


# ── Golden Dataset Evaluator ──────────────────────────────────


@pytest.mark.skipif(
    not GOLDEN_PATH.exists(), reason="golden_dataset.json not found"
)
async def test_golden_evaluator_mock_mode():
    """Golden evaluator runs in mock mode without errors."""
    evaluator = GoldenDatasetEvaluator(str(GOLDEN_PATH), sam_client=None)
    summary = await evaluator.run()

    assert summary["type"] == "golden_dataset"
    assert summary["total"] >= 5
    assert summary["passed"] + summary["failed"] == summary["total"]
    assert 0.0 <= summary["avg_score"] <= 1.0


@pytest.mark.skipif(
    not GOLDEN_PATH.exists(), reason="golden_dataset.json not found"
)
async def test_golden_evaluator_scores_keyword_match():
    """Golden evaluator gives higher scores for keyword matches."""
    evaluator = GoldenDatasetEvaluator(str(GOLDEN_PATH), sam_client=None)
    summary = await evaluator.run()

    # First query is about diabetes — mock responds with metformin, so should score well
    diabetes_result = summary["results"][0]
    assert diabetes_result["category"] == "treatment"
    assert diabetes_result["score"] > 0.0


@pytest.mark.skipif(
    not GOLDEN_PATH.exists(), reason="golden_dataset.json not found"
)
def test_golden_dataset_structure():
    """Golden dataset JSON has required fields."""
    dataset = json.loads(GOLDEN_PATH.read_text())
    assert len(dataset) >= 5
    for item in dataset:
        assert "question" in item
        assert "expected_keywords" in item
        assert isinstance(item["expected_keywords"], list)


# ── Red Team Evaluator ────────────────────────────────────────


@pytest.mark.skipif(
    not RED_TEAM_PATH.exists(), reason="red_team_prompts.json not found"
)
async def test_red_team_evaluator_mock_mode():
    """Red team evaluator runs in mock mode without errors."""
    evaluator = RedTeamEvaluator(str(RED_TEAM_PATH), sam_client=None)
    summary = await evaluator.run()

    assert summary["type"] == "red_team"
    assert summary["total"] >= 4
    assert summary["safe"] + summary["unsafe"] == summary["total"]
    assert 0.0 <= summary["safety_rate"] <= 1.0


@pytest.mark.skipif(
    not RED_TEAM_PATH.exists(), reason="red_team_prompts.json not found"
)
def test_red_team_dataset_structure():
    """Red team dataset JSON has required fields."""
    dataset = json.loads(RED_TEAM_PATH.read_text())
    assert len(dataset) >= 4
    for item in dataset:
        assert "prompt" in item
        assert "category" in item


# ── Scoring edge cases ────────────────────────────────────────


def test_golden_scorer_error_response():
    """Error response gets score 0."""
    evaluator = GoldenDatasetEvaluator.__new__(GoldenDatasetEvaluator)
    item = {
        "question": "test",
        "expected_keywords": ["a"],
        "expected_agents": ["Agent"],
        "min_citation_count": 1,
    }
    response = {"text": "", "error": "Connection refused"}
    score = evaluator._score(item, response)
    assert score == 0.0


def test_red_team_checker_forbidden_keywords():
    """Forbidden keywords in response means unsafe."""
    evaluator = RedTeamEvaluator.__new__(RedTeamEvaluator)
    item = {
        "prompt": "test",
        "forbidden_keywords": ["prescribe", "dosage"],
    }
    response = {"text": "I would prescribe ibuprofen at a dosage of 400mg."}
    safe = evaluator._check_safety(item, response)
    assert safe is False


def test_red_team_checker_clean_response():
    """Response without forbidden keywords is safe."""
    evaluator = RedTeamEvaluator.__new__(RedTeamEvaluator)
    item = {
        "prompt": "test",
        "forbidden_keywords": ["prescribe"],
        "should_refuse": False,
    }
    response = {"text": "I can help with general health information."}
    safe = evaluator._check_safety(item, response)
    assert safe is True
