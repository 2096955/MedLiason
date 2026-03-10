"""Tests for the MemoryPlaneTool — session-scoped KV store."""

import json
from unittest.mock import MagicMock, AsyncMock

import pytest

from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend


@pytest.fixture
def tool():
    t = MemoryPlaneTool()
    MemoryPlaneTool._backend = DictBackend()
    return t


@pytest.fixture
def ctx():
    c = MagicMock()
    c.session = MagicMock()
    c.session.id = "sess-001"
    return c


@pytest.fixture
def ctx2():
    c = MagicMock()
    c.session = MagicMock()
    c.session.id = "sess-002"
    return c


# ── store / retrieve ──────────────────────────────────────────


async def test_store_and_retrieve(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "store", "key": "drug", "value": "metformin"}, ctx
    )
    assert result["success"] is True

    result = await tool._run_async_impl(
        {"operation": "retrieve", "key": "drug"}, ctx
    )
    assert result["success"] is True
    assert result["found"] is True
    assert result["value"] == "metformin"


async def test_retrieve_missing_key(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "retrieve", "key": "nonexistent"}, ctx
    )
    assert result["success"] is True
    assert result["found"] is False
    assert result["value"] is None


async def test_store_requires_key(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "store", "value": "test"}, ctx
    )
    assert result["success"] is False
    assert "Key is required" in result["error"]


async def test_retrieve_requires_key(tool, ctx):
    result = await tool._run_async_impl({"operation": "retrieve"}, ctx)
    assert result["success"] is False


async def test_store_json_value(tool, ctx):
    evidence = {"pmid": "123", "title": "Test Study"}
    await tool._run_async_impl(
        {"operation": "store", "key": "ev1", "value": json.dumps(evidence)}, ctx
    )
    result = await tool._run_async_impl(
        {"operation": "retrieve", "key": "ev1"}, ctx
    )
    assert json.loads(result["value"]) == evidence


# ── session isolation ─────────────────────────────────────────


async def test_session_isolation(tool, ctx, ctx2):
    await tool._run_async_impl(
        {"operation": "store", "key": "data", "value": "session1"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "data", "value": "session2"}, ctx2
    )

    r1 = await tool._run_async_impl({"operation": "retrieve", "key": "data"}, ctx)
    r2 = await tool._run_async_impl({"operation": "retrieve", "key": "data"}, ctx2)
    assert r1["value"] == "session1"
    assert r2["value"] == "session2"


async def test_clear_session_does_not_affect_other_sessions(tool, ctx, ctx2):
    await tool._run_async_impl(
        {"operation": "store", "key": "k1", "value": "v1"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "k2", "value": "v2"}, ctx2
    )

    await tool._run_async_impl({"operation": "clear_session"}, ctx)

    r1 = await tool._run_async_impl({"operation": "retrieve", "key": "k1"}, ctx)
    r2 = await tool._run_async_impl({"operation": "retrieve", "key": "k2"}, ctx2)
    assert r1["found"] is False
    assert r2["found"] is True


# ── namespace separation ──────────────────────────────────────


async def test_namespace_separation(tool, ctx):
    await tool._run_async_impl(
        {"operation": "store", "key": "item", "value": "citation_val", "namespace": "citations"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "item", "value": "evidence_val", "namespace": "evidence"}, ctx
    )

    r1 = await tool._run_async_impl(
        {"operation": "retrieve", "key": "item", "namespace": "citations"}, ctx
    )
    r2 = await tool._run_async_impl(
        {"operation": "retrieve", "key": "item", "namespace": "evidence"}, ctx
    )
    assert r1["value"] == "citation_val"
    assert r2["value"] == "evidence_val"


async def test_invalid_namespace(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "store", "key": "k", "value": "v", "namespace": "invalid_ns"}, ctx
    )
    assert result["success"] is False
    assert "Invalid namespace" in result["error"]


async def test_default_namespace_is_intermediate(tool, ctx):
    await tool._run_async_impl(
        {"operation": "store", "key": "test", "value": "val"}, ctx
    )
    # list_keys in intermediate namespace
    result = await tool._run_async_impl(
        {"operation": "list_keys", "namespace": "intermediate"}, ctx
    )
    assert "test" in result["keys"]


# ── list_keys ─────────────────────────────────────────────────


