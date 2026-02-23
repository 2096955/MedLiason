"""Tests for strategy_seeder — cold→hot seed payload construction."""

import pytest

from lifesci_tools import cold_store, strategy_seeder


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "seed-test.db")


def _populate_cold_store(db_path, n_sessions=5):
    """Populate cold store with enough data to trigger strategy generation."""
    conn = cold_store.get_connection(db_path)
    for i in range(n_sessions):
        sid = f"seed-sess-{i}"
        cold_store.write_session_outcome(
            conn,
            session_id=sid,
            query_domain="drugs",
            query_text="metformin side effects",
            coverage_pct=0.7 + i * 0.05,
            verification_verdict="PASS",
            verification_score=0.8 + i * 0.02,
            specialists_used=["DrugSpecialist", "LiteratureSpecialist"],
        )
        cold_store.write_routing_pattern(
            conn,
            session_id=sid,
            query_domain="drugs",
            agent_name="DrugSpecialist",
            coverage_contribution=0.5,
            session_verification_score=0.8 + i * 0.02,
        )
        cold_store.write_routing_pattern(
            conn,
            session_id=sid,
            query_domain="drugs",
            agent_name="LiteratureSpecialist",
            coverage_contribution=0.3,
            session_verification_score=0.8 + i * 0.02,
        )
        cold_store.write_source_reliability(
            conn,
            session_id=sid,
            source_name="pubmed",
            evidence_count=5,
            avg_grade_score=3.0,
            citations_valid=4,
            citations_invalid=0,
            passed_verification=True,
        )
        cold_store.upsert_query_pattern(
            conn,
            normalized_template="effects metformin side",
            domain="drugs",
            coverage_pct=0.7 + i * 0.05,
            verification_score=0.8 + i * 0.02,
        )
        cold_store.upsert_agent_coactivation(
            conn,
            agent_a="DrugSpecialist",
            agent_b="LiteratureSpecialist",
            passed=True,
            combined_coverage=0.8,
        )
    conn.commit()
    cold_store.refresh_learned_strategies(conn)
    conn.close()


# ── seed with data ───────────────────────────────────────────


def test_build_seed_payload_with_data(db_path):
    _populate_cold_store(db_path)
    payload = strategy_seeder.build_seed_payload(
        "What are the side effects of metformin?", db_path
    )
    assert payload["seeded"] is True
    assert payload["routing_hints"] is not None
    assert payload["source_rankings"] is not None
    assert payload["query_intelligence"] is not None
    assert payload["agent_pairs"] is not None


def test_seed_routing_hints_structure(db_path):
    _populate_cold_store(db_path)
    payload = strategy_seeder.build_seed_payload(
        "metformin side effects", db_path
    )
    hints = payload["routing_hints"]
    assert "domain" in hints
    assert "agents" in hints
    assert len(hints["agents"]) > 0
    assert "agent" in hints["agents"][0]


def test_seed_source_rankings_structure(db_path):
    _populate_cold_store(db_path)
    payload = strategy_seeder.build_seed_payload(
        "metformin side effects", db_path
    )
    rankings = payload["source_rankings"]
    assert "sources" in rankings
    assert len(rankings["sources"]) > 0
    assert rankings["sources"][0]["source"] == "pubmed"


# ── empty cold store ─────────────────────────────────────────


def test_build_seed_payload_empty_db(db_path):
    # Create empty DB
    conn = cold_store.get_connection(db_path)
    conn.close()

    payload = strategy_seeder.build_seed_payload("any question", db_path)
    assert payload["seeded"] is False


def test_build_seed_payload_unknown_query(db_path):
    _populate_cold_store(db_path)
    payload = strategy_seeder.build_seed_payload(
        "completely unrelated question about astronomy", db_path
    )
    # No query intelligence match, but source rankings and agent pairs exist
    assert payload["query_intelligence"] is None


# ── no db_path ───────────────────────────────────────────────


def test_build_seed_payload_no_db_path():
    payload = strategy_seeder.build_seed_payload("question", "")
    assert payload["seeded"] is False
    assert "not configured" in payload["reason"]


def test_build_seed_payload_none_db_path():
    payload = strategy_seeder.build_seed_payload("question", None)
    assert payload["seeded"] is False


# ── exception handling ───────────────────────────────────────


def test_build_seed_payload_invalid_path():
    payload = strategy_seeder.build_seed_payload(
        "question", "/nonexistent/impossible/path/cold.db"
    )
    # Should handle gracefully — either create it or report unavailable
    # Since get_connection creates directories, this may succeed on some systems
    assert "seeded" in payload


def test_build_seed_payload_empty_query(db_path):
    _populate_cold_store(db_path)
    payload = strategy_seeder.build_seed_payload("", db_path)
    # Empty query → no query intelligence, but other strategies may exist
    assert payload["query_intelligence"] is None
