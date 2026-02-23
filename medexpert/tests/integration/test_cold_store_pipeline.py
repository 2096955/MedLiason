"""Integration tests for the cold store pipeline.

End-to-end: flush mock session → verify cold store → seed new session →
verify seed payload. Tests learning accumulation and backward compatibility.
"""

import json
from unittest.mock import MagicMock

import pytest

from lifesci_tools import cold_store, cold_worker, strategy_seeder
from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend


@pytest.fixture(autouse=True)
def _clear_ddl_cache():
    cold_store.clear_init_cache()
    yield
    cold_store.clear_init_cache()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "pipeline-test.db")


@pytest.fixture
def tool(db_path):
    t = MemoryPlaneTool(tool_config={"cold_db_path": db_path})
    t._cold_db_path = db_path
    MemoryPlaneTool._backend = DictBackend()
    return t


@pytest.fixture
def ctx():
    c = MagicMock()
    c.session = MagicMock()
    c.session.id = "pipeline-sess-001"
    return c


def _make_session_payload(session_id, query="metformin side effects"):
    return {
        "session_id": session_id,
        "query_domain": "drugs",
        "query_text": query,
        "coverage_pct": 0.85,
        "verification_verdict": "PASS",
        "verification_score": 0.9,
        "revision_triggered": False,
        "specialists_used": ["DrugSpecialist", "LiteratureSpecialist"],
        "agent_coverage": {"DrugSpecialist": 0.5, "LiteratureSpecialist": 0.35},
        "sources": [
            {
                "source_name": "pubmed",
                "evidence_count": 5,
                "avg_grade_score": 3.0,
                "citations_valid": 4,
                "citations_invalid": 1,
                "passed_verification": True,
            },
            {
                "source_name": "openfda",
                "evidence_count": 3,
                "avg_grade_score": 2.5,
                "citations_valid": 3,
                "citations_invalid": 0,
                "passed_verification": True,
            },
        ],
    }


# ── E2E: flush → cold store → seed ──────────────────────────


def test_flush_and_verify_cold_store(db_path):
    """Persist a session via cold_worker._persist_one and verify all tables."""
    payload = _make_session_payload("e2e-sess-1")
    payload["cold_db_path"] = db_path

    cold_worker._persist_one(db_path, payload)

    conn = cold_store.get_connection(db_path)

    # Session outcomes
    row = conn.execute(
        "SELECT * FROM session_outcomes WHERE session_id = 'e2e-sess-1'"
    ).fetchone()
    assert row is not None
    assert row["verification_verdict"] == "PASS"
    assert row["coverage_pct"] == 0.85

    # Source reliability
    sources = conn.execute(
        "SELECT * FROM source_reliability WHERE session_id = 'e2e-sess-1'"
    ).fetchall()
    assert len(sources) == 2

    # Routing patterns
    routes = conn.execute(
        "SELECT * FROM routing_patterns WHERE session_id = 'e2e-sess-1'"
    ).fetchall()
    assert len(routes) == 2

    # Query patterns
    qp = conn.execute("SELECT * FROM query_patterns").fetchone()
    assert qp is not None
    assert qp["frequency"] == 1

    # Agent co-activation (1 pair: Drug + Literature)
    coact = conn.execute("SELECT * FROM agent_coactivation").fetchall()
    assert len(coact) == 1

    conn.close()


def test_flush_then_seed_roundtrip(db_path):
    """Flush multiple sessions, refresh strategies, then seed a new session."""
    # Flush 5 sessions
    for i in range(5):
        payload = _make_session_payload(f"roundtrip-{i}")
        payload["cold_db_path"] = db_path
        cold_worker._persist_one(db_path, payload)

    # Refresh strategies
    conn = cold_store.get_connection(db_path)
    rows_written = cold_store.refresh_learned_strategies(conn)
    conn.close()
    assert rows_written >= 1

    # Seed a new session
    seed = strategy_seeder.build_seed_payload(
        "What are the side effects of metformin?", db_path
    )
    assert seed["seeded"] is True
    assert seed["routing_hints"] is not None
    assert seed["source_rankings"] is not None
    assert seed["agent_pairs"] is not None


def test_learning_accumulation_across_sessions(db_path):
    """Strategies improve confidence as more sessions accumulate."""
    confidences = []
    for i in range(15):
        payload = _make_session_payload(f"accum-{i}")
        payload["cold_db_path"] = db_path
        cold_worker._persist_one(db_path, payload)

        if i >= 4 and (i + 1) % 5 == 0:
            conn = cold_store.get_connection(db_path)
            cold_store.refresh_learned_strategies(conn)
            strategy = cold_store.get_routing_strategy(conn, "drugs")
            if strategy:
                confidences.append(strategy["confidence"])
            conn.close()

    # Confidence should increase (or at least not decrease) with more data
    assert len(confidences) >= 2
    assert confidences[-1] >= confidences[0]