async def test_list_keys(tool, ctx):
    await tool._run_async_impl(
        {"operation": "store", "key": "a", "value": "1", "namespace": "evidence"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "store", "key": "b", "value": "2", "namespace": "evidence"}, ctx
    )
    result = await tool._run_async_impl(
        {"operation": "list_keys", "namespace": "evidence"}, ctx
    )
    assert result["success"] is True
    assert sorted(result["keys"]) == ["a", "b"]


async def test_list_keys_empty(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "list_keys", "namespace": "citations"}, ctx
    )
    assert result["success"] is True
    assert result["keys"] == []


# ── append ────────────────────────────────────────────────────


async def test_append_creates_list(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "append", "key": "findings", "value": '"first"'}, ctx
    )
    assert result["success"] is True
    assert result["length"] == 1

    result = await tool._run_async_impl(
        {"operation": "append", "key": "findings", "value": '"second"'}, ctx
    )
    assert result["length"] == 2

    stored = await tool._run_async_impl(
        {"operation": "retrieve", "key": "findings"}, ctx
    )
    assert json.loads(stored["value"]) == ["first", "second"]


async def test_append_to_existing_json_list(tool, ctx):
    await tool._run_async_impl(
        {"operation": "store", "key": "list", "value": '["a","b"]'}, ctx
    )
    await tool._run_async_impl(
        {"operation": "append", "key": "list", "value": '"c"'}, ctx
    )
    stored = await tool._run_async_impl(
        {"operation": "retrieve", "key": "list"}, ctx
    )
    assert json.loads(stored["value"]) == ["a", "b", "c"]


async def test_append_to_non_list_wraps(tool, ctx):
    await tool._run_async_impl(
        {"operation": "store", "key": "scalar", "value": "hello"}, ctx
    )
    await tool._run_async_impl(
        {"operation": "append", "key": "scalar", "value": '"world"'}, ctx
    )
    stored = await tool._run_async_impl(
        {"operation": "retrieve", "key": "scalar"}, ctx
    )
    parsed = json.loads(stored["value"])
    assert len(parsed) == 2


async def test_append_requires_key(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "append", "value": "test"}, ctx
    )
    assert result["success"] is False


# ── clear_session ─────────────────────────────────────────────


async def test_clear_session(tool, ctx):
    for ns in ("citations", "evidence", "intermediate"):
        await tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v", "namespace": ns}, ctx
        )
    result = await tool._run_async_impl({"operation": "clear_session"}, ctx)
    assert result["success"] is True
    assert result["cleared"] >= 3


async def test_clear_empty_session(tool, ctx):
    result = await tool._run_async_impl({"operation": "clear_session"}, ctx)
    assert result["success"] is True
    assert result["cleared"] == 0


# ── error handling ────────────────────────────────────────────


async def test_unknown_operation(tool, ctx):
    result = await tool._run_async_impl({"operation": "delete"}, ctx)
    assert result["success"] is False
    assert "Unknown operation" in result["error"]


async def test_empty_operation(tool, ctx):
    result = await tool._run_async_impl({}, ctx)
    assert result["success"] is False


async def test_no_session_uses_default(tool):
    ctx_no_session = MagicMock()
    ctx_no_session.session = None
    result = await tool._run_async_impl(
        {"operation": "store", "key": "k", "value": "v"}, ctx_no_session
    )
    assert result["success"] is True


# ── DictBackend unit tests ────────────────────────────────────


async def test_dict_backend_exists():
    backend = DictBackend()
    await backend.set("key1", "val1")
    assert await backend.exists("key1") is True
    assert await backend.exists("key2") is False


async def test_dict_backend_delete():
    backend = DictBackend()
    await backend.set("key1", "val1")
    await backend.set("key2", "val2")
    await backend.delete("key1")
    assert await backend.get("key1") is None
    assert await backend.get("key2") == "val2"


async def test_dict_backend_keys_pattern():
    backend = DictBackend()
    await backend.set("medexpert:s1:evidence:a", "1")
    await backend.set("medexpert:s1:evidence:b", "2")
    await backend.set("medexpert:s1:citations:c", "3")
    keys = await backend.keys("medexpert:s1:evidence:*")
    assert sorted(keys) == ["medexpert:s1:evidence:a", "medexpert:s1:evidence:b"]


# ── read-only mode ───────────────────────────────────────────


@pytest.fixture
def ro_tool():
    """Memory plane tool configured with read_only=True."""
    t = MemoryPlaneTool(tool_config={"read_only": True})
    MemoryPlaneTool._backend = DictBackend()
    return t


