"""Protocol step validator callback for MedExpert orchestrator.

Enforces the 7-step research protocol by validating that the LLM's
tool calls are allowed at the current protocol step. Uses soft rejection:
invalid tool calls are replaced with an error message, and the LLM
re-plans on the next turn.

Insertion point: after_model_callback chain in setup.py, position 2.5
(after sanitize_tool_names, before process_artifact_blocks).

State layer: uses session.state (persists across full task lifecycle),
NOT callback_context.state (resets every LLM turn).
"""

import fnmatch
import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types as adk_types

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from solace_agent_mesh.agent.sac.component import SamAgentComponent

# ── Protocol step definitions ─────────────────────────────────────────
# Maps each step number to (label, set of allowed tool names).
# "peer_*" is a glob pattern matching any tool starting with "peer_".

STEP_LABELS: Dict[int, str] = {
    0: "SEED",
    1: "PLAN",
    2: "DELEGATE",
    3: "COLLECT",
    4: "SYNTHESIZE",
    5: "VERIFY+REVISE",
    6: "PERSIST",
}

ALLOWED_TOOLS_PER_STEP: Dict[int, Set[str]] = {
    0: {"memory_plane", "phi_redactor", "session_state", "cold_query", "web_search_firecrawl"},
    1: {"query_decomposer", "memory_plane", "session_state"},
    2: {"peer_*", "session_state"},
    3: {"publish_sources", "completeness_checker", "memory_plane", "peer_*", "session_state", "evidence_store"},
    4: {"report_generator", "phi_redactor", "memory_plane", "session_state", "evidence_store"},
    5: {"peer_Verifier*", "peer_Reviser*", "memory_plane", "session_state", "evidence_store"},
    6: {"memory_plane", "graph_writer", "session_state", "cold_query"},
}

# Tools that, when successfully validated, advance the step counter.
# The value is the step to advance TO after the tool completes.
# This enables batching tools from consecutive steps in a single LLM turn.
# For memory_plane, we further inspect the `operation` argument.
STEP_ADVANCEMENT_TRIGGERS: Dict[str, int] = {
    "memory_plane:seed_session": 1,   # seed done -> ready for PLAN
    "cold_query:seed_session": 1,     # cold_query variant of seed
    "query_decomposer": 2,            # plan done -> ready for DELEGATE
    # peer_* tools at step 2 keep us at step 2 (multiple peers in one turn)
    "publish_sources": 3,             # starts COLLECT (valid at step 3)
    "completeness_checker": 3,        # still in COLLECT
    "report_generator": 5,            # synthesize done -> ready for VERIFY
    "peer_VerifierAgent": 5,          # stays in VERIFY+REVISE
    "peer_ReviserAgent": 5,           # stays in VERIFY+REVISE
    "memory_plane:flush_cold": 6,     # PERSIST (terminal)
    "cold_query:flush_cold": 6,       # cold_query variant of flush
}

# Session state key for tracking protocol step
_SESSION_STATE_KEY = "_protocol_current_step"

# Session state key to track PERSIST enforcement attempts (prevents infinite loops)
_PERSIST_REJECTIONS_KEY = "_protocol_persist_rejections"
_MAX_PERSIST_REJECTIONS = 2  # Allow LLM to skip after 2 failed attempts

# PERSIST enforcement only activates when the LLM reaches VERIFY+REVISE
# (step 5+) and tries to skip PERSIST (step 6). Earlier steps pass through
# — non-medical queries can short-circuit, intermediate text at steps 0-4
# is not blocked.
_PERSIST_ENFORCEMENT_THRESHOLD = 5
_REQUIRED_FINAL_STEP = 6

# Session state key set by query_decomposer with the selected agent list.
# When present, peer_* tools at DELEGATE (step 2) and COLLECT (step 3) are
# restricted to only those agents.  If absent, all peer_* tools are allowed
# (graceful fallback).
_SELECTED_AGENTS_KEY = "_selected_agents"


# ── Helper functions ──────────────────────────────────────────────────


