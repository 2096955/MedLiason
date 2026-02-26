"""Tests for cold_store — SQLite schema, CRUD, aggregation, normalisation."""

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


# ── schema creation ──────────────────────────────────────────


def test_schema_creates_all_tables(db):
    cursor = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = sorted(
        row["name"] for row in cursor if row["name"] != "sqlite_sequence"
    )
    expected = sorted(
        [
            "session_outcomes",
            "source_reliability",
            "routing_patterns",
            "query_patterns",
            "agent_coactivation",
            "learned_strategies",
            "prompt_versions",
            "prompt_scores",
            "prompt_promotions",
            "user_feedback",
        ]
    )
    assert tables == expected


def test_schema_idempotent(tmp_path):
    db_path = str(tmp_path / "idempotent.db")
    conn1 = cold_store.get_connection(db_path)
    conn1.close()
    conn2 = cold_store.get_connection(db_path)
    cursor = conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row["name"] for row in cursor if row["name"] != "sqlite_sequence"]
    assert len(tables) == 10
    conn2.close()


def test_parent_directory_created(tmp_path):
    db_path = str(tmp_path / "nested" / "dir" / "cold.db")
    conn = cold_store.get_connection(db_path)
    conn.close()
    assert (tmp_path / "nested" / "dir" / "cold.db").exists()


# ── session_outcomes CRUD ────────────────────────────────────


def test_write_session_outcome(db):
    cold_store.write_session_outcome(
        db,
        session_id="s1",
        query_domain="drugs",
        query_text="metformin side effects",
        coverage_pct=0.85,
        verification_verdict="PASS",
        verification_score=0.92,
        revision_triggered=False,
        specialists_used=["DrugSpecialist", "LiteratureSpecialist"],
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM session_outcomes WHERE session_id = 's1'"
    ).fetchone()
    assert row["query_domain"] == "drugs"
    assert row["coverage_pct"] == 0.85
    assert row["verification_verdict"] == "PASS"
    assert json.loads(row["specialists_used_json"]) == [
        "DrugSpecialist",
        "LiteratureSpecialist",
    ]


def test_write_session_outcome_replace(db):
    cold_store.write_session_outcome(db, session_id="s1", query_domain="drugs")
    cold_store.write_session_outcome(
        db, session_id="s1", query_domain="literature", coverage_pct=0.5
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM session_outcomes WHERE session_id = 's1'"
    ).fetchone()
    assert row["query_domain"] == "literature"
    assert row["coverage_pct"] == 0.5


def test_write_session_outcome_defaults(db):
    cold_store.write_session_outcome(db, session_id="s2", query_domain="general")
    db.commit()
    row = db.execute(
        "SELECT * FROM session_outcomes WHERE session_id = 's2'"
    ).fetchone()
    assert row["coverage_pct"] == 0.0
    assert row["verification_verdict"] == "UNKNOWN"
    assert row["revision_triggered"] == 0
    assert json.loads(row["specialists_used_json"]) == []


# ── source_reliability CRUD ──────────────────────────────────


def test_write_source_reliability(db):
    cold_store.write_session_outcome(db, session_id="s1", query_domain="drugs")
    cold_store.write_source_reliability(
        db,
        session_id="s1",
        source_name="pubmed",
        evidence_count=5,
        avg_grade_score=3.2,
        citations_valid=4,
        citations_invalid=1,
        passed_verification=True,
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM source_reliability WHERE session_id = 's1'"
    ).fetchone()
    assert row["source_name"] == "pubmed"
    assert row["evidence_count"] == 5
    assert row["passed_verification"] == 1


def test_multiple_sources_per_session(db):
    cold_store.write_session_outcome(db, session_id="s1", query_domain="drugs")
    cold_store.write_source_reliability(db, session_id="s1", source_name="pubmed")
    cold_store.write_source_reliability(db, session_id="s1", source_name="openfda")
    db.commit()
    rows = db.execute(
        "SELECT * FROM source_reliability WHERE session_id = 's1'"
    ).fetchall()
    assert len(rows) == 2


# ── routing_patterns CRUD ────────────────────────────────────


def test_write_routing_pattern(db):
    cold_store.write_session_outcome(db, session_id="s1", query_domain="drugs")
    cold_store.write_routing_pattern(
        db,
        session_id="s1",
        query_domain="drugs",
        agent_name="DrugSpecialist",
        coverage_contribution=0.6,
        session_verification_score=0.9,
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM routing_patterns WHERE session_id = 's1'"
    ).fetchone()
    assert row["agent_name"] == "DrugSpecialist"
    assert row["coverage_contribution"] == 0.6