async def test_read_only_blocks_store(ro_tool, ctx):
    result = await ro_tool._run_async_impl(
        {"operation": "store", "key": "k", "value": "v"}, ctx
    )
    assert result["success"] is False
    assert "read-only" in result["error"]


async def test_read_only_blocks_append(ro_tool, ctx):
    result = await ro_tool._run_async_impl(
        {"operation": "append", "key": "k", "value": '"v"'}, ctx
    )
    assert result["success"] is False
    assert "read-only" in result["error"]


async def test_read_only_blocks_clear_session(ro_tool, ctx):
    result = await ro_tool._run_async_impl(
        {"operation": "clear_session"}, ctx
    )
    assert result["success"] is False
    assert "read-only" in result["error"]


async def test_read_only_allows_retrieve(ro_tool, ctx):
    # Pre-populate via direct backend write
    key = ro_tool._make_key("sess-001", "evidence", "drug")
    await MemoryPlaneTool._backend.set(key, "metformin")
    result = await ro_tool._run_async_impl(
        {"operation": "retrieve", "key": "drug", "namespace": "evidence"}, ctx
    )
    assert result["success"] is True
    assert result["found"] is True
    assert result["value"] == "metformin"


async def test_read_only_allows_list_keys(ro_tool, ctx):
    key = ro_tool._make_key("sess-001", "citations", "pmid_123")
    await MemoryPlaneTool._backend.set(key, "data")
    result = await ro_tool._run_async_impl(
        {"operation": "list_keys", "namespace": "citations"}, ctx
    )
    assert result["success"] is True
    assert "pmid_123" in result["keys"]


async def test_read_only_description_differs(ro_tool, tool):
    assert "Read-only" in ro_tool.tool_description
    assert "Read-only" not in tool.tool_description


async def test_read_only_init_from_tool_config():
    t = MemoryPlaneTool(tool_config={"read_only": True})
    await t.init(MagicMock(), {"read_only": True})
    assert t._read_only is True


async def test_read_write_init_default():
    t = MemoryPlaneTool()
    await t.init(MagicMock(), {})
    assert t._read_only is False


# ── learning namespace ───────────────────────────────────────


async def test_learning_namespace_store_retrieve(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "store", "key": "hint", "value": "use DrugSpecialist", "namespace": "learning"}, ctx
    )
    assert result["success"] is True
    result = await tool._run_async_impl(
        {"operation": "retrieve", "key": "hint", "namespace": "learning"}, ctx
    )
    assert result["found"] is True
    assert result["value"] == "use DrugSpecialist"


# ── cold operations: flush_cold ──────────────────────────────


@pytest.fixture
def cold_tool(tmp_path):
    """Memory plane tool with cold_db_path configured."""
    db_path = str(tmp_path / "test-cold.db")
    t = MemoryPlaneTool(tool_config={"cold_db_path": db_path})
    t._cold_db_path = db_path
    MemoryPlaneTool._backend = DictBackend()
    return t


async def test_flush_cold_no_config(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "flush_cold"}, ctx
    )
    assert result["success"] is True
    assert result["flushed"] is False
    assert "not available" in result["reason"]


async def test_flush_cold_enqueues_with_auto_collection(cold_tool, ctx):
    import lifesci_tools.cold_worker as cw

    cw.reset_for_testing()

    # Pre-populate hot store with session signals
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "verification_verdict", "value": "PASS", "namespace": "verification"}, ctx
    )
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "verification_score", "value": "0.85", "namespace": "verification"}, ctx
    )
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "coverage_pct", "value": "0.9"}, ctx
    )
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "specialists_used", "value": '["DrugSpecialist"]'}, ctx
    )

    result = await cold_tool._run_async_impl(
        {"operation": "flush_cold", "query": "test question", "query_domain": "drugs"}, ctx
    )
    assert result["success"] is True
    assert result["enqueued"] is True
    assert "auto_collected_keys" in result
    assert "verification_verdict" in result["auto_collected_keys"]


async def test_flush_cold_auto_collects_evidence_count(cold_tool, ctx):
    """Auto-collection counts evidence namespace keys."""
    import lifesci_tools.cold_worker as cw

    cw.reset_for_testing()

    # Store 3 evidence items
    for i in range(3):
        await cold_tool._run_async_impl(
            {"operation": "store", "key": f"ev_{i}", "value": json.dumps({"data": i}), "namespace": "evidence"}, ctx
        )

    result = await cold_tool._run_async_impl(
        {"operation": "flush_cold", "query_domain": "drugs"}, ctx
    )
    assert result["success"] is True
    assert "evidence_count" in result["auto_collected_keys"]


