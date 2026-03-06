"""Context Compactor — before_model_callback for context-heavy protocol steps.

Injects summarization hints into the LLM system instruction at steps
where raw conversation history becomes redundant because the relevant
data has been stored in the evidence store or memory plane.

Gated by ``context_compaction: true`` in the orchestrator's app_config.

Insertion point: before_model_callback chain in setup.py, after
inject_dynamic_instructions_callback.
"""

import logging
from typing import TYPE_CHECKING, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from solace_agent_mesh.agent.utils.context_helpers import (
    get_session_from_callback_context,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from solace_agent_mesh.agent.sac.component import SamAgentComponent

# Session state key used by the protocol step validator.
_SESSION_STATE_KEY = "_protocol_current_step"

# ── Compaction hints per step ─────────────────────────────────────────
# Each entry maps a protocol step to the hint injected AFTER that step
# has been reached. The hint is appended to llm_request.config.system_instruction.

_COMPACTION_HINTS = {
    3: (
        "[Context Compaction] Specialist outputs from STEPS 2-3 have been "
        "summarized in the evidence store. Reference stored evidence, not "
        "raw specialist responses above."
    ),
    5: (
        "[Context Compaction] Verification complete. Only the synthesis and "
        "verdict matter for the final response."
    ),
}


def context_compactor_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
    host_component: "SamAgentComponent",
) -> Optional[LlmResponse]:
    """Before-model callback that injects context compaction hints.

    Only active when the agent's ``app_config`` contains
    ``context_compaction: true``.

    Does NOT modify conversation history — only appends a system
    instruction to the LLM request at appropriate protocol steps.

    Returns ``None`` always (never short-circuits the callback chain).
    """
    log_identifier = "[Callback:ContextCompactor]"

    # Gate: only run if context compaction is enabled
    if not host_component.get_config("app_config", {}).get("context_compaction"):
        return None

    # Read current protocol step from session state
    try:
        session = get_session_from_callback_context(callback_context)
        session_state = session.state
    except (AttributeError, ImportError) as exc:
        log.debug(
            "%s Could not access session state (%s). Skipping.",
            log_identifier,
            exc,
        )
        return None

    current_step = session_state.get(_SESSION_STATE_KEY, 0)

    # Find the applicable hint: use the highest step <= current_step
    hint = None
    for step_threshold in sorted(_COMPACTION_HINTS.keys(), reverse=True):
        if current_step >= step_threshold:
            hint = _COMPACTION_HINTS[step_threshold]
            break

    if not hint:
        return None

    # Inject into system instruction
    if llm_request.config is None:
        log.debug(
            "%s llm_request.config is None, cannot inject compaction hint.",
            log_identifier,
        )
        return None

    if llm_request.config.system_instruction is None:
        llm_request.config.system_instruction = ""

    if llm_request.config.system_instruction:
        llm_request.config.system_instruction += "\n\n---\n\n" + hint
    else:
        llm_request.config.system_instruction = hint

    log.info(
        "%s Injected compaction hint for step %d: %s",
        log_identifier,
        current_step,
        hint[:60] + "...",
    )

    return None
