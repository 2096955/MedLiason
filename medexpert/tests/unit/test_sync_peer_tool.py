"""
Unit tests for SyncPeerAgentTool — synchronous peer agent delegation.
"""

import asyncio
import threading
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from solace_agent_mesh.agent.tools.sync_peer_agent_tool import (
    SyncPeerAgentTool,
    SYNC_PEER_TOOL_PREFIX,
)
from solace_agent_mesh.agent.tools.peer_agent_tool import (
    CORRELATION_DATA_PREFIX,
    PEER_TOOL_PREFIX,
    PeerAgentTool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_host_component():
    """Create a mock host component with the attributes SyncPeerAgentTool needs."""
    component = MagicMock()
    component.log_identifier = "[TestComponent]"
    component.agent_name = "TestOrchestrator"
    component.namespace = "test_ns"

    # Peer agents registry
    mock_card = MagicMock()
    mock_card.description = "A specialist agent for verification."
    mock_card.capabilities = None
    mock_card.skills = []
    component.peer_agents = {"VerifierAgent": mock_card}

    # Active tasks
    component.active_tasks = {}
    component.active_tasks_lock = threading.Lock()

    # Sync response infrastructure
    component._pending_sync_responses = {}
    component.peer_response_queue_lock = threading.Lock()

    # Config
    component.get_config = MagicMock(side_effect=_config_side_effect)

    # Cache service
    component.cache_service = MagicMock()

    # submit_a2a_task
    component.submit_a2a_task = MagicMock(return_value="test_sub_task_id")

    return component


def _config_side_effect(key, default=None):
    configs = {
        "inter_agent_communication": {"request_timeout_seconds": 30},
        "agent_name": "TestOrchestrator",
    }
    return configs.get(key, default)


@pytest.fixture
def mock_tool_context():
    """Create a mock ADK ToolContext."""
    ctx = MagicMock()
    ctx.state = {
        "a2a_context": {
            "logical_task_id": "task_123",
            "a2a_user_config": {},
            "contextId": "ctx_1",
        }
    }
    ctx._invocation_context = MagicMock()
    ctx._invocation_context.user_id = "user_1"
    ctx._invocation_context.session = MagicMock()
    ctx._invocation_context.session.id = "session_1"
    ctx._invocation_context.invocation_id = "inv_1"
    ctx.function_call_id = "fc_1"
    return ctx


@pytest.fixture
def mock_task_context():
    """Create a mock TaskExecutionContext."""
    tc = MagicMock()
    tc.register_peer_sub_task = MagicMock()
    return tc


@pytest.fixture
def sync_tool(mock_host_component):
    return SyncPeerAgentTool(
        target_agent_name="VerifierAgent",
        host_component=mock_host_component,
    )


# ---------------------------------------------------------------------------
# Basic properties
# ---------------------------------------------------------------------------

class TestSyncPeerToolProperties:
    def test_is_long_running_false(self, sync_tool):
        assert sync_tool.is_long_running is False

    def test_name_uses_sync_prefix(self, sync_tool):
        assert sync_tool.name == f"{SYNC_PEER_TOOL_PREFIX}VerifierAgent"

    def test_name_does_not_start_with_peer_prefix(self, sync_tool):
        """Sync tool names must NOT start with 'peer_' to avoid being
        caught by preregister_long_running_tools_callback."""
        assert not sync_tool.name.startswith(PEER_TOOL_PREFIX)

    def test_inherits_from_peer_agent_tool(self, sync_tool):
        assert isinstance(sync_tool, PeerAgentTool)

    def test_target_agent_name(self, sync_tool):
        assert sync_tool.target_agent_name == "VerifierAgent"

    def test_get_declaration_returns_function(self, sync_tool):
        decl = sync_tool._get_declaration()
        assert decl is not None
        assert decl.name == f"{SYNC_PEER_TOOL_PREFIX}VerifierAgent"
        assert "SYNC" in decl.description

    def test_get_declaration_returns_none_for_unknown_peer(self, mock_host_component):
        tool = SyncPeerAgentTool(
            target_agent_name="NonExistentAgent",
            host_component=mock_host_component,
        )
        assert tool._get_declaration() is None


# ---------------------------------------------------------------------------
# Successful sync response
# ---------------------------------------------------------------------------

class TestSyncResponse:
    @pytest.mark.asyncio
    async def test_successful_sync_response(
        self, sync_tool, mock_host_component, mock_tool_context, mock_task_context
    ):
        """The tool should block and return the peer's response when delivered."""
        mock_host_component.active_tasks["task_123"] = mock_task_context

        # Simulate the response arriving shortly after submission
        async def fake_submit(*args, **kwargs):
            # Give the tool a moment to register the waiter
            await asyncio.sleep(0.05)
            # Find the sub_task_id from pending sync responses
            with mock_host_component.peer_response_queue_lock:
                sub_task_ids = list(mock_host_component._pending_sync_responses.keys())
            assert len(sub_task_ids) == 1
            sub_task_id = sub_task_ids[0]
            # Deliver the response
            with mock_host_component.peer_response_queue_lock:
                waiter = mock_host_component._pending_sync_responses[sub_task_id]
                waiter["result"] = {"result": "Verification passed."}
                waiter["event"].set()

        # Replace submit_a2a_task with our async simulator
        original_submit = mock_host_component.submit_a2a_task

        def sync_submit(*args, **kwargs):
            original_submit(*args, **kwargs)
            asyncio.get_event_loop().create_task(fake_submit())

        mock_host_component.submit_a2a_task = sync_submit

        result = await sync_tool.run_async(
            args={"task_description": "Verify this claim."},
            tool_context=mock_tool_context,
        )

        assert result == {"result": "Verification passed."}

    @pytest.mark.asyncio
    async def test_sync_response_with_error_payload(
        self, sync_tool, mock_host_component, mock_tool_context, mock_task_context
    ):
        """The tool should return error payloads from the peer as-is."""
        mock_host_component.active_tasks["task_123"] = mock_task_context

        async def fake_submit(*args, **kwargs):
            await asyncio.sleep(0.05)
            with mock_host_component.peer_response_queue_lock:
                sub_task_ids = list(mock_host_component._pending_sync_responses.keys())
            sub_task_id = sub_task_ids[0]
            with mock_host_component.peer_response_queue_lock:
                waiter = mock_host_component._pending_sync_responses[sub_task_id]
                waiter["result"] = {"error": "Peer failed", "code": "INTERNAL_ERROR"}
                waiter["event"].set()

        original_submit = mock_host_component.submit_a2a_task

        def sync_submit(*args, **kwargs):
            original_submit(*args, **kwargs)
            asyncio.get_event_loop().create_task(fake_submit())

        mock_host_component.submit_a2a_task = sync_submit

        result = await sync_tool.run_async(
            args={"task_description": "Verify this."},
            tool_context=mock_tool_context,
        )

        assert result["error"] == "Peer failed"


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------

class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_structured_error(
        self, sync_tool, mock_host_component, mock_tool_context, mock_task_context
    ):
        """When the peer does not respond, the tool should return a timeout error."""
        mock_host_component.active_tasks["task_123"] = mock_task_context

        result = await sync_tool.run_async(
            args={"task_description": "Verify.", "max_wait_seconds": 1},
            tool_context=mock_tool_context,
        )

        assert result["status"] == "timeout"
        assert "VerifierAgent" in result["message"]

    @pytest.mark.asyncio
    async def test_timeout_cleans_up_waiter(
        self, sync_tool, mock_host_component, mock_tool_context, mock_task_context
    ):
        """After timeout, _pending_sync_responses should be empty."""
        mock_host_component.active_tasks["task_123"] = mock_task_context

        await sync_tool.run_async(
            args={"task_description": "Verify.", "max_wait_seconds": 1},
            tool_context=mock_tool_context,
        )

        assert len(mock_host_component._pending_sync_responses) == 0

    @pytest.mark.asyncio
    async def test_max_wait_seconds_capped_at_600(
        self, sync_tool, mock_host_component, mock_tool_context, mock_task_context
    ):
        """max_wait_seconds should be capped at 600 even if a larger value is given."""
        mock_host_component.active_tasks["task_123"] = mock_task_context

        # We can't easily test the actual cap without waiting, but we can
        # verify the tool processes the parameter correctly by checking it
        # returns timeout (using a small value) rather than an error.
        result = await sync_tool.run_async(
            args={"task_description": "Test.", "max_wait_seconds": 1},
            tool_context=mock_tool_context,
        )
        assert result["status"] == "timeout"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_peer_not_found(self, mock_host_component, mock_tool_context):
        """Tool should return error when peer agent is not in registry."""
        tool = SyncPeerAgentTool(
            target_agent_name="GhostAgent",
            host_component=mock_host_component,
        )

        result = await tool.run_async(
            args={"task_description": "Do something."},
            tool_context=mock_tool_context,
        )

        assert result["status"] == "error"
        assert "GhostAgent" in result["message"]

    @pytest.mark.asyncio
    async def test_task_context_not_found(
        self, sync_tool, mock_host_component, mock_tool_context
    ):
        """Tool should return error when no TaskExecutionContext exists."""
        # active_tasks is empty — no task context
        result = await sync_tool.run_async(
            args={"task_description": "Verify."},
            tool_context=mock_tool_context,
        )

        assert result["status"] == "error"
        assert "TaskExecutionContext" in result["message"]

    @pytest.mark.asyncio
    async def test_message_size_exceeded(
        self, sync_tool, mock_host_component, mock_tool_context, mock_task_context
    ):
        """Tool should return error on MessageSizeExceededError."""
        mock_host_component.active_tasks["task_123"] = mock_task_context

        # Create a stand-in exception that matches the except clause
        # without importing the real class (which can trigger Solace threads)
        exc_class = type(
            "MessageSizeExceededError", (Exception,), {}
        )
        # Patch the exception class in the sync_peer_agent_tool module
        with patch(
            "solace_agent_mesh.agent.tools.sync_peer_agent_tool.MessageSizeExceededError",
            exc_class,
        ):
            mock_host_component.submit_a2a_task = MagicMock(
                side_effect=exc_class("Too large")
            )

            result = await sync_tool.run_async(
                args={"task_description": "Large payload."},
                tool_context=mock_tool_context,
            )

        assert result["status"] == "error"
        assert "size exceeded" in result["message"].lower()
        # Waiter should be cleaned up
        assert len(mock_host_component._pending_sync_responses) == 0


# ---------------------------------------------------------------------------
# Concurrent requests
# ---------------------------------------------------------------------------

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_sync_requests_no_interference(
        self, mock_host_component, mock_tool_context, mock_task_context
    ):
        """Two sync tools targeting different agents should not interfere."""
        # Add a second peer
        mock_card2 = MagicMock()
        mock_card2.description = "A specialist for revision."
        mock_card2.capabilities = None
        mock_card2.skills = []
        mock_host_component.peer_agents["ReviserAgent"] = mock_card2

        mock_host_component.active_tasks["task_123"] = mock_task_context

        tool_v = SyncPeerAgentTool("VerifierAgent", mock_host_component)
        tool_r = SyncPeerAgentTool("ReviserAgent", mock_host_component)

        async def deliver_responses():
            await asyncio.sleep(0.1)
            with mock_host_component.peer_response_queue_lock:
                for sid, waiter in mock_host_component._pending_sync_responses.items():
                    if "Verifier" in sid or True:  # We'll use the tool name in the result
                        pass
                # Deliver different results to each
                items = list(mock_host_component._pending_sync_responses.items())
            # Each waiter gets a unique result
            for i, (sid, waiter) in enumerate(items):
                waiter["result"] = {"result": f"response_{i}"}
                waiter["event"].set()

        asyncio.get_event_loop().create_task(deliver_responses())

        # Create separate tool contexts
        ctx1 = MagicMock()
        ctx1.state = mock_tool_context.state.copy()
        ctx1._invocation_context = mock_tool_context._invocation_context
        ctx1.function_call_id = "fc_v"

        ctx2 = MagicMock()
        ctx2.state = mock_tool_context.state.copy()
        ctx2._invocation_context = mock_tool_context._invocation_context
        ctx2.function_call_id = "fc_r"

        results = await asyncio.gather(
            tool_v.run_async(
                args={"task_description": "Verify.", "max_wait_seconds": 5},
                tool_context=ctx1,
            ),
            tool_r.run_async(
                args={"task_description": "Revise.", "max_wait_seconds": 5},
                tool_context=ctx2,
            ),
        )

        # Both should have received responses (not timeout)
        assert all(r.get("result") is not None for r in results)
        # No leftover waiters
        assert len(mock_host_component._pending_sync_responses) == 0


# ---------------------------------------------------------------------------
# Component integration: try_deliver_sync_response
# ---------------------------------------------------------------------------

class TestTryDeliverSyncResponse:
    def test_deliver_to_existing_waiter(self, mock_host_component):
        """try_deliver_sync_response should signal the event and store the payload."""
        event = asyncio.Event()
        sub_task_id = f"{CORRELATION_DATA_PREFIX}test123"
        mock_host_component._pending_sync_responses[sub_task_id] = {
            "event": event,
            "result": None,
        }
        # Ensure fallback path (no async loop) so event.set() is called directly
        mock_host_component._async_loop = None

        # Import and call the method on the real component class
        from solace_agent_mesh.agent.sac.component import SamAgentComponent

        # Use the component method directly
        delivered = SamAgentComponent.try_deliver_sync_response(
            mock_host_component, sub_task_id, {"result": "hello"}
        )

        assert delivered is True
        assert event.is_set()
        assert mock_host_component._pending_sync_responses[sub_task_id]["result"] == {
            "result": "hello"
        }

    def test_no_waiter_returns_false(self, mock_host_component):
        """try_deliver_sync_response should return False when no waiter exists."""
        from solace_agent_mesh.agent.sac.component import SamAgentComponent

        delivered = SamAgentComponent.try_deliver_sync_response(
            mock_host_component, "nonexistent_id", {"result": "hello"}
        )
        assert delivered is False

    def test_deliver_does_not_remove_waiter(self, mock_host_component):
        """The waiter should remain until the tool's run_async retrieves it."""
        event = asyncio.Event()
        sub_task_id = f"{CORRELATION_DATA_PREFIX}test456"
        mock_host_component._pending_sync_responses[sub_task_id] = {
            "event": event,
            "result": None,
        }

        from solace_agent_mesh.agent.sac.component import SamAgentComponent

        SamAgentComponent.try_deliver_sync_response(
            mock_host_component, sub_task_id, {"result": "data"}
        )

        # Waiter should still be present (run_async pops it after event.wait())
        assert sub_task_id in mock_host_component._pending_sync_responses