async def test_flush_cold_explicit_overrides_auto(cold_tool, ctx):
    """Explicit values in the value param override auto-collected signals."""
    import lifesci_tools.cold_worker as cw

    cw.reset_for_testing()

    # Auto-collected: coverage_pct=0.5
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "coverage_pct", "value": "0.5"}, ctx
    )

    # Explicit override: coverage_pct=0.99
    result = await cold_tool._run_async_impl(
        {"operation": "flush_cold", "value": json.dumps({"coverage_pct": 0.99})}, ctx
    )
    assert result["success"] is True

    # Check the enqueued payload has the explicit value
    item = cw._queue.get_nowait()
    assert item["coverage_pct"] == 0.99


async def test_flush_cold_malformed_value_ignored(cold_tool, ctx):
    """Malformed JSON in value param doesn't crash; auto-collection still works."""
    import lifesci_tools.cold_worker as cw

    cw.reset_for_testing()

    result = await cold_tool._run_async_impl(
        {"operation": "flush_cold", "value": "not valid json!!"}, ctx
    )
    assert result["success"] is True
    assert result["enqueued"] is True


async def test_flush_cold_empty_value_ok(cold_tool, ctx):
    """Empty value param is fine — relies entirely on auto-collection."""
    import lifesci_tools.cold_worker as cw

    cw.reset_for_testing()

    result = await cold_tool._run_async_impl(
        {"operation": "flush_cold"}, ctx
    )
    assert result["success"] is True


async def test_flush_cold_collects_gvr_signals(cold_tool, ctx):
    """Auto-collection picks up STEP 10 GVR signals."""
    import lifesci_tools.cold_worker as cw

    cw.reset_for_testing()

    # Store STEP 10 signals
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "revision_triggered", "value": "true"}, ctx
    )
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "gvr_cycles", "value": "1"}, ctx
    )
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "re_verification_verdict", "value": "PASS", "namespace": "verification"}, ctx
    )
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "re_verification_score", "value": "0.75", "namespace": "verification"}, ctx
    )
    await cold_tool._run_async_impl(
        {"operation": "store", "key": "verification_verdict", "value": "CRITICAL_ISSUES", "namespace": "verification"}, ctx
    )

    result = await cold_tool._run_async_impl(
        {"operation": "flush_cold", "query_domain": "drugs"}, ctx
    )
    assert result["success"] is True
    keys = result["auto_collected_keys"]
    assert "revision_triggered" in keys
    assert "re_verification_verdict" in keys
    assert "verification_verdict" in keys

    item = cw._queue.get_nowait()
    assert item["revision_triggered"] is True
    assert item["gvr_cycles"] == 1
    assert item["re_verification_verdict"] == "PASS"
    assert item["re_verification_score"] == 0.75
    assert item["verification_verdict"] == "CRITICAL_ISSUES"


async def test_flush_cold_collects_report_withheld(cold_tool, ctx):
    """Auto-collection picks up report_withheld flag."""
    import lifesci_tools.cold_worker as cw

    cw.reset_for_testing()

    await cold_tool._run_async_impl(
        {"operation": "store", "key": "report_withheld", "value": "true", "namespace": "verification"}, ctx
    )

    result = await cold_tool._run_async_impl(
        {"operation": "flush_cold"}, ctx
    )
    assert result["success"] is True
    item = cw._queue.get_nowait()
    assert item["report_withheld"] is True


async def test_flush_cold_blocked_for_read_only(ctx):
    t = MemoryPlaneTool(tool_config={"read_only": True, "cold_db_path": "/tmp/test.db"})
    t._cold_db_path = "/tmp/test.db"
    MemoryPlaneTool._backend = DictBackend()
    result = await t._run_async_impl(
        {"operation": "flush_cold"}, ctx
    )
    assert result["success"] is False
    assert "read-only" in result["error"]


# ── cold operations: seed_session ────────────────────────────


async def test_seed_session_no_config(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "seed_session", "query": "test question"}, ctx
    )
    assert result["success"] is True
    assert result["seeded"] is False
    assert "not available" in result["reason"]