def _is_tool_allowed(
    tool_name: str,
    current_step: int,
    selected_agents: Optional[List[str]] = None,
) -> bool:
    """Check whether *tool_name* is permitted at *current_step*.

    Handles glob patterns: ``peer_*`` in the allowed set matches any tool
    whose name starts with ``peer_``.

    When *selected_agents* is provided (set by ``query_decomposer`` in
    session state), ``peer_*`` glob matches at DELEGATE (step 2) and
    COLLECT (step 3) are further restricted to only those agents.  This
    reduces the effective tool count from ~10 peer tools to 3-5, improving
    LLM tool-selection reliability (Cert Domain 2, Task 2.3).

    If *selected_agents* is ``None`` or empty, all ``peer_*`` tools are
    allowed (graceful fallback for when query_decomposer hasn't run or
    couldn't write to session state).
    """
    allowed = ALLOWED_TOOLS_PER_STEP.get(current_step)
    if allowed is None:
        # Unknown step — permissive fallback (don't block)
        return True

    for pattern in allowed:
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(tool_name, pattern):
                # Dynamic peer filtering: at DELEGATE and COLLECT steps,
                # restrict peer_* to only the agents selected by
                # query_decomposer, if available.
                if (
                    selected_agents
                    and pattern == "peer_*"
                    and current_step in (2, 3)
                    and tool_name.startswith("peer_")
                ):
                    # Extract agent name from "peer_AgentName"
                    agent_name = tool_name[len("peer_"):]
                    if agent_name not in selected_agents:
                        log.info(
                            "[ProtocolStepValidator] Peer tool '%s' not in "
                            "selected agents %s — rejected by dynamic filter.",
                            tool_name,
                            selected_agents,
                        )
                        return False
                return True
        elif tool_name == pattern:
            return True
    return False


def _detect_step_advancement(
    tool_name: str,
    tool_args: Optional[Dict] = None,
    current_step: int = 0,
) -> int:
    """Return the new step number if *tool_name* triggers advancement.

    Predictive advancement: when the validator sees a tool call that
    belongs to a later step, it advances the step counter *before*
    validating subsequent tools in the same response.

    Returns *current_step* unchanged if no advancement is triggered.
    """
    tool_args = tool_args or {}

    # Special handling for memory_plane and cold_query — check the `operation` argument
    if tool_name in ("memory_plane", "cold_query"):
        operation = tool_args.get("operation", "").lower()
        composite_key = f"{tool_name}:{operation}"
        if composite_key in STEP_ADVANCEMENT_TRIGGERS:
            new_step = STEP_ADVANCEMENT_TRIGGERS[composite_key]
            if new_step >= current_step:
                return new_step
        # Without a triggering operation, don't advance
        return current_step

    # Direct match in triggers
    if tool_name in STEP_ADVANCEMENT_TRIGGERS:
        new_step = STEP_ADVANCEMENT_TRIGGERS[tool_name]
        if new_step >= current_step:
            return new_step

    # Prefix match for verifier/reviser variants (Pro, Opus suffixes)
    if tool_name.startswith("peer_Verifier") or tool_name.startswith("peer_Reviser"):
        if 5 >= current_step:
            return 5

    # Any peer_* tool when current_step < 2 → advance to step 2
    if tool_name.startswith("peer_") and current_step < 2:
        return 2

    return current_step


def cleanup_step_state(session_state: dict) -> None:
    """Reset ``_protocol_current_step`` to 0.

    Called at task start to prevent stale state from a prior protocol
    run bleeding into a new one.
    """
    session_state[_SESSION_STATE_KEY] = 0
    session_state[_PERSIST_REJECTIONS_KEY] = 0


# ── Main callback ─────────────────────────────────────────────────────


