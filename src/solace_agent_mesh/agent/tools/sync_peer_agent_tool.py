"""
Synchronous peer agent tool — blocks until the peer responds.

Unlike PeerAgentTool (fire-and-forget), this tool waits for the peer's
response and returns it directly to the calling LLM. Useful for
multi-turn conversations between agents (e.g., orchestrator <-> verifier).
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from ...common.constants import DEFAULT_COMMUNICATION_TIMEOUT
from ...common.exceptions import MessageSizeExceededError
from .peer_agent_tool import PeerAgentTool, CORRELATION_DATA_PREFIX

log = logging.getLogger(__name__)

SYNC_PEER_TOOL_PREFIX = "sync_peer_"


class SyncPeerAgentTool(PeerAgentTool):
    """
    Synchronous peer agent tool — blocks until the peer responds.

    Unlike PeerAgentTool (fire-and-forget), this tool waits for the peer's
    response and returns it directly to the calling LLM. Useful for
    multi-turn conversations between agents (e.g., orchestrator <-> verifier).

    The tool submits an A2A task identically to PeerAgentTool, but instead of
    returning None immediately, it registers an asyncio.Event on the host
    component and waits for the response handler to deliver the result.

    When the response arrives via ``handle_a2a_response``, the event handler
    checks ``_pending_sync_responses`` on the component. If the sub_task_id
    is found there, it stores the payload and sets the event — bypassing the
    normal parallel-result / re-trigger flow entirely.
    """

    is_long_running = False  # Blocks until response

    def __init__(self, target_agent_name: str, host_component):
        super().__init__(
            target_agent_name=target_agent_name,
            host_component=host_component,
        )
        # Override the name to use the sync prefix
        self.name = f"{SYNC_PEER_TOOL_PREFIX}{target_agent_name}"
        self.log_identifier = (
            f"{host_component.log_identifier}[SyncPeerTool:{target_agent_name}]"
        )
        # Override is_long_running after super().__init__ set it to True
        self.is_long_running = False

    def _get_declaration(self) -> Optional[adk_types.FunctionDeclaration]:
        """
        Generates the FunctionDeclaration. Identical to PeerAgentTool but
        uses the sync tool name and adds a ``max_wait_seconds`` parameter.
        """
        agent_card = self._get_peer_agent_card()
        if not agent_card:
            log.warning(
                "%s Peer agent '%s' not found in registry. Tool unavailable.",
                self.log_identifier,
                self.target_agent_name,
            )
            return None

        self.description = (
            f"Send a task to the {self.target_agent_name} agent and WAIT for the response. "
            f"Returns the peer's reply directly. Use this when you need the answer before proceeding."
        )

        # Build enhanced description (reuse parent logic for skills etc.)
        enhanced = self._build_enhanced_description(agent_card)
        self.description = (
            f"[SYNC] {enhanced}\n\n"
            "This tool blocks until the peer responds or the timeout is reached."
        )

        parameters_schema = adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "task_description": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="Detailed description of the task for the peer agent.",
                ),
                "artifacts": adk_types.Schema(
                    type=adk_types.Type.ARRAY,
                    items=adk_types.Schema(
                        type=adk_types.Type.OBJECT,
                        properties={
                            "filename": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="The filename of the artifact.",
                            ),
                            "version": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="The version of the artifact (e.g., 'latest' or a number). Defaults to 'latest'.",
                                nullable=True,
                            ),
                        },
                        required=["filename"],
                    ),
                    description="A list of artifacts to provide as context to the peer agent.",
                    nullable=True,
                ),
                "max_wait_seconds": adk_types.Schema(
                    type=adk_types.Type.INTEGER,
                    description=(
                        "Maximum seconds to wait for the peer's response. "
                        "Defaults to the agent's configured request_timeout_seconds. "
                        "Capped at 600 seconds."
                    ),
                    nullable=True,
                ),
            },
            required=["task_description"],
        )

        return adk_types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=parameters_schema,
        )

    async def run_async(
        self, *, args: Dict[str, Any], tool_context: ToolContext
    ) -> Any:
        """
        Sends a task to the peer agent and blocks until the response arrives.

        1. Submits the A2A task identically to PeerAgentTool.
        2. Registers an asyncio.Event in host_component._pending_sync_responses.
        3. Waits for the event (set by handle_a2a_response when the peer replies).
        4. Returns the peer's response text directly.
        """
        sub_task_id = f"{CORRELATION_DATA_PREFIX}{uuid.uuid4().hex}"
        log_id = f"{self.log_identifier}[SubTask:{sub_task_id}]"

        try:
            agent_card = self._get_peer_agent_card()
            if not agent_card:
                return {
                    "status": "error",
                    "message": f"Peer agent '{self.target_agent_name}' not found or unavailable.",
                }

            original_task_context = tool_context.state.get("a2a_context", {})
            main_logical_task_id = original_task_context.get(
                "logical_task_id", "unknown_task"
            )
            user_id = tool_context._invocation_context.user_id
            user_config = original_task_context.get("a2a_user_config", {})
            invocation_id = tool_context._invocation_context.invocation_id

            # Determine timeout
            config_timeout = self.host_component.get_config(
                "inter_agent_communication", {}
            ).get("request_timeout_seconds", DEFAULT_COMMUNICATION_TIMEOUT)
            max_wait = args.get("max_wait_seconds")
            if max_wait is not None:
                timeout_sec = min(int(max_wait), 600)
            else:
                timeout_sec = config_timeout

            # Get the task context
            task_context_obj = None
            with self.host_component.active_tasks_lock:
                task_context_obj = self.host_component.active_tasks.get(
                    main_logical_task_id
                )

            if not task_context_obj:
                return {
                    "status": "error",
                    "message": f"TaskExecutionContext not found for task '{main_logical_task_id}'",
                }

            # Prepare and send the A2A message (reuse parent logic)
            from ...common import a2a

            a2a_message_parts = self._prepare_a2a_parts(args, tool_context)
            a2a_metadata = {}
            raw_artifacts = args.get("artifacts", [])
            if raw_artifacts and isinstance(raw_artifacts, list):
                try:
                    a2a_metadata["invoked_with_artifacts"] = raw_artifacts
                except Exception:
                    pass

            a2a_metadata["sessionBehavior"] = "RUN_BASED"
            a2a_metadata["parentTaskId"] = main_logical_task_id
            a2a_metadata["function_call_id"] = tool_context.function_call_id
            a2a_metadata["agent_name"] = self.target_agent_name

            # Propagate peer_help origin for loop prevention
            caller_name = self.host_component.get_config("agent_name", "")
            caller_iac = self.host_component.get_config(
                "inter_agent_communication", {}
            )
            if caller_iac.get("peer_help", {}).get("enabled"):
                a2a_metadata["_peer_help_origin"] = caller_name

            a2a_message = a2a.create_user_message(
                parts=a2a_message_parts,
                metadata=a2a_metadata,
                context_id=original_task_context.get("contextId"),
            )

            correlation_data = {
                "adk_function_call_id": tool_context.function_call_id,
                "original_task_context": original_task_context,
                "peer_tool_name": self.name,
                "peer_agent_name": self.target_agent_name,
                "logical_task_id": main_logical_task_id,
                "invocation_id": invocation_id,
                "is_sync": True,  # Signal for the response handler
            }

            # Register the sub-task in the parent task context
            task_context_obj.register_peer_sub_task(sub_task_id, correlation_data)

            # Register the sync waiter BEFORE sending to avoid race conditions
            event = asyncio.Event()
            with self.host_component.peer_response_queue_lock:
                self.host_component._pending_sync_responses[sub_task_id] = {
                    "event": event,
                    "result": None,
                }

            # Add cache entry for timeout tracking (still needed for cleanup)
            self.host_component.cache_service.add_data(
                key=sub_task_id,
                value=main_logical_task_id,
                expiry=timeout_sec,
                component=self.host_component,
            )

            # Submit the A2A task
            try:
                self.host_component.submit_a2a_task(
                    target_agent_name=self.target_agent_name,
                    a2a_message=a2a_message,
                    user_id=user_id,
                    user_config=user_config,
                    sub_task_id=sub_task_id,
                )
            except MessageSizeExceededError as e:
                self._cleanup_sync_waiter(sub_task_id)
                return {
                    "status": "error",
                    "message": f"Message size exceeded: {e}",
                }

            log.info(
                "%s Sync task submitted. Waiting up to %ds for response.",
                log_id,
                timeout_sec,
            )

            # Wait for the response
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                log.warning(
                    "%s Timed out after %ds waiting for peer response.",
                    log_id,
                    timeout_sec,
                )
                self._cleanup_sync_waiter(sub_task_id)
                return {
                    "status": "timeout",
                    "message": (
                        f"Peer agent '{self.target_agent_name}' did not respond "
                        f"within {timeout_sec} seconds."
                    ),
                }

            # Retrieve the result
            with self.host_component.peer_response_queue_lock:
                waiter = self.host_component._pending_sync_responses.pop(
                    sub_task_id, None
                )

            if waiter and waiter.get("result") is not None:
                result = waiter["result"]
                log.info(
                    "%s Received sync response from peer.",
                    log_id,
                )
                return result
            else:
                return {
                    "status": "error",
                    "message": "Sync response was signaled but no result was stored.",
                }

        except Exception as e:
            log.exception("%s Error during sync peer tool execution: %s", log_id, e)
            self._cleanup_sync_waiter(sub_task_id)
            return {
                "status": "error",
                "message": f"Failed to delegate sync task to '{self.target_agent_name}': {e}",
            }

    def _cleanup_sync_waiter(self, sub_task_id: str) -> None:
        """Remove the sync waiter entry to avoid leaks."""
        with self.host_component.peer_response_queue_lock:
            self.host_component._pending_sync_responses.pop(sub_task_id, None)