async def test_seed_session_empty_db(cold_tool, ctx):
    from lifesci_tools import cold_store

    # Ensure the DB exists but is empty
    conn = cold_store.get_connection(cold_tool._cold_db_path)
    conn.close()

    result = await cold_tool._run_async_impl(
        {"operation": "seed_session", "query": "test question"}, ctx
    )
    assert result["success"] is True
    assert result["seeded"] is False


async def test_seed_session_with_data(cold_tool, ctx):
    from lifesci_tools import cold_store

    db_path = cold_tool._cold_db_path
    conn = cold_store.get_connection(db_path)
    # Populate enough data
    for i in range(5):
        sid = f"seed-{i}"
        cold_store.write_session_outcome(
            conn, session_id=sid, query_domain="drugs",
            query_text="test question",
            coverage_pct=0.8, verification_score=0.9,
        )
        cold_store.write_routing_pattern(
            conn, session_id=sid, query_domain="drugs",
            agent_name="DrugSpecialist", coverage_contribution=0.5,
            session_verification_score=0.9,
        )
        cold_store.write_source_reliability(
            conn, session_id=sid, source_name="pubmed",
            evidence_count=5, avg_grade_score=3.0,
            passed_verification=True,
        )
        cold_store.upsert_query_pattern(
            conn, normalized_template="question test",
            domain="drugs", coverage_pct=0.8, verification_score=0.9,
        )
    conn.commit()
    cold_store.refresh_learned_strategies(conn)
    conn.close()

    result = await cold_tool._run_async_impl(
        {"operation": "seed_session", "query": "test question"}, ctx
    )
    assert result["success"] is True
    assert result["seeded"] is True

    # Verify seed data was stored in learning namespace
    result = await cold_tool._run_async_impl(
        {"operation": "retrieve", "key": "seed", "namespace": "learning"}, ctx
    )
    assert result["found"] is True


# ── cold operations: query_cold ──────────────────────────────


async def test_query_cold_no_config(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "query_cold", "query": "test"}, ctx
    )
    assert result["success"] is True
    assert result["results"] == []
    assert "not available" in result["reason"]


async def test_query_cold_empty_db(cold_tool, ctx):
    from lifesci_tools import cold_store

    conn = cold_store.get_connection(cold_tool._cold_db_path)
    conn.close()

    result = await cold_tool._run_async_impl(
        {"operation": "query_cold", "query": "test question"}, ctx
    )
    assert result["success"] is True
    assert result["total_sessions"] == 0
    assert result["query_intelligence"] is None


async def test_query_cold_with_data(cold_tool, ctx):
    from lifesci_tools import cold_store

    conn = cold_store.get_connection(cold_tool._cold_db_path)
    cold_store.write_session_outcome(
        conn, session_id="s1", query_domain="drugs",
        query_text="drug interactions",
    )
    cold_store.upsert_query_pattern(
        conn, normalized_template="drug interactions",
        domain="drugs", coverage_pct=0.8, verification_score=0.9,
    )
    conn.commit()
    conn.close()

    result = await cold_tool._run_async_impl(
        {"operation": "query_cold", "query": "drug interactions"}, ctx
    )
    assert result["success"] is True
    assert result["total_sessions"] == 1
    assert result["query_intelligence"] is not None
    assert result["query_intelligence"]["domain"] == "drugs"


# ── cold operations: get_strategy (uses strategy_type param) ─


async def test_get_strategy_no_config(tool, ctx):
    result = await tool._run_async_impl(
        {"operation": "get_strategy", "key": "drugs", "strategy_type": "routing"}, ctx
    )
    assert result["success"] is True
    assert result["strategy"] is None
    assert "not available" in result["reason"]


async def test_get_strategy_requires_key(cold_tool, ctx):
    result = await cold_tool._run_async_impl(
        {"operation": "get_strategy", "strategy_type": "routing"}, ctx
    )
    assert result["success"] is False
    assert "Key is required" in result["error"]


async def test_get_strategy_requires_strategy_type(cold_tool, ctx):
    from lifesci_tools import cold_store
    conn = cold_store.get_connection(cold_tool._cold_db_path)
    conn.close()

    result = await cold_tool._run_async_impl(
        {"operation": "get_strategy", "key": "drugs"}, ctx
    )
    assert result["success"] is False
    assert "strategy_type is required" in result["error"]