# ── query_patterns upsert ────────────────────────────────────


def test_upsert_query_pattern_insert(db):
    cold_store.upsert_query_pattern(
        db,
        normalized_template="effects metformin side",
        domain="drugs",
        coverage_pct=0.8,
        verification_score=0.9,
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM query_patterns WHERE normalized_template = 'effects metformin side'"
    ).fetchone()
    assert row["frequency"] == 1
    assert row["domain"] == "drugs"
    assert row["avg_coverage_pct"] == 0.8


def test_upsert_query_pattern_increments_frequency(db):
    cold_store.upsert_query_pattern(
        db, normalized_template="diabetes treatment", coverage_pct=0.7, verification_score=0.8
    )
    cold_store.upsert_query_pattern(
        db, normalized_template="diabetes treatment", coverage_pct=0.9, verification_score=0.95
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM query_patterns WHERE normalized_template = 'diabetes treatment'"
    ).fetchone()
    assert row["frequency"] == 2
    # Running average: (0.7 * 1 + 0.9) / 2 = 0.8
    assert abs(row["avg_coverage_pct"] - 0.8) < 0.01


# ── agent_coactivation upsert ───────────────────────────────


def test_upsert_agent_coactivation(db):
    cold_store.upsert_agent_coactivation(
        db,
        agent_a="DrugSpecialist",
        agent_b="LiteratureSpecialist",
        passed=True,
        combined_coverage=0.85,
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM agent_coactivation WHERE agent_a = 'DrugSpecialist'"
    ).fetchone()
    assert row["co_used_count"] == 1
    assert row["co_passed_count"] == 1


def test_upsert_agent_coactivation_sorts_names(db):
    cold_store.upsert_agent_coactivation(
        db, agent_a="Z_Agent", agent_b="A_Agent", passed=False, combined_coverage=0.5
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM agent_coactivation"
    ).fetchone()
    assert row["agent_a"] == "A_Agent"
    assert row["agent_b"] == "Z_Agent"


def test_upsert_agent_coactivation_increments(db):
    cold_store.upsert_agent_coactivation(
        db, agent_a="A", agent_b="B", passed=True, combined_coverage=0.8
    )
    cold_store.upsert_agent_coactivation(
        db, agent_a="A", agent_b="B", passed=False, combined_coverage=0.6
    )
    db.commit()
    row = db.execute("SELECT * FROM agent_coactivation").fetchone()
    assert row["co_used_count"] == 2
    assert row["co_passed_count"] == 1


# ── refresh_learned_strategies ───────────────────────────────


def _populate_sessions(db, n=5):
    """Insert n sessions with routing and source data."""
    for i in range(n):
        sid = f"sess-{i}"
        cold_store.write_session_outcome(
            db,
            session_id=sid,
            query_domain="drugs",
            query_text=f"question {i}",
            coverage_pct=0.7 + i * 0.05,
            verification_verdict="PASS" if i % 2 == 0 else "MINOR_ISSUES",
            verification_score=0.6 + i * 0.05,
            specialists_used=["DrugSpecialist", "LiteratureSpecialist"],
        )
        cold_store.write_source_reliability(
            db,
            session_id=sid,
            source_name="pubmed",
            evidence_count=3 + i,
            avg_grade_score=2.5 + i * 0.1,
            citations_valid=3,
            citations_invalid=0,
            passed_verification=True,
        )
        cold_store.write_routing_pattern(
            db,
            session_id=sid,
            query_domain="drugs",
            agent_name="DrugSpecialist",
            coverage_contribution=0.5,
            session_verification_score=0.6 + i * 0.05,
        )
        cold_store.write_routing_pattern(
            db,
            session_id=sid,
            query_domain="drugs",
            agent_name="LiteratureSpecialist",
            coverage_contribution=0.3,
            session_verification_score=0.6 + i * 0.05,
        )
    db.commit()


def test_refresh_learned_strategies_routing(db):
    _populate_sessions(db, 5)
    rows_written = cold_store.refresh_learned_strategies(db)
    assert rows_written >= 1
    strategy = cold_store.get_routing_strategy(db, "drugs")
    assert strategy is not None
    assert len(strategy["agents"]) == 2


