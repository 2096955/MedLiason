"""Tests for memory plane decomposition: session_state, evidence_store, cold_query.

Also tests the context_compactor callback and protocol_step_validator
updates for the new tool names.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from lifesci_tools.memory_plane import MemoryPlaneTool, DictBackend
from lifesci_tools.session_state import SessionStateTool
from lifesci_tools.evidence_store import EvidenceStoreTool
from lifesci_tools.cold_query import ColdQueryTool
from lifesci_tools.context_compactor import (
    context_compactor_callback,
    _COMPACTION_HINTS,
)
from lifesci_tools.protocol_step_validator import (
    ALLOWED_TOOLS_PER_STEP,
    STEP_ADVANCEMENT_TRIGGERS,
    _is_tool_allowed,
    _detect_step_advancement,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_backend():
    """Reset the shared backend before each test."""
    MemoryPlaneTool._backend = DictBackend()
    yield
    MemoryPlaneTool._backend = None


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


@pytest.fixture
def session_tool():
    return SessionStateTool()


@pytest.fixture
def evidence_tool():
    return EvidenceStoreTool()


@pytest.fixture
def evidence_tool_ro():
    return EvidenceStoreTool(tool_config={"read_only": True})


@pytest.fixture
def cold_tool(tmp_path):
    db_path = str(tmp_path / "test-cold.db")
    t = ColdQueryTool(tool_config={"cold_db_path": db_path})
    t._cold_db_path = db_path
    return t


@pytest.fixture
def cold_tool_no_config():
    return ColdQueryTool()


# ═══════════════════════════════════════════════════════════════════════
# SessionStateTool tests
# ═══════════════════════════════════════════════════════════════════════


class TestSessionStateStore:
    async def test_store_and_retrieve(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "store", "key": "drug", "value": "metformin"}, ctx
        )
        assert result["success"] is True

        result = await session_tool._run_async_impl(
            {"operation": "retrieve", "key": "drug"}, ctx
        )
        assert result["success"] is True
        assert result["found"] is True
        assert result["value"] == "metformin"

    async def test_store_requires_key(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "store", "value": "test"}, ctx
        )
        assert result["success"] is False
        assert "Key is required" in result["error"]

    async def test_retrieve_missing_key(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "retrieve", "key": "nonexistent"}, ctx
        )
        assert result["success"] is True
        assert result["found"] is False

    async def test_idempotent_store(self, session_tool, ctx):
        await session_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v"}, ctx
        )
        result = await session_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v"}, ctx
        )
        assert result["success"] is True
        assert result.get("idempotent") is True


class TestSessionStateNamespaces:
    async def test_only_allows_intermediate_and_verification(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v", "namespace": "citations"},
            ctx,
        )
        assert result["success"] is False
        assert "Invalid namespace" in result["error"]

    async def test_rejects_evidence_namespace(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v", "namespace": "evidence"},
            ctx,
        )
        assert result["success"] is False

    async def test_allows_intermediate(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v", "namespace": "intermediate"},
            ctx,
        )
        assert result["success"] is True

    async def test_allows_verification(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v", "namespace": "verification"},
            ctx,
        )
        assert result["success"] is True

    async def test_default_namespace_is_intermediate(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "store", "key": "test", "value": "val"}, ctx
        )
        assert result["success"] is True
        assert result["namespace"] == "intermediate"


class TestSessionStateAppend:
    async def test_append_creates_list(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "append", "key": "findings", "value": '"first"'}, ctx
        )
        assert result["success"] is True
        assert result["length"] == 1

        result = await session_tool._run_async_impl(
            {"operation": "append", "key": "findings", "value": '"second"'}, ctx
        )
        assert result["length"] == 2

    async def test_append_requires_key(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "append", "value": "test"}, ctx
        )
        assert result["success"] is False


class TestSessionStateClearSession:
    async def test_clear_session(self, session_tool, ctx):
        await session_tool._run_async_impl(
            {"operation": "store", "key": "k1", "value": "v1"}, ctx
        )
        await session_tool._run_async_impl(
            {
                "operation": "store",
                "key": "k2",
                "value": "v2",
                "namespace": "verification",
            },
            ctx,
        )
        result = await session_tool._run_async_impl(
            {"operation": "clear_session"}, ctx
        )
        assert result["success"] is True
        assert result["cleared"] >= 2

    async def test_clear_empty_session(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "clear_session"}, ctx
        )
        assert result["success"] is True
        assert result["cleared"] == 0


class TestSessionStateListKeys:
    async def test_list_keys(self, session_tool, ctx):
        await session_tool._run_async_impl(
            {
                "operation": "store",
                "key": "a",
                "value": "1",
                "namespace": "verification",
            },
            ctx,
        )
        await session_tool._run_async_impl(
            {
                "operation": "store",
                "key": "b",
                "value": "2",
                "namespace": "verification",
            },
            ctx,
        )
        result = await session_tool._run_async_impl(
            {"operation": "list_keys", "namespace": "verification"}, ctx
        )
        assert result["success"] is True
        assert sorted(result["keys"]) == ["a", "b"]

    async def test_list_keys_empty(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "list_keys", "namespace": "verification"}, ctx
        )
        assert result["success"] is True
        assert result["keys"] == []


class TestSessionStateOperations:
    async def test_all_five_operations_accepted(self, session_tool, ctx):
        """session_state supports exactly 5 operations."""
        for op in ("store", "retrieve", "list_keys", "append", "clear_session"):
            args = {"operation": op}
            if op in ("store", "append"):
                args["key"] = "test_key"
                args["value"] = "test_val"
            elif op == "retrieve":
                args["key"] = "test_key"
            result = await session_tool._run_async_impl(args, ctx)
            assert result["success"] is True, f"Operation '{op}' failed: {result}"

    async def test_unknown_operation_rejected(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "flush_cold"}, ctx
        )
        assert result["success"] is False
        assert "Unknown operation" in result["error"]

    async def test_seed_session_rejected(self, session_tool, ctx):
        result = await session_tool._run_async_impl(
            {"operation": "seed_session", "query": "test"}, ctx
        )
        assert result["success"] is False


class TestSessionStateVerificationSharedKeys:
    async def test_verification_keys_shared_across_agents(self, session_tool, ctx):
        """Verification namespace should use shared keys (no agent scope)."""
        # Store in verification namespace
        result = await session_tool._run_async_impl(
            {
                "operation": "store",
                "key": "verdict",
                "value": "PASS",
                "namespace": "verification",
            },
            ctx,
        )
        assert result["success"] is True

        # The key should be in the standard format
        full_key = f"medexpert:sess-001:verification:verdict"
        stored = await MemoryPlaneTool._backend.get(full_key)
        assert stored == "PASS"


# ═══════════════════════════════════════════════════════════════════════
# EvidenceStoreTool tests
# ═══════════════════════════════════════════════════════════════════════


class TestEvidenceStoreNamespaces:
    async def test_only_allows_citations_and_evidence(self, evidence_tool, ctx):
        result = await evidence_tool._run_async_impl(
            {
                "operation": "store",
                "key": "k",
                "value": "v",
                "namespace": "intermediate",
            },
            ctx,
        )
        assert result["success"] is False
        assert "Invalid namespace" in result["error"]

    async def test_rejects_verification_namespace(self, evidence_tool, ctx):
        result = await evidence_tool._run_async_impl(
            {
                "operation": "store",
                "key": "k",
                "value": "v",
                "namespace": "verification",
            },
            ctx,
        )
        assert result["success"] is False

    async def test_allows_citations(self, evidence_tool, ctx):
        result = await evidence_tool._run_async_impl(
            {
                "operation": "store",
                "key": "pmid_123",
                "value": '{"pmid":"123"}',
                "namespace": "citations",
            },
            ctx,
        )
        assert result["success"] is True

    async def test_allows_evidence(self, evidence_tool, ctx):
        result = await evidence_tool._run_async_impl(
            {"operation": "store", "key": "ev1", "value": '{"grade":"High"}'},
            ctx,
        )
        assert result["success"] is True

    async def test_default_namespace_is_evidence(self, evidence_tool, ctx):
        result = await evidence_tool._run_async_impl(
            {"operation": "store", "key": "test", "value": "val"}, ctx
        )
        assert result["success"] is True
        assert result["namespace"] == "evidence"


class TestEvidenceStoreOperations:
    async def test_store_and_retrieve(self, evidence_tool, ctx):
        await evidence_tool._run_async_impl(
            {"operation": "store", "key": "ev1", "value": "data"}, ctx
        )
        result = await evidence_tool._run_async_impl(
            {"operation": "retrieve", "key": "ev1"}, ctx
        )
        assert result["success"] is True
        assert result["found"] is True
        assert result["value"] == "data"

    async def test_list_keys(self, evidence_tool, ctx):
        await evidence_tool._run_async_impl(
            {
                "operation": "store",
                "key": "a",
                "value": "1",
                "namespace": "citations",
            },
            ctx,
        )
        await evidence_tool._run_async_impl(
            {
                "operation": "store",
                "key": "b",
                "value": "2",
                "namespace": "citations",
            },
            ctx,
        )
        result = await evidence_tool._run_async_impl(
            {"operation": "list_keys", "namespace": "citations"}, ctx
        )
        assert sorted(result["keys"]) == ["a", "b"]

    async def test_only_store_retrieve_list_keys(self, evidence_tool, ctx):
        """evidence_store supports exactly 3 operations."""
        result = await evidence_tool._run_async_impl(
            {"operation": "append", "key": "k", "value": "v"}, ctx
        )
        assert result["success"] is False
        assert "Unknown operation" in result["error"]

    async def test_clear_session_not_supported(self, evidence_tool, ctx):
        result = await evidence_tool._run_async_impl(
            {"operation": "clear_session"}, ctx
        )
        assert result["success"] is False

    async def test_idempotent_store(self, evidence_tool, ctx):
        await evidence_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v"}, ctx
        )
        result = await evidence_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v"}, ctx
        )
        assert result.get("idempotent") is True


class TestEvidenceStoreReadOnly:
    async def test_read_only_blocks_store(self, evidence_tool_ro, ctx):
        result = await evidence_tool_ro._run_async_impl(
            {"operation": "store", "key": "k", "value": "v"}, ctx
        )
        assert result["success"] is False
        assert "read-only" in result["error"]

    async def test_read_only_allows_retrieve(self, evidence_tool_ro, ctx):
        # Pre-populate via direct backend write
        key = f"medexpert:sess-001:evidence:drug"
        await MemoryPlaneTool._backend.set(key, "metformin")
        result = await evidence_tool_ro._run_async_impl(
            {"operation": "retrieve", "key": "drug"}, ctx
        )
        assert result["success"] is True
        assert result["found"] is True

    async def test_read_only_allows_list_keys(self, evidence_tool_ro, ctx):
        key = f"medexpert:sess-001:citations:pmid_123"
        await MemoryPlaneTool._backend.set(key, "data")
        result = await evidence_tool_ro._run_async_impl(
            {"operation": "list_keys", "namespace": "citations"}, ctx
        )
        assert result["success"] is True
        assert "pmid_123" in result["keys"]

    async def test_read_only_description_differs(self, evidence_tool, evidence_tool_ro):
        assert "Read-only" in evidence_tool_ro.tool_description
        assert "Read-only" not in evidence_tool.tool_description


# ═══════════════════════════════════════════════════════════════════════
# ColdQueryTool tests
# ═══════════════════════════════════════════════════════════════════════


class TestColdQueryOperations:
    async def test_only_accepts_cold_operations(self, cold_tool, ctx):
        result = await cold_tool._run_async_impl(
            {"operation": "store", "key": "k", "value": "v"}, ctx
        )
        assert result["success"] is False
        assert "Unknown operation" in result["error"]

    async def test_rejects_retrieve(self, cold_tool, ctx):
        result = await cold_tool._run_async_impl(
            {"operation": "retrieve", "key": "k"}, ctx
        )
        assert result["success"] is False

    async def test_rejects_append(self, cold_tool, ctx):
        result = await cold_tool._run_async_impl(
            {"operation": "append", "key": "k", "value": "v"}, ctx
        )
        assert result["success"] is False


class TestColdQuerySeedSession:
    async def test_seed_session_no_config(self, cold_tool_no_config, ctx):
        result = await cold_tool_no_config._run_async_impl(
            {"operation": "seed_session", "query": "test"}, ctx
        )
        assert result["success"] is True
        assert result["seeded"] is False
        assert "not available" in result["reason"]

    async def test_seed_session_empty_db(self, cold_tool, ctx):
        from lifesci_tools import cold_store

        conn = cold_store.get_connection(cold_tool._cold_db_path)
        conn.close()

        result = await cold_tool._run_async_impl(
            {"operation": "seed_session", "query": "test question"}, ctx
        )
        assert result["success"] is True
        assert result["seeded"] is False


class TestColdQueryFlushCold:
    async def test_flush_cold_no_config(self, cold_tool_no_config, ctx):
        result = await cold_tool_no_config._run_async_impl(
            {"operation": "flush_cold"}, ctx
        )
        assert result["success"] is True
        assert result["flushed"] is False
        assert "not available" in result["reason"]

    async def test_flush_cold_enqueues(self, cold_tool, ctx):
        import lifesci_tools.cold_worker as cw

        cw.reset_for_testing()

        # Pre-populate hot store
        backend = MemoryPlaneTool._backend
        await backend.set(
            "medexpert:sess-001:verification:verification_verdict", "PASS"
        )
        await backend.set(
            "medexpert:sess-001:intermediate:coverage_pct", "0.9"
        )

        result = await cold_tool._run_async_impl(
            {"operation": "flush_cold", "query": "test", "query_domain": "drugs"},
            ctx,
        )
        assert result["success"] is True
        assert result["enqueued"] is True
        assert "verification_verdict" in result["auto_collected_keys"]


class TestColdQueryQueryCold:
    async def test_query_cold_no_config(self, cold_tool_no_config, ctx):
        result = await cold_tool_no_config._run_async_impl(
            {"operation": "query_cold", "query": "test"}, ctx
        )
        assert result["success"] is True
        assert result["results"] == []
        assert "not available" in result["reason"]

    async def test_query_cold_empty_db(self, cold_tool, ctx):
        from lifesci_tools import cold_store

        conn = cold_store.get_connection(cold_tool._cold_db_path)
        conn.close()

        result = await cold_tool._run_async_impl(
            {"operation": "query_cold", "query": "test"}, ctx
        )
        assert result["success"] is True
        assert result["total_sessions"] == 0


class TestColdQueryGetStrategy:
    async def test_get_strategy_no_config(self, cold_tool_no_config, ctx):
        result = await cold_tool_no_config._run_async_impl(
            {"operation": "get_strategy", "key": "drugs", "strategy_type": "routing"},
            ctx,
        )
        assert result["success"] is True
        assert result["strategy"] is None
        assert "not available" in result["reason"]

    async def test_get_strategy_requires_key(self, cold_tool, ctx):
        result = await cold_tool._run_async_impl(
            {"operation": "get_strategy", "strategy_type": "routing"}, ctx
        )
        assert result["success"] is False
        assert "Key is required" in result["error"]

    async def test_get_strategy_requires_strategy_type(self, cold_tool, ctx):
        from lifesci_tools import cold_store

        conn = cold_store.get_connection(cold_tool._cold_db_path)
        conn.close()

        result = await cold_tool._run_async_impl(
            {"operation": "get_strategy", "key": "drugs"}, ctx
        )
        assert result["success"] is False
        assert "strategy_type is required" in result["error"]

    async def test_get_strategy_not_found(self, cold_tool, ctx):
        from lifesci_tools import cold_store

        conn = cold_store.get_connection(cold_tool._cold_db_path)
        conn.close()

        result = await cold_tool._run_async_impl(
            {
                "operation": "get_strategy",
                "key": "nonexistent",
                "strategy_type": "routing",
            },
            ctx,
        )
        assert result["success"] is True
        assert result["found"] is False


# ═══════════════════════════════════════════════════════════════════════
# Shared backend singleton tests
# ═══════════════════════════════════════════════════════════════════════


class TestSharedBackend:
    async def test_all_tools_share_same_backend(self, session_tool, evidence_tool, cold_tool):
        """All 3 decomposed tools plus MemoryPlaneTool share the same backend."""
        mp = MemoryPlaneTool()
        assert session_tool._backend is mp._backend
        assert evidence_tool._backend is mp._backend
        assert cold_tool._backend is mp._backend

    async def test_cross_tool_data_visibility(self, session_tool, evidence_tool, ctx):
        """Data written via one tool is visible to the other (via direct key)."""
        # Write evidence via evidence_store
        await evidence_tool._run_async_impl(
            {
                "operation": "store",
                "key": "cit1",
                "value": "citation_data",
                "namespace": "citations",
            },
            ctx,
        )
        # Read directly from backend (as memory_plane would)
        key = "medexpert:sess-001:citations:cit1"
        val = await MemoryPlaneTool._backend.get(key)
        assert val == "citation_data"


# ═══════════════════════════════════════════════════════════════════════
# Context Compactor tests
# ═══════════════════════════════════════════════════════════════════════


def _make_callback_context_and_request(current_step, compaction_enabled=True):
    """Helper to create mock callback_context and llm_request."""
    # Mock session with state
    session = MagicMock()
    session.state = {"_protocol_current_step": current_step}

    # Mock callback_context with _invocation_context.session
    callback_context = MagicMock()
    callback_context._invocation_context = MagicMock()
    callback_context._invocation_context.session = session

    # Mock llm_request
    llm_request = MagicMock()
    llm_request.config = MagicMock()
    llm_request.config.system_instruction = "Original system instruction."

    # Mock host_component
    host_component = MagicMock()
    host_component.get_config.return_value = {
        "context_compaction": compaction_enabled
    }
    # Make get_config return the right value for the specific key
    def _get_config(key, default=None):
        if key == "app_config":
            return {"context_compaction": compaction_enabled}
        return default
    host_component.get_config = _get_config

    return callback_context, llm_request, host_component


class TestContextCompactor:
    def test_does_nothing_when_disabled(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(
            current_step=4, compaction_enabled=False
        )
        result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        # system_instruction should be unchanged
        assert llm_req.config.system_instruction == "Original system instruction."

    def test_does_nothing_at_step_0(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=0)
        result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert llm_req.config.system_instruction == "Original system instruction."

    def test_does_nothing_at_step_1(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=1)
        result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert llm_req.config.system_instruction == "Original system instruction."

    def test_does_nothing_at_step_2(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=2)
        result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert llm_req.config.system_instruction == "Original system instruction."

    def test_injects_hint_at_step_3(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=3)
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert _COMPACTION_HINTS[3] in llm_req.config.system_instruction
        assert "Original system instruction." in llm_req.config.system_instruction

    def test_injects_hint_at_step_4(self):
        """Step 4 should still get the step-3 hint (highest threshold <= 4)."""
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=4)
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert _COMPACTION_HINTS[3] in llm_req.config.system_instruction

    def test_injects_step5_hint_at_step_5(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=5)
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert _COMPACTION_HINTS[5] in llm_req.config.system_instruction
        # Should NOT contain the step-3 hint (replaced by step-5)
        assert _COMPACTION_HINTS[3] not in llm_req.config.system_instruction

    def test_injects_step5_hint_at_step_6(self):
        """Step 6 should get the step-5 hint."""
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=6)
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert _COMPACTION_HINTS[5] in llm_req.config.system_instruction

    def test_always_returns_none(self):
        """Context compactor should never short-circuit the callback chain."""
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=5)
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None

    def test_handles_none_system_instruction(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=3)
        llm_req.config.system_instruction = None
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert llm_req.config.system_instruction == _COMPACTION_HINTS[3]

    def test_handles_empty_system_instruction(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=3)
        llm_req.config.system_instruction = ""
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None
        assert llm_req.config.system_instruction == _COMPACTION_HINTS[3]

    def test_handles_none_config(self):
        cb_ctx, llm_req, host = _make_callback_context_and_request(current_step=3)
        llm_req.config = None
        _patch = patch(
            "lifesci_tools.context_compactor.get_session_from_callback_context",
            return_value=cb_ctx._invocation_context.session,
        )
        with _patch:
            result = context_compactor_callback(cb_ctx, llm_req, host)
        assert result is None  # Graceful no-op


# ═══════════════════════════════════════════════════════════════════════
# Protocol Step Validator updates tests
# ═══════════════════════════════════════════════════════════════════════


class TestProtocolValidatorNewTools:
    def test_session_state_allowed_at_all_steps(self):
        for step in range(7):
            assert _is_tool_allowed("session_state", step), (
                f"session_state should be allowed at step {step}"
            )

    def test_evidence_store_allowed_at_steps_3_4_5(self):
        for step in (3, 4, 5):
            assert _is_tool_allowed("evidence_store", step), (
                f"evidence_store should be allowed at step {step}"
            )

    def test_evidence_store_not_allowed_at_steps_0_1_2_6(self):
        for step in (0, 1, 2, 6):
            assert not _is_tool_allowed("evidence_store", step), (
                f"evidence_store should NOT be allowed at step {step}"
            )

    def test_cold_query_allowed_at_steps_0_and_6(self):
        assert _is_tool_allowed("cold_query", 0)
        assert _is_tool_allowed("cold_query", 6)

    def test_cold_query_not_allowed_at_steps_1_through_5(self):
        for step in range(1, 6):
            assert not _is_tool_allowed("cold_query", step), (
                f"cold_query should NOT be allowed at step {step}"
            )

    def test_cold_query_seed_session_triggers_advancement(self):
        new_step = _detect_step_advancement(
            "cold_query", {"operation": "seed_session"}, current_step=0
        )
        assert new_step == 1

    def test_cold_query_flush_cold_triggers_advancement(self):
        new_step = _detect_step_advancement(
            "cold_query", {"operation": "flush_cold"}, current_step=5
        )
        assert new_step == 6

    def test_session_state_does_not_trigger_advancement(self):
        """session_state is not in STEP_ADVANCEMENT_TRIGGERS."""
        new_step = _detect_step_advancement(
            "session_state", {"operation": "store"}, current_step=3
        )
        # session_state is not memory_plane or cold_query, so no composite key lookup
        # and it's not in STEP_ADVANCEMENT_TRIGGERS directly
        assert new_step == 3

    def test_evidence_store_does_not_trigger_advancement(self):
        new_step = _detect_step_advancement(
            "evidence_store", {"operation": "store"}, current_step=3
        )
        assert new_step == 3


# ═══════════════════════════════════════════════════════════════════════
# Tool metadata tests
# ═══════════════════════════════════════════════════════════════════════


class TestToolMetadata:
    def test_session_state_tool_name(self, session_tool):
        assert session_tool.tool_name == "session_state"

    def test_evidence_store_tool_name(self, evidence_tool):
        assert evidence_tool.tool_name == "evidence_store"

    def test_cold_query_tool_name(self, cold_tool):
        assert cold_tool.tool_name == "cold_query"

    def test_session_state_has_description(self, session_tool):
        assert "session" in session_tool.tool_description.lower()

    def test_evidence_store_has_description(self, evidence_tool):
        assert "evidence" in evidence_tool.tool_description.lower()

    def test_cold_query_has_description(self, cold_tool):
        assert "cold" in cold_tool.tool_description.lower()

    def test_all_tools_have_parameters_schema(self, session_tool, evidence_tool, cold_tool):
        for tool in (session_tool, evidence_tool, cold_tool):
            schema = tool.parameters_schema
            assert schema is not None
            assert schema.properties is not None
            assert "operation" in schema.properties


# ═══════════════════════════════════════════════════════════════════════
# Backward compatibility: original memory_plane still works
# ═══════════════════════════════════════════════════════════════════════


class TestMemoryPlaneFacade:
    async def test_original_memory_plane_still_works(self, ctx):
        """The original MemoryPlaneTool should still function unchanged."""
        mp = MemoryPlaneTool()
        result = await mp._run_async_impl(
            {"operation": "store", "key": "test", "value": "hello"}, ctx
        )
        assert result["success"] is True

        result = await mp._run_async_impl(
            {"operation": "retrieve", "key": "test"}, ctx
        )
        assert result["found"] is True
        assert result["value"] == "hello"

    def test_deprecation_notice_in_docstring(self):
        import lifesci_tools.memory_plane as mp_mod
        assert "deprecated" in (mp_mod.__doc__ or "").lower()