async def test_get_strategy_not_found(cold_tool, ctx):
    from lifesci_tools import cold_store

    conn = cold_store.get_connection(cold_tool._cold_db_path)
    conn.close()

    result = await cold_tool._run_async_impl(
        {"operation": "get_strategy", "key": "nonexistent", "strategy_type": "routing"}, ctx
    )
    assert result["success"] is True
    assert result["found"] is False


async def test_get_strategy_found(cold_tool, ctx):
    from lifesci_tools import cold_store

    conn = cold_store.get_connection(cold_tool._cold_db_path)
    for i in range(5):
        sid = f"strat-{i}"
        cold_store.write_session_outcome(
            conn, session_id=sid, query_domain="drugs",
        )
        cold_store.write_routing_pattern(
            conn, session_id=sid, query_domain="drugs",
            agent_name="DrugSpecialist", coverage_contribution=0.5,
            session_verification_score=0.85,
        )
    conn.commit()
    cold_store.refresh_learned_strategies(conn)
    conn.close()

    result = await cold_tool._run_async_impl(
        {"operation": "get_strategy", "key": "drugs", "strategy_type": "routing"}, ctx
    )
    assert result["success"] is True
    assert result["found"] is True
    assert result["strategy"] is not None


# ── cold operations: read-only allows reads ──────────────────


async def test_read_only_allows_query_cold(tmp_path, ctx):
    db_path = str(tmp_path / "ro-cold.db")
    t = MemoryPlaneTool(tool_config={"read_only": True, "cold_db_path": db_path})
    t._cold_db_path = db_path
    MemoryPlaneTool._backend = DictBackend()

    from lifesci_tools import cold_store
    conn = cold_store.get_connection(db_path)
    conn.close()

    result = await t._run_async_impl(
        {"operation": "query_cold", "query": "test"}, ctx
    )
    assert result["success"] is True


async def test_read_only_allows_get_strategy(tmp_path, ctx):
    db_path = str(tmp_path / "ro-cold2.db")
    t = MemoryPlaneTool(tool_config={"read_only": True, "cold_db_path": db_path})
    t._cold_db_path = db_path
    MemoryPlaneTool._backend = DictBackend()

    from lifesci_tools import cold_store
    conn = cold_store.get_connection(db_path)
    conn.close()

    result = await t._run_async_impl(
        {"operation": "get_strategy", "key": "drugs", "strategy_type": "routing"}, ctx
    )
    assert result["success"] is True


async def test_read_only_allows_seed_session(tmp_path, ctx):
    db_path = str(tmp_path / "ro-cold3.db")
    t = MemoryPlaneTool(tool_config={"read_only": True, "cold_db_path": db_path})
    t._cold_db_path = db_path
    MemoryPlaneTool._backend = DictBackend()

    from lifesci_tools import cold_store
    conn = cold_store.get_connection(db_path)
    conn.close()

    result = await t._run_async_impl(
        {"operation": "seed_session", "query": "test"}, ctx
    )
    assert result["success"] is True


# ── cold_db_path stored via init ─────────────────────────────


async def test_init_stores_cold_db_path(tmp_path):
    t = MemoryPlaneTool()
    db_path = str(tmp_path / "init-cold.db")
    await t.init(MagicMock(), {"cold_db_path": db_path})
    assert t._cold_db_path == db_path


async def test_init_cold_db_path_empty_by_default():
    t = MemoryPlaneTool()
    await t.init(MagicMock(), {})
    assert t._cold_db_path == ""


# ── configurable TTL ─────────────────────────────────────────


def test_custom_ttl_stored():
    t = MemoryPlaneTool(tool_config={"ttl_seconds": 7200})
    assert t._ttl_seconds == 7200


def test_default_ttl_is_3600():
    t = MemoryPlaneTool()
    assert t._ttl_seconds == 3600


# ── idempotent store ─────────────────────────────────────────


async def test_idempotent_store_same_value(tool, ctx):
    await tool._run_async_impl(
        {"operation": "store", "key": "k", "value": "v"}, ctx
    )
    result = await tool._run_async_impl(
        {"operation": "store", "key": "k", "value": "v"}, ctx
    )
    assert result["success"] is True
    assert result.get("idempotent") is True


async def test_non_idempotent_store_different_value(tool, ctx):
    await tool._run_async_impl(
        {"operation": "store", "key": "k", "value": "v1"}, ctx
    )
    result = await tool._run_async_impl(
        {"operation": "store", "key": "k", "value": "v2"}, ctx
    )
    assert result["success"] is True
    assert result.get("idempotent") is None