# ── E2E via memory_plane tool with auto-collection ───────────


async def test_memory_plane_auto_collect_and_seed(tool, ctx, db_path):
    """Test auto-collection: populate hot store, flush, verify cold, seed."""
    import lifesci_tools.cold_worker as cw
    cw.reset_for_testing()

    # Simulate the orchestrator storing signals during Steps 1-10
    await tool._run_async_impl(
        {"operation": "store", "key": "verification_verdict", "value": "PASS", "namespace": "verification"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "verification_score", "value": "0.88", "namespace": "verification"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "coverage_pct", "value": "0.85"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "specialists_used", "value": '["DrugSpecialist","LiteratureSpecialist"]'}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "query_domain", "value": "drugs"}, ctx
    )
    # Store some evidence
    for i in range(3):
        await tool._run_async_impl(
            {"operation": "store", "key": f"ev_{i}", "value": json.dumps({"data": f"item-{i}"}), "namespace": "evidence"}, ctx
        )

    # flush_cold with just query — auto-collection does the rest
    result = await tool._run_async_impl(
        {"operation": "flush_cold", "query": "metformin side effects"}, ctx
    )
    assert result["success"] is True
    assert "verification_verdict" in result["auto_collected_keys"]
    assert "coverage_pct" in result["auto_collected_keys"]
    assert "evidence_count" in result["auto_collected_keys"]

    # The worker enqueued the item — persist directly for test reliability
    item = cw._queue.get_nowait()
    assert item["verification_verdict"] == "PASS"
    assert item["coverage_pct"] == 0.85
    assert item["evidence_count"] == 3
    assert item["specialists_used"] == ["DrugSpecialist", "LiteratureSpecialist"]
    assert item["query_domain"] == "drugs"

    # Now persist 5 sessions directly and seed
    for i in range(5):
        cold_worker._persist_one(db_path, _make_session_payload(f"auto-{i}"))

    conn = cold_store.get_connection(db_path)
    cold_store.refresh_learned_strategies(conn)
    conn.close()

    # Seed session via tool
    result = await tool._run_async_impl(
        {"operation": "seed_session", "query": "metformin side effects"}, ctx
    )
    assert result["success"] is True
    assert result["seeded"] is True

    # Verify seed data in learning namespace
    result = await tool._run_async_impl(
        {"operation": "retrieve", "key": "seed", "namespace": "learning"}, ctx
    )
    assert result["found"] is True


# ── Backward compatibility ───────────────────────────────────


async def test_backward_compat_no_cold_db_path(ctx):
    """Existing operations work fine when cold_db_path is not configured."""
    t = MemoryPlaneTool()
    MemoryPlaneTool._backend = DictBackend()

    # Standard store/retrieve still works
    result = await t._run_async_impl(
        {"operation": "store", "key": "drug", "value": "metformin"}, ctx
    )
    assert result["success"] is True

    result = await t._run_async_impl(
        {"operation": "retrieve", "key": "drug"}, ctx
    )
    assert result["success"] is True
    assert result["value"] == "metformin"

    # Cold operations fail gracefully
    result = await t._run_async_impl(
        {"operation": "flush_cold"}, ctx
    )
    assert result["success"] is False
    assert "not configured" in result["error"]

    result = await t._run_async_impl(
        {"operation": "seed_session", "query": "test"}, ctx
    )
    assert result["success"] is False
    assert "not configured" in result["error"]


async def test_existing_operations_unchanged_with_cold_config(tool, ctx):
    """Standard operations still work identically when cold_db_path IS configured."""
    # store
    result = await tool._run_async_impl(
        {"operation": "store", "key": "k1", "value": "v1"}, ctx
    )
    assert result["success"] is True

    # retrieve
    result = await tool._run_async_impl(
        {"operation": "retrieve", "key": "k1"}, ctx
    )
    assert result["success"] is True
    assert result["value"] == "v1"

    # list_keys
    result = await tool._run_async_impl(
        {"operation": "list_keys"}, ctx
    )
    assert result["success"] is True
    assert "k1" in result["keys"]

    # append
    result = await tool._run_async_impl(
        {"operation": "append", "key": "list", "value": '"item1"'}, ctx
    )
    assert result["success"] is True
    assert result["length"] == 1

    # clear_session
    result = await tool._run_async_impl(
        {"operation": "clear_session"}, ctx
    )
    assert result["success"] is True