def test_refresh_learned_strategies_source_ranking(db):
    _populate_sessions(db, 5)
    cold_store.refresh_learned_strategies(db)
    rankings = cold_store.get_source_rankings(db)
    assert rankings is not None
    assert len(rankings["sources"]) >= 1
    assert rankings["sources"][0]["source"] == "pubmed"


def test_refresh_learned_strategies_empty_db(db):
    rows_written = cold_store.refresh_learned_strategies(db)
    assert rows_written == 0


def test_refresh_learned_strategies_insufficient_samples(db):
    """Strategies require >= 2 samples per group."""
    cold_store.write_session_outcome(
        db, session_id="s-only", query_domain="drugs"
    )
    cold_store.write_routing_pattern(
        db, session_id="s-only", query_domain="drugs", agent_name="DrugSpecialist"
    )
    db.commit()
    rows_written = cold_store.refresh_learned_strategies(db)
    assert rows_written == 0


# ── read helpers ─────────────────────────────────────────────


def test_get_routing_strategy_missing(db):
    assert cold_store.get_routing_strategy(db, "nonexistent") is None


def test_get_source_rankings_missing(db):
    assert cold_store.get_source_rankings(db) is None


def test_get_query_intelligence(db):
    cold_store.upsert_query_pattern(
        db,
        normalized_template="diabetes treatment",
        domain="drugs",
        coverage_pct=0.85,
        verification_score=0.9,
    )
    db.commit()
    intel = cold_store.get_query_intelligence(db, "diabetes treatment")
    assert intel is not None
    assert intel["frequency"] == 1
    assert intel["domain"] == "drugs"


def test_get_query_intelligence_missing(db):
    assert cold_store.get_query_intelligence(db, "nonexistent") is None


def test_get_best_agent_pairs(db):
    _populate_sessions(db, 5)
    # Need co-activation data — populate_sessions adds routing but not coactivation
    for i in range(3):
        cold_store.upsert_agent_coactivation(
            db, agent_a="DrugSpecialist", agent_b="LiteratureSpecialist",
            passed=True, combined_coverage=0.7 + i * 0.05,
        )
    db.commit()
    cold_store.refresh_learned_strategies(db)
    pairs = cold_store.get_best_agent_pairs(db)
    assert pairs is not None
    assert len(pairs["pairs"]) >= 1


def test_get_best_agent_pairs_missing(db):
    assert cold_store.get_best_agent_pairs(db) is None


def test_get_session_count(db):
    assert cold_store.get_session_count(db) == 0
    cold_store.write_session_outcome(db, session_id="s1", query_domain="drugs")
    cold_store.write_session_outcome(db, session_id="s2", query_domain="literature")
    db.commit()
    assert cold_store.get_session_count(db) == 2


# ── normalize_query ──────────────────────────────────────────


def test_normalize_query_basic():
    result = cold_store.normalize_query("What are the side effects of Metformin?")
    # Should be lowercased, no punctuation, stop words removed, sorted
    assert "metformin" in result
    assert "effects" in result
    assert "side" in result
    assert "what" not in result  # stop word
    assert "are" not in result  # stop word
    assert "the" not in result  # stop word


def test_normalize_query_sorted():
    result = cold_store.normalize_query("Zebra Apple Mango")
    assert result == "apple mango zebra"


def test_normalize_query_deduplicates():
    result = cold_store.normalize_query("drug drug drug effects")
    assert result == "drug effects"


def test_normalize_query_empty():
    assert cold_store.normalize_query("") == ""
    assert cold_store.normalize_query("the a an") == ""


def test_normalize_query_punctuation():
    result = cold_store.normalize_query("What's the drug-drug interaction?")
    assert "interaction" in result
    assert "?" not in result
    assert "'" not in result


# ── cascade delete ───────────────────────────────────────────


