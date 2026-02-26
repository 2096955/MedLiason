"""Tests for the self-evolving prompt system (prompt_evolver.py).

Covers: _agent_to_yaml_filename, _build_failure_feedback,
_check_underperformer, _check_rollback, check_and_evolve,
_run_regression, and seed_baselines.
"""

import json
import os

import pytest
from unittest.mock import patch, MagicMock

from lifesci_tools import cold_store
from lifesci_tools.prompt_evolver import (
    PromptEvolver,
    seed_baselines,
    _agent_to_yaml_filename,
    EVOLUTION_ENABLED,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_ddl_cache():
    cold_store.clear_init_cache()
    yield
    cold_store.clear_init_cache()


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test-cold.db")
    conn = cold_store.get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test-cold.db")


@pytest.fixture
def evolver(tmp_path, db_path):
    golden_path = str(tmp_path / "golden.json")
    # Write a minimal golden dataset
    golden = [
        {
            "question": "What is metformin?",
            "expected_keywords": ["metformin", "diabetes"],
            "expected_agents": ["DrugSpecialist", "LiteratureSpecialist"],
            "min_citation_count": 1,
            "category": "drug",
        }
    ]
    with open(golden_path, "w") as f:
        json.dump(golden, f)
    return PromptEvolver(db_path, golden_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentToYamlFilename:
    """Tests for CamelCase -> snake_case.yaml conversion."""

    def test_agent_to_yaml_filename(self):
        assert _agent_to_yaml_filename("LiteratureSpecialist") == "literature_specialist.yaml"

    def test_single_word(self):
        assert _agent_to_yaml_filename("Orchestrator") == "orchestrator.yaml"

    def test_multi_word(self):
        assert _agent_to_yaml_filename("ClinicalTrialsSpecialist") == "clinical_trials_specialist.yaml"


class TestBuildFailureFeedback:
    """Tests for human-readable failure feedback generation."""

    def test_build_failure_feedback_entity_low(self):
        underperf = {
            "avg_entity": 0.5,
            "avg_fidelity": 0.6,
            "avg_citation": 0.9,
            "avg_quality": 0.8,
        }
        feedback = PromptEvolver(db_path="unused")._build_failure_feedback(underperf)
        assert "Entity preservation is low" in feedback
        assert "0.50" in feedback
        # Should not mention citation since 0.9 >= 0.7
        assert "Citation accuracy" not in feedback

    def test_build_failure_feedback_all_ok(self):
        """When all individual averages are above their thresholds, a generic message appears."""
        underperf = {
            "avg_entity": 0.9,
            "avg_fidelity": 0.5,
            "avg_citation": 0.8,
            "avg_quality": 0.7,
        }
        feedback = PromptEvolver(db_path="unused")._build_failure_feedback(underperf)
        assert "Overall composite score has degraded" in feedback

    def test_build_failure_feedback_multiple_failures(self):
        """Multiple failing dimensions all appear in feedback."""
        underperf = {
            "avg_entity": 0.3,
            "avg_fidelity": 0.1,
            "avg_citation": 0.4,
            "avg_quality": 0.3,
        }
        feedback = PromptEvolver(db_path="unused")._build_failure_feedback(underperf)
        assert "Entity preservation is low" in feedback
        assert "Evidence fidelity is low" in feedback
        assert "Citation accuracy is low" in feedback
        assert "Composite quality is low" in feedback


class TestCheckUnderperformer:
    """Tests for underperformance detection logic."""

    def test_check_underperformer_not_enough_data(self, db, evolver):
        """Fewer than EVOLUTION_ROLLING_WINDOW sessions returns None."""
        # Write only 3 sessions (window is 5)
        cold_store.write_prompt_version(
            db, "DrugSpecialist", 0, "instruction", is_active=True, is_baseline=True
        )
        for i in range(3):
            cold_store.write_session_outcome(db, session_id=f"sess-{i}", query_domain="drug")
            cold_store.write_prompt_score(
                db,
                "DrugSpecialist",
                0,
                f"sess-{i}",
                scores={
                    "entity": 0.3,
                    "fidelity": 0.2,
                    "citation": 0.3,
                    "quality": 0.2,
                    "llm_judge": None,
                    "composite": 0.25,
                },
            )

        result = evolver._check_underperformer(db, "DrugSpecialist")
        assert result is None

    def test_check_underperformer_performing_well(self, db, evolver):
        """Avg composite >= 0.55 means no underperformance detected."""
        cold_store.write_prompt_version(
            db, "DrugSpecialist", 0, "instruction", is_active=True, is_baseline=True
        )
        for i in range(5):
            cold_store.write_session_outcome(db, session_id=f"sess-{i}", query_domain="drug")
            cold_store.write_prompt_score(
                db,
                "DrugSpecialist",
                0,
                f"sess-{i}",
                scores={
                    "entity": 0.9,
                    "fidelity": 0.5,
                    "citation": 0.8,
                    "quality": 0.7,
                    "llm_judge": None,
                    "composite": 0.7,
                },
            )

        result = evolver._check_underperformer(db, "DrugSpecialist")
        assert result is None

    def test_check_underperformer_detected(self, db, evolver):
        """Avg composite < 0.55 returns details dict."""
        cold_store.write_prompt_version(
            db, "DrugSpecialist", 0, "instruction", is_active=True, is_baseline=True
        )
        for i in range(5):
            cold_store.write_session_outcome(db, session_id=f"sess-{i}", query_domain="drug")
            cold_store.write_prompt_score(
                db,
                "DrugSpecialist",
                0,
                f"sess-{i}",
                scores={
                    "entity": 0.4,
                    "fidelity": 0.2,
                    "citation": 0.3,
                    "quality": 0.3,
                    "llm_judge": None,
                    "composite": 0.3,
                },
            )

        result = evolver._check_underperformer(db, "DrugSpecialist")
        assert result is not None
        assert result["agent_name"] == "DrugSpecialist"
        assert result["avg_composite"] < 0.55
        assert "avg_entity" in result
        assert "avg_fidelity" in result
        assert "avg_citation" in result
        assert "avg_quality" in result
        assert "scores" in result
        assert len(result["scores"]) == 5


class TestCheckRollback:
    """Tests for auto-rollback detection."""

    def test_check_rollback_no_active_prompt(self, db, evolver):
        """No active prompt for the agent returns None."""
        result = evolver._check_rollback(db, "DrugSpecialist")
        assert result is None

    def test_check_rollback_baseline_skipped(self, db, evolver):
        """Baseline version (v0, is_baseline=True) is never rolled back."""
        cold_store.write_prompt_version(
            db, "DrugSpecialist", 0, "baseline instruction",
            is_active=True, is_baseline=True,
        )
        result = evolver._check_rollback(db, "DrugSpecialist")
        assert result is None


class TestCheckAndEvolve:
    """Tests for the main check_and_evolve entry point."""

    def test_check_and_evolve_disabled(self, db, evolver):
        """When EVOLUTION_ENABLED is patched to False, returns empty list."""
        with patch("lifesci_tools.prompt_evolver.EVOLUTION_ENABLED", False):
            actions = evolver.check_and_evolve(db)
        assert actions == []


class TestRunRegression:
    """Tests for golden dataset regression."""

    def test_run_regression_no_dataset(self, tmp_path):
        """Missing golden dataset file auto-passes regression."""
        missing_path = str(tmp_path / "nonexistent_golden.json")
        ev = PromptEvolver(str(tmp_path / "test.db"), missing_path)
        result = ev._run_regression("DrugSpecialist", "new instruction text")
        assert result["passed"] is True
        assert result["reason"] == "no_dataset"
        assert result["scores"] == []

    def test_run_regression_no_relevant_items(self, tmp_path):
        """Agent not in any expected_agents list auto-passes."""
        golden_path = str(tmp_path / "golden.json")
        golden = [
            {
                "question": "What is metformin?",
                "expected_keywords": ["metformin"],
                "expected_agents": ["LiteratureSpecialist"],
                "category": "drug",
            }
        ]
        with open(golden_path, "w") as f:
            json.dump(golden, f)

        ev = PromptEvolver(str(tmp_path / "test.db"), golden_path)
        result = ev._run_regression("GenomicsSpecialist", "instruction")
        assert result["passed"] is True
        assert result["reason"] == "no_relevant_items"