def protocol_step_validator_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
    host_component: "SamAgentComponent",
) -> Optional[LlmResponse]:
    """After-model callback that enforces the 7-step protocol.

    Only active when the agent's ``app_config`` contains
    ``protocol_enforcement: true``.  On invalid tool calls, the
    offending ``function_call`` parts are replaced with a text part
    containing an error message (soft rejection).  The LLM re-plans on
    the next turn.

    Returns ``None`` when no modification is needed (pass-through).
    Returns a modified ``LlmResponse`` when tool calls were rejected.
    """
    log_identifier = "[Callback:ProtocolStepValidator]"

    # Gate: only run if protocol enforcement is enabled
    if not host_component.get_config("app_config", {}).get("protocol_enforcement"):
        return None

    # Skip partial (streaming) responses — only validate final responses
    if llm_response.partial:
        return None

    # Skip responses with no content or no parts
    if not llm_response.content or not llm_response.content.parts:
        return None

    # ── Retrieve session state ──────────────────────────────────
    # Use the same pattern as process_artifact_blocks_callback (callbacks.py:200)
    try:
        from solace_agent_mesh.agent.utils.context_helpers import (
            get_session_from_callback_context,
        )

        session = get_session_from_callback_context(callback_context)
        session_state = session.state
    except (AttributeError, ImportError) as exc:
        log.debug(
            "%s Could not access session state (%s). Skipping validation.",
            log_identifier,
            exc,
        )
        return None

    current_step = session_state.get(_SESSION_STATE_KEY, 0)

    # ── Read selected agents for dynamic peer filtering ──────────
    # Set by query_decomposer at PLAN step.  When present, restricts
    # peer_* tools at DELEGATE/COLLECT to only those agents.
    selected_agents: Optional[List[str]] = session_state.get(
        _SELECTED_AGENTS_KEY
    )

    # ── Extract function_call parts ─────────────────────────────
    parts = llm_response.content.parts
    has_function_calls = any(p.function_call for p in parts)
    if not has_function_calls:
        # ── PERSIST enforcement: reject final text if STEP 6 not reached ──
        # Only enforce at step 5+ (VERIFY+REVISE). Steps 0-4 pass through
        # to allow non-medical short-circuit and intermediate text.
        if _PERSIST_ENFORCEMENT_THRESHOLD <= current_step < _REQUIRED_FINAL_STEP:
            persist_rejections = session_state.get(_PERSIST_REJECTIONS_KEY, 0)
            if persist_rejections < _MAX_PERSIST_REJECTIONS:
                session_state[_PERSIST_REJECTIONS_KEY] = persist_rejections + 1
                log.warning(
                    "%s Final text response at step %d (%s) — STEP 6 (PERSIST) "
                    "not reached. Rejecting (attempt %d/%d).",
                    log_identifier,
                    current_step,
                    STEP_LABELS.get(current_step, "?"),
                    persist_rejections + 1,
                    _MAX_PERSIST_REJECTIONS,
                )
                # Advance to step 6 so the LLM can call PERSIST tools
                session_state[_SESSION_STATE_KEY] = 6
                return LlmResponse(
                    content=adk_types.Content(
                        role="model",
                        parts=[adk_types.Part(text=(
                            "[Protocol Error] You MUST complete STEP 6 (PERSIST) "
                            "before delivering the final answer.\n"
                            "1. Call `memory_plane` with operation='flush_cold' to "
                            "save session signals to cold store.\n"
                            "2. If knowledge graph is available, call `graph_writer` "
                            "with session_id, query_text, domain, specialists_used.\n"
                            "Then deliver your final answer."
                        ))],
                    ),
                    partial=False,
                    turn_complete=False,
                )
            else:
                log.warning(
                    "%s PERSIST enforcement exhausted (%d attempts). "
                    "Allowing final response at step %d.",
                    log_identifier,
                    _MAX_PERSIST_REJECTIONS,
                    current_step,
                )
        return None

    # ── Validate each function call ─────────────────────────────
    # Order: validate tool against current step FIRST. If valid, check
    # for step advancement (which applies to SUBSEQUENT tools in the
    # same response). This prevents a tool from advancing the step
    # and then self-validating at the new step.
    valid_parts: List[adk_types.Part] = []
    rejected: List[Tuple[str, int, str]] = []  # (tool_name, step, allowed_str)
    step = current_step

    for part in parts:
        if not part.function_call:
            # Keep non-function-call parts (text, etc.)
            valid_parts.append(part)
            continue

        tool_name = part.function_call.name
        tool_args = dict(part.function_call.args) if part.function_call.args else {}

        # Validate tool against current step
        if _is_tool_allowed(tool_name, step, selected_agents):
            valid_parts.append(part)
            # Predictive advancement: after a valid tool, check if it
            # triggers a step change for subsequent tools in this turn.
            new_step = _detect_step_advancement(tool_name, tool_args, step)
            if new_step > step:
                log.info(
                    "%s Predictive advancement: step %d (%s) -> step %d (%s) triggered by %s",
                    log_identifier,
                    step,
                    STEP_LABELS.get(step, "?"),
                    new_step,
                    STEP_LABELS.get(new_step, "?"),
                    tool_name,
                )
                step = new_step
        else:
            allowed = ALLOWED_TOOLS_PER_STEP.get(step, set())
            allowed_str = ", ".join(sorted(allowed)) if allowed else "(none)"
            rejected.append((tool_name, step, allowed_str))
            log.warning(
                "%s Rejected tool '%s' at step %d (%s). Allowed: {%s}",
                log_identifier,
                tool_name,
                step,
                STEP_LABELS.get(step, "?"),
                allowed_str,
            )

    # ── Persist updated step ────────────────────────────────────
    if step != current_step:
        session_state[_SESSION_STATE_KEY] = step
        log.info(
            "%s Updated session step: %d -> %d",
            log_identifier,
            current_step,
            step,
        )

    # ── Build response ──────────────────────────────────────────
    if not rejected:
        return None  # All tools valid — pass through

    # Build error text for rejected tools
    error_lines = []
    for tool_name, at_step, allowed_str in rejected:
        step_label = STEP_LABELS.get(at_step, f"step {at_step}")
        error_lines.append(
            f"[Protocol Error] Cannot call {tool_name} at step {at_step} "
            f"({step_label}). Expected tools: {{{allowed_str}}}. "
            f"Complete the current step first."
        )

    error_text = "\n".join(error_lines)
    error_part = adk_types.Part(text=error_text)

    # Include the error text alongside any valid parts
    result_parts = valid_parts + [error_part] if valid_parts else [error_part]

    log.info(
        "%s Rejected %d tool call(s). Returning soft rejection.",
        log_identifier,
        len(rejected),
    )

    return LlmResponse(
        content=adk_types.Content(
            role="model",
            parts=result_parts,
        ),
        partial=False,
        turn_complete=False,  # Force LLM to re-plan on next turn
    )