def test_cascade_delete_removes_child_rows(db):
    """INSERT OR REPLACE on session_outcomes cascades to child tables."""
    cold_store.write_session_outcome(
        db, session_id="cascade-1", query_domain="drugs",
        specialists_used=["DrugSpecialist"],
    )
    cold_store.write_source_reliability(
        db, session_id="cascade-1", source_name="pubmed", evidence_count=5,
    )
    cold_store.write_routing_pattern(
        db, session_id="cascade-1", query_domain="drugs",
        agent_name="DrugSpecialist",
    )
    db.commit()

    # Verify children exist
    assert len(db.execute("SELECT * FROM source_reliability WHERE session_id='cascade-1'").fetchall()) == 1
    assert len(db.execute("SELECT * FROM routing_patterns WHERE session_id='cascade-1'").fetchall()) == 1

    # Replace the session → CASCADE should delete children
    cold_store.write_session_outcome(
        db, session_id="cascade-1", query_domain="literature",
    )
    db.commit()

    # Children should be gone
    assert len(db.execute("SELECT * FROM source_reliability WHERE session_id='cascade-1'").fetchall()) == 0
    assert len(db.execute("SELECT * FROM routing_patterns WHERE session_id='cascade-1'").fetchall()) == 0


# ── DDL caching ──────────────────────────────────────────────


def test_ddl_cache_skips_second_init(tmp_path):
    """Second get_connection for same path skips executescript."""
    db_path = str(tmp_path / "cache-test.db")
    conn1 = cold_store.get_connection(db_path)
    conn1.close()

    # Second call should not re-run DDL (we can't directly observe this,
    # but we verify it doesn't error and the tables still exist)
    conn2 = cold_store.get_connection(db_path)
    tables = [
        r["name"] for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        if r["name"] != "sqlite_sequence"
    ]
    assert len(tables) == 10
    conn2.close()


def test_clear_init_cache():
    """Public clear_init_cache resets the cache."""
    cold_store._initialised_paths.add("/fake/path")
    cold_store.clear_init_cache()
    assert len(cold_store._initialised_paths) == 0


# ── get_strategy_by_type ─────────────────────────────────────


def test_get_strategy_by_type(db):
    _populate_sessions(db, 5)
    cold_store.refresh_learned_strategies(db)
    result = cold_store.get_strategy_by_type(db, "routing", "drugs")
    assert result is not None
    assert result["strategy_type"] == "routing"
    assert result["domain_or_key"] == "drugs"
    assert isinstance(result["strategy"], list)


def test_get_strategy_by_type_missing(db):
    assert cold_store.get_strategy_by_type(db, "routing", "nonexistent") is None


# ── fuzzy query matching (Jaccard) ────────────────────────────


def test_jaccard_similarity():
    assert cold_store._jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)
    assert cold_store._jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0
    assert cold_store._jaccard_similarity({"a"}, {"b"}) == 0.0
    assert cold_store._jaccard_similarity(set(), {"a"}) == 0.0
    assert cold_store._jaccard_similarity(set(), set()) == 0.0


def test_get_query_intelligence_exact_match_returns_similarity_one(db):
    cold_store.upsert_query_pattern(
        db,
        normalized_template="effects metformin side",
        domain="drugs",
        coverage_pct=0.8,
        verification_score=0.9,
    )
    db.commit()
    intel = cold_store.get_query_intelligence(db, "effects metformin side")
    assert intel is not None
    assert intel["similarity"] == 1.0
    assert intel["template"] == "effects metformin side"


def test_get_query_intelligence_fuzzy_match(db):
    cold_store.upsert_query_pattern(
        db,
        normalized_template="effects metformin side",
        domain="drugs",
        coverage_pct=0.8,
        verification_score=0.9,
    )
    db.commit()
    # "adverse effects metformin" has 2 tokens overlapping with 3-token template
    # Jaccard = 2 / 4 = 0.5 — right at threshold
    intel = cold_store.get_query_intelligence(db, "adverse effects metformin")
    assert intel is not None
    assert intel["similarity"] >= 0.5
    assert intel["template"] == "effects metformin side"


def test_get_query_intelligence_below_threshold(db):
    cold_store.upsert_query_pattern(
        db,
        normalized_template="diabetes treatment insulin",
        domain="drugs",
        coverage_pct=0.7,
        verification_score=0.8,
    )
    db.commit()
    # Zero token overlap
    intel = cold_store.get_query_intelligence(db, "cardiac stent placement")
    assert intel is None


def test_get_query_intelligence_best_of_multiple(db):
    cold_store.upsert_query_pattern(
        db,
        normalized_template="effects metformin side",
        domain="drugs",
        coverage_pct=0.8,
        verification_score=0.9,
    )
    cold_store.upsert_query_pattern(
        db,
        normalized_template="metformin dosage diabetes",
        domain="drugs",
        coverage_pct=0.6,
        verification_score=0.7,
    )
    db.commit()
    # "metformin side" overlaps with first: 2/3=0.67, with second: 1/4=0.25
    intel = cold_store.get_query_intelligence(db, "metformin side")
    assert intel is not None
    assert intel["template"] == "effects metformin side"


