"""Tests for cold_store — prompt version CRUD, scoring, promotion, and pruning."""

import json
import sqlite3

import pytest

from lifesci_tools import cold_store


@pytest.fixture(autouse=True)
def _clear_ddl_cache():
    """Clear the DDL initialisation cache between tests."""
    cold_store.clear_init_cache()
    yield
    cold_store.clear_init_cache()


@pytest.fixture
def db(tmp_path):
    """Return a connection to a fresh in-memory-style temp cold store."""
    db_path = str(tmp_path / "test-cold.db")
    conn = cold_store.get_connection(db_path)
    yield conn
    conn.close()


# -- schema creation ---------------------------------------------------------


def test_schema_creates_prompt_tables(db):
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = sorted(
        row["name"] for row in cursor if row["name"] != "sqlite_sequence"
    )
    expected = sorted(
        [
            "agent_coactivation",
            "learned_strategies",
            "pending_calibration",
            "prompt_promotions",
            "prompt_scores",
            "prompt_versions",
            "query_patterns",
            "routing_patterns",
            "session_outcomes",
            "source_reliability",
            "triage_outcomes",
            "user_feedback",
        ]
    )
    assert tables == expected


# -- write & get prompt version ----------------------------------------------


def test_write_and_get_prompt_version(db):
    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=0,
        instruction="You are a medical orchestrator.",
        model="openai/gemini-2.5-flash-001",
        temperature=0.2,
        metadata={"origin": "seed"},
        is_active=True,
        is_baseline=True,
    )
    result = cold_store.get_prompt_version(db, "orchestrator", 0)
    assert result is not None
    assert result["agent_name"] == "orchestrator"
    assert result["version"] == 0
    assert result["instruction"] == "You are a medical orchestrator."
    assert result["model"] == "openai/gemini-2.5-flash-001"
    assert result["temperature"] == 0.2
    assert result["metadata"] == {"origin": "seed"}
    assert result["is_active"] is True
    assert result["is_baseline"] is True
    assert result["created_at"] is not None


# -- get_active_prompt -------------------------------------------------------


def test_get_active_prompt_returns_active(db):
    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=0,
        instruction="v0 instruction",
        is_active=True,
    )
    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=1,
        instruction="v1 instruction",
        is_active=False,
    )
    result = cold_store.get_active_prompt(db, "orchestrator")
    assert result is not None
    assert result["version"] == 0
    assert result["instruction"] == "v0 instruction"


# -- promote_prompt ----------------------------------------------------------


def test_promote_prompt(db):
    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=0,
        instruction="v0 instruction",
        is_active=True,
        is_baseline=True,
    )
    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=1,
        instruction="v1 instruction",
        is_active=False,
    )
    cold_store.promote_prompt(db, "orchestrator", to_version=1)

    v1 = cold_store.get_prompt_version(db, "orchestrator", 1)
    assert v1 is not None
    assert v1["is_active"] is True

    v0 = cold_store.get_prompt_version(db, "orchestrator", 0)
    assert v0 is not None
    assert v0["is_active"] is False


# -- get_prompt_history ------------------------------------------------------


def test_get_prompt_history(db):
    for i in range(3):
        cold_store.write_prompt_version(
            db,
            agent_name="orchestrator",
            version=i,
            instruction=f"v{i} instruction",
        )
    history = cold_store.get_prompt_history(db, "orchestrator")
    assert len(history) == 3
    # Newest first
    assert history[0]["version"] == 2
    assert history[1]["version"] == 1
    assert history[2]["version"] == 0


# -- get_next_prompt_version -------------------------------------------------


def test_get_next_prompt_version(db):
    # Empty agent returns 0
    assert cold_store.get_next_prompt_version(db, "orchestrator") == 0

    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=0,
        instruction="v0 instruction",
    )
    assert cold_store.get_next_prompt_version(db, "orchestrator") == 1

    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=1,
        instruction="v1 instruction",
    )
    assert cold_store.get_next_prompt_version(db, "orchestrator") == 2


# -- write & get prompt score ------------------------------------------------


def test_write_and_get_prompt_score(db):
    # FK requirement: session_outcomes row must exist first
    cold_store.write_session_outcome(db, session_id="sess-1", query_domain="drug")

    cold_store.write_prompt_score(
        db,
        agent_name="orchestrator",
        prompt_version=0,
        session_id="sess-1",
        scores={
            "entity": 0.9,
            "fidelity": 0.85,
            "citation": 0.8,
            "quality": 0.88,
            "llm_judge": 0.92,
            "composite": 0.87,
        },
    )
    db.commit()

    results = cold_store.get_rolling_scores(db, "orchestrator", n_sessions=5)
    assert len(results) == 1
    assert results[0]["agent_name"] == "orchestrator"
    assert results[0]["prompt_version"] == 0
    assert results[0]["session_id"] == "sess-1"
    assert results[0]["entity"] == 0.9
    assert results[0]["fidelity"] == 0.85
    assert results[0]["citation"] == 0.8
    assert results[0]["quality"] == 0.88
    assert results[0]["llm_judge"] == 0.92
    assert results[0]["composite"] == 0.87


# -- get_rolling_scores limits -----------------------------------------------


