"""Triage handoff guard — after_model_callback preventing premature task completion.

When the TriageIntakeAgent's triage_intake tool returns status="complete", the LLM
MUST call peer_TriageOrchestratorAgent in the same response turn. If the LLM
generates text indicating handoff intent without the peer tool call, this callback
soft-rejects the response so the LLM retries with the tool call included.
"""

import logging
import re
from typing import TYPE_CHECKING

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types as adk_types

if TYPE_CHECKING:
    from solace_agent_mesh.agent.sac.component import SamAgentComponent

log = logging.getLogger(__name__)

# Patterns indicating handoff intent in the LLM's text response
_HANDOFF_PATTERNS = re.compile(
    r"routing your case|specialist panel|routing.*to.*specialist|"
    r"routing.*case.*analysis|sending.*orchestrator",
    re.IGNORECASE,
)

_PEER_TOOL_PREFIX = "peer_TriageOrchestratorAgent"


def triage_handoff_guard_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
    host_component: "SamAgentComponent",
) -> LlmResponse | None:
    """After-model callback that ensures the intake agent calls the peer tool.

    Only active when:
    - Agent is TriageIntakeAgent
    - Config has triage_handoff_guard: true
    - Session state has _triage_intake_complete: True
    - LLM response is final (not partial/streaming)

    Returns None (pass-through) when no issue detected.
    Returns a modified LlmResponse with soft-rejection text when the LLM
    generated handoff text without the peer tool call.
    """
    # Gate: only run for TriageIntakeAgent
    agent_name = host_component.get_config("agent_name", "")
    if "TriageIntakeAgent" not in agent_name:
        return None

    # Gate: config flag
    app_config = host_component.get_config("app_config", {})
    if not (app_config or {}).get("triage_handoff_guard"):
        return None

    # Skip partial (streaming) responses
    if llm_response.partial:
        return None

    # Gate: triage_intake must have returned complete
    state = callback_context.state or {}
    if not state.get("_triage_intake_complete"):
        return None

    # Check if response has content
    if not llm_response.content or not llm_response.content.parts:
        return None

    # Check for peer tool call (prefix match for Pro/Opus variants)
    has_peer_call = any(
        p.function_call and (p.function_call.name or "").startswith(_PEER_TOOL_PREFIX)
        for p in llm_response.content.parts
    )
    if has_peer_call:
        return None  # Correct behavior — pass through

    # Check for handoff intent in text
    text_parts = [p.text for p in llm_response.content.parts if p.text]
    combined_text = " ".join(text_parts)

    if not _HANDOFF_PATTERNS.search(combined_text):
        return None  # No handoff intent detected — pass through

    # Derive the expected peer tool name from the agent's own suffix
    suffix = agent_name.replace("TriageIntakeAgent", "")
    peer_tool_name = f"{_PEER_TOOL_PREFIX}{suffix}"

    # SOFT REJECT: LLM said it would route but didn't call the tool
    log.warning(
        "[Callback:TriageHandoffGuard] Soft-rejecting response: "
        "handoff text detected without %s tool call. Agent will retry.",
        peer_tool_name,
    )

    rejection_text = (
        f"You indicated you would route the case but did not call "
        f"{peer_tool_name}. You MUST call {peer_tool_name} with the "
        f"clinical note JSON as the task_description. Include your 'Thank you' "
        f"text AND the tool call in the same response."
    )

    return LlmResponse(
        content=adk_types.Content(
            role="model",
            parts=[adk_types.Part(text=rejection_text)],
        ),
    )