def test_get_query_intelligence_custom_threshold(db):
    cold_store.upsert_query_pattern(
        db,
        normalized_template="abc def ghi",
        domain="drugs",
        coverage_pct=0.8,
        verification_score=0.9,
    )
    db.commit()
    # "abc xyz" has Jaccard = 1/4 = 0.25
    assert cold_store.get_query_intelligence(db, "abc xyz") is None
    intel = cold_store.get_query_intelligence(db, "abc xyz", similarity_threshold=0.2)
    assert intel is not None


# ── pruning ──────────────────────────────────────────────────


def test_prune_age_based(db):
    """Sessions older than max_age_days are deleted."""
    db.execute(
        "INSERT INTO session_outcomes (session_id, query_domain, created_at) "
        "VALUES ('old-1', 'drugs', datetime('now', '-100 days'))"
    )
    cold_store.write_session_outcome(db, session_id="new-1", query_domain="drugs")
    db.commit()
    assert cold_store.get_session_count(db) == 2

    result = cold_store.prune_cold_store(db, max_age_days=90)
    assert result["age_based"] == 1
    assert cold_store.get_session_count(db) == 1


def test_prune_count_based(db):
    """Excess sessions beyond max_sessions are pruned (oldest first)."""
    for i in range(15):
        cold_store.write_session_outcome(
            db, session_id=f"cnt-{i:03d}", query_domain="drugs"
        )
    db.commit()
    assert cold_store.get_session_count(db) == 15

    result = cold_store.prune_cold_store(db, max_sessions=10, max_age_days=9999)
    assert result["count_based"] == 5
    assert cold_store.get_session_count(db) == 10


def test_prune_cascades_to_children(db):
    """Pruning sessions cascades to source_reliability and routing_patterns."""
    db.execute(
        "INSERT INTO session_outcomes (session_id, query_domain, created_at) "
        "VALUES ('old-cascade', 'drugs', datetime('now', '-200 days'))"
    )
    cold_store.write_source_reliability(
        db, session_id="old-cascade", source_name="pubmed"
    )
    cold_store.write_routing_pattern(
        db, session_id="old-cascade", query_domain="drugs",
        agent_name="DrugSpecialist",
    )
    db.commit()

    cold_store.prune_cold_store(db, max_age_days=90)
    assert len(db.execute(
        "SELECT * FROM source_reliability WHERE session_id='old-cascade'"
    ).fetchall()) == 0
    assert len(db.execute(
        "SELECT * FROM routing_patterns WHERE session_id='old-cascade'"
    ).fetchall()) == 0


def test_prune_vacuum_threshold(db):
    """VACUUM runs only when > 100 rows are deleted."""
    for i in range(150):
        db.execute(
            "INSERT INTO session_outcomes (session_id, query_domain, created_at) "
            f"VALUES ('vac-{i:03d}', 'drugs', datetime('now', '-200 days'))"
        )
    db.commit()

    result = cold_store.prune_cold_store(db, max_age_days=90)
    assert result["vacuumed"] is True


def test_prune_no_op_when_within_limits(db):
    """Pruning with nothing to prune returns zeroes."""
    cold_store.write_session_outcome(db, session_id="ok-1", query_domain="drugs")
    db.commit()
    result = cold_store.prune_cold_store(db, max_age_days=90, max_sessions=10000)
    assert result["age_based"] == 0
    assert result["count_based"] == 0
    assert result["vacuumed"] is False


def test_prune_cleans_old_query_patterns(db):
    """Old query_patterns are pruned by age."""
    db.execute(
        "INSERT INTO query_patterns (normalized_template, domain, updated_at) "
        "VALUES ('old pattern', 'drugs', datetime('now', '-100 days'))"
    )
    cold_store.upsert_query_pattern(
        db, normalized_template="new pattern", domain="drugs"
    )
    db.commit()

    cold_store.prune_cold_store(db, max_age_days=90)
    rows = db.execute("SELECT * FROM query_patterns").fetchall()
    assert len(rows) == 1
    assert rows[0]["normalized_template"] == "new pattern"
