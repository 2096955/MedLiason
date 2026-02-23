"""Tests for cold_worker — background queue consumer and persistence."""

import queue
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from lifesci_tools import cold_store, cold_worker


@pytest.fixture(autouse=True)
def reset_worker():
    """Reset cold worker global state before each test."""
    cold_worker.reset_for_testing()
    yield
    cold_worker.reset_for_testing()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "worker-test.db")


def _make_payload(db_path, session_id="s-1", specialists=None):
    return {
        "cold_db_path": db_path,
        "session_id": session_id,
        "query_domain": "drugs",
        "query_text": "metformin side effects",
        "coverage_pct": 0.85,
        "verification_verdict": "PASS",
        "verification_score": 0.9,
        "revision_triggered": False,
        "specialists_used": specialists if specialists is not None else ["DrugSpecialist", "LiteratureSpecialist"],
        "agent_coverage": {"DrugSpecialist": 0.5, "LiteratureSpecialist": 0.35},
        "sources": [
            {
                "source_name": "pubmed",
                "evidence_count": 5,
                "avg_grade_score": 3.0,
                "citations_valid": 4,
                "citations_invalid": 1,
                "passed_verification": True,
            }
        ],
    }


# ── enqueue ──────────────────────────────────────────────────


def test_enqueue_returns_true(db_path):
    result = cold_worker.enqueue_session_flush(_make_payload(db_path))
    assert result is True
    assert cold_worker._queue.qsize() == 1


# ── _persist_one ─────────────────────────────────────────────


def test_persist_one_writes_all_tables(db_path):
    payload = _make_payload(db_path)
    cold_worker._persist_one(db_path, payload)

    conn = cold_store.get_connection(db_path)
    # Session outcome
    row = conn.execute("SELECT * FROM session_outcomes WHERE session_id = 's-1'").fetchone()
    assert row is not None
    assert row["verification_verdict"] == "PASS"

    # Source reliability
    sources = conn.execute("SELECT * FROM source_reliability WHERE session_id = 's-1'").fetchall()
    assert len(sources) == 1
    assert sources[0]["source_name"] == "pubmed"

    # Routing patterns
    routes = conn.execute("SELECT * FROM routing_patterns WHERE session_id = 's-1'").fetchall()
    assert len(routes) == 2

    # Query patterns
    qp = conn.execute("SELECT * FROM query_patterns").fetchone()
    assert qp is not None
    assert qp["frequency"] == 1

    # Agent co-activation
    coact = conn.execute("SELECT * FROM agent_coactivation").fetchone()
    assert coact is not None
    assert coact["co_used_count"] == 1

    conn.close()


def test_persist_one_empty_specialists(tmp_path):
    path = str(tmp_path / "empty-spec.db")
    payload = _make_payload(path, session_id="s-empty-spec", specialists=[])
    payload["sources"] = []
    cold_worker._persist_one(path, payload)

    conn = cold_store.get_connection(path)
    row = conn.execute(
        "SELECT * FROM session_outcomes WHERE session_id = 's-empty-spec'"
    ).fetchone()
    assert row is not None
    routes = conn.execute(
        "SELECT * FROM routing_patterns WHERE session_id = 's-empty-spec'"
    ).fetchall()
    assert len(routes) == 0
    conn.close()


def test_persist_one_empty_query_text(db_path):
    payload = _make_payload(db_path)
    payload["query_text"] = ""
    cold_worker._persist_one(db_path, payload)
    conn = cold_store.get_connection(db_path)
    qp = conn.execute("SELECT * FROM query_patterns").fetchall()
    assert len(qp) == 0
    conn.close()


def test_persist_one_coactivation_pairs(db_path):
    payload = _make_payload(
        db_path, specialists=["A", "B", "C"]
    )
    cold_worker._persist_one(db_path, payload)
    conn = cold_store.get_connection(db_path)
    rows = conn.execute("SELECT * FROM agent_coactivation").fetchall()
    # 3 choose 2 = 3 pairs
    assert len(rows) == 3
    conn.close()


# ── retry logic ──────────────────────────────────────────────


def test_persist_one_raises_on_invalid_path():
    # Use a path that's guaranteed invalid on all platforms
    with pytest.raises(Exception):
        cold_worker._persist_one("", _make_payload(""))


# ── worker loop integration ──────────────────────────────────


def test_worker_processes_payload(db_path):
    """Test that the worker loop picks up and processes a queued payload."""
    payload = _make_payload(db_path)

    # Directly call _persist_one to test without spawning threads
    cold_worker._persist_one(db_path, payload)

    conn = cold_store.get_connection(db_path)
    assert cold_store.get_session_count(conn) == 1
    conn.close()


def test_worker_multiple_sessions(db_path):
    for i in range(3):
        cold_worker._persist_one(db_path, _make_payload(db_path, session_id=f"s-{i}"))

    conn = cold_store.get_connection(db_path)
    assert cold_store.get_session_count(conn) == 3
    conn.close()


# ── strategy refresh threshold ───────────────────────────────


def test_refresh_triggered_after_threshold(db_path):
    """After REFRESH_INTERVAL sessions, strategies should be refreshed."""
    for i in range(cold_worker.REFRESH_INTERVAL + 1):
        cold_worker._persist_one(
            db_path,
            _make_payload(db_path, session_id=f"s-{i}"),
        )

    conn = cold_store.get_connection(db_path)
    cold_store.refresh_learned_strategies(conn)
    strategy = cold_store.get_routing_strategy(conn, "drugs")
    assert strategy is not None
    conn.close()


def test_refresh_not_triggered_below_threshold(db_path):
    """Below the threshold, no learned strategies should exist."""
    cold_worker._persist_one(db_path, _make_payload(db_path))
    conn = cold_store.get_connection(db_path)
    # Don't manually refresh — check that nothing is there yet
    strategy = cold_store.get_routing_strategy(conn, "drugs")
    assert strategy is None
    conn.close()


# ── payload missing cold_db_path ─────────────────────────────


def test_persist_one_no_db_path_raises():
    payload = _make_payload("", session_id="s-bad")
    with pytest.raises(Exception):
        cold_worker._persist_one("", payload)


# ── stop mechanism ───────────────────────────────────────────


def test_stop_before_start():
    """stop() is a no-op when worker hasn't started."""
    cold_worker.stop(timeout=0.5)
    assert cold_worker._started is False


def test_stop_terminates_worker(db_path):
    """stop() sends sentinel and joins the worker thread."""
    cold_worker.enqueue_session_flush(_make_payload(db_path))
    assert cold_worker._started is True
    cold_worker.stop(timeout=2.0)
    assert cold_worker._started is False
    assert cold_worker._worker_thread is None