def test_get_rolling_scores_limits(db):
    for i in range(10):
        sid = f"sess-{i}"
        cold_store.write_session_outcome(db, session_id=sid, query_domain="drug")
        cold_store.write_prompt_score(
            db,
            agent_name="orchestrator",
            prompt_version=0,
            session_id=sid,
            scores={"composite": 0.5 + i * 0.05},
        )
    db.commit()

    results = cold_store.get_rolling_scores(db, "orchestrator", n_sessions=5)
    assert len(results) == 5
    # All 10 sessions written; only 5 returned (the limit works)
    all_results = cold_store.get_rolling_scores(db, "orchestrator", n_sessions=100)
    assert len(all_results) == 10


# -- get_aggregate_score -----------------------------------------------------


def test_get_aggregate_score(db):
    for i in range(3):
        sid = f"agg-sess-{i}"
        cold_store.write_session_outcome(db, session_id=sid, query_domain="drug")
        cold_store.write_prompt_score(
            db,
            agent_name="orchestrator",
            prompt_version=0,
            session_id=sid,
            scores={
                "entity": 0.8 + i * 0.05,
                "fidelity": 0.7 + i * 0.05,
                "citation": 0.6 + i * 0.05,
                "quality": 0.9 + i * 0.02,
                "composite": 0.75 + i * 0.05,
            },
        )
    db.commit()

    agg = cold_store.get_aggregate_score(db, "orchestrator", prompt_version=0)
    assert agg["sample_count"] == 3
    # avg composite: (0.75 + 0.80 + 0.85) / 3 = 0.8
    assert abs(agg["avg_composite"] - 0.8) < 0.01
    # avg entity: (0.8 + 0.85 + 0.9) / 3 = 0.85
    assert abs(agg["avg_entity"] - 0.85) < 0.01
    # avg fidelity: (0.7 + 0.75 + 0.8) / 3 = 0.75
    assert abs(agg["avg_fidelity"] - 0.75) < 0.01
    # avg citation: (0.6 + 0.65 + 0.7) / 3 = 0.65
    assert abs(agg["avg_citation"] - 0.65) < 0.01


# -- write_prompt_promotion --------------------------------------------------


def test_write_prompt_promotion(db):
    row_id = cold_store.write_prompt_promotion(
        db,
        agent_name="orchestrator",
        from_version=0,
        to_version=1,
        reason="composite improved by 12%",
        composite_before=0.75,
        composite_after=0.87,
        regression_detail={"tests_passed": 42, "tests_failed": 0},
    )
    assert row_id is not None

    row = db.execute(
        "SELECT * FROM prompt_promotions WHERE id = ?", (row_id,)
    ).fetchone()
    assert row["agent_name"] == "orchestrator"
    assert row["from_version"] == 0
    assert row["to_version"] == 1
    assert row["reason"] == "composite improved by 12%"
    assert row["composite_before"] == 0.75
    assert row["composite_after"] == 0.87
    assert row["regression_passed"] == 1
    detail = json.loads(row["regression_detail"])
    assert detail["tests_passed"] == 42
    assert detail["tests_failed"] == 0


# -- prune_prompt_versions ---------------------------------------------------


def test_prune_prompt_versions(db):
    # v0 is baseline, v54 is active — both must survive pruning
    cold_store.write_prompt_version(
        db,
        agent_name="orchestrator",
        version=0,
        instruction="baseline v0",
        is_baseline=True,
        is_active=False,
    )
    for v in range(1, 55):
        cold_store.write_prompt_version(
            db,
            agent_name="orchestrator",
            version=v,
            instruction=f"v{v} instruction",
            is_active=(v == 54),
        )

    # 55 total versions (v0 through v54)
    count_before = db.execute(
        "SELECT COUNT(*) AS cnt FROM prompt_versions WHERE agent_name = 'orchestrator'"
    ).fetchone()["cnt"]
    assert count_before == 55

    deleted = cold_store.prune_prompt_versions(db, "orchestrator", max_versions=50)
    assert deleted == 5  # 55 - 50 = 5 oldest non-baseline non-active removed

    count_after = db.execute(
        "SELECT COUNT(*) AS cnt FROM prompt_versions WHERE agent_name = 'orchestrator'"
    ).fetchone()["cnt"]
    assert count_after == 50

    # Baseline (v0) must still exist
    v0 = cold_store.get_prompt_version(db, "orchestrator", 0)
    assert v0 is not None
    assert v0["is_baseline"] is True

    # Active (v54) must still exist
    v54 = cold_store.get_prompt_version(db, "orchestrator", 54)
    assert v54 is not None
    assert v54["is_active"] is True


# -- update_llm_judge_score --------------------------------------------------


def test_update_llm_judge_score(db):
    cold_store.write_session_outcome(db, session_id="sess-1", query_domain="drug")

    cold_store.write_prompt_score(
        db,
        agent_name="orchestrator",
        prompt_version=0,
        session_id="sess-1",
        scores={
            "entity": 0.9,
            "fidelity": 0.85,
            "composite": 0.80,
        },
    )
    db.commit()

    # Verify llm_judge is None initially
    scores_before = cold_store.get_rolling_scores(db, "orchestrator", n_sessions=1)
    assert scores_before[0]["llm_judge"] is None

    # Backfill the LLM judge score
    cold_store.update_llm_judge_score(
        db,
        agent_name="orchestrator",
        session_id="sess-1",
        llm_judge_score=0.91,
        new_composite=0.88,
    )

    scores_after = cold_store.get_rolling_scores(db, "orchestrator", n_sessions=1)
    assert scores_after[0]["llm_judge"] == 0.91
    assert scores_after[0]["composite"] == 0.88
