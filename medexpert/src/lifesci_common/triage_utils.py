"""Shared utilities for the Medical Triage pipeline.

Provides a single SSE emission helper used by all triage tools, avoiding
boilerplate duplication and implementing cumulative emission (D2).

Cumulative emission: each SSE event carries the full accumulated pipeline
state.  The helper reads/writes ``tool_context.state["_triage_cumulative"]``
so that later stages automatically include earlier stage data.
"""

import logging

from google.adk.tools import ToolContext

log = logging.getLogger(__name__)

_TRIAGE_CUMULATIVE_KEY = "_triage_cumulative"

# Fields accepted by TriageProgressData — anything else is silently dropped.
_KNOWN_FIELDS = frozenset({
    "stage",
    "stage_name",
    "total_stages",
    "detail",
    "specialist_verdicts",
    "consensus",
    "evaluation",
    "nba",
    "emergency_override",
    "error",
})


async def emit_triage_progress(
    tool_context: ToolContext,
    *,
    stage: int,
    stage_name: str,
    detail: str,
    log_identifier: str = "[Triage:Progress]",
    **kwargs,
) -> None:
    """Emit cumulative ``TriageProgressData`` via SSE.

    Reads accumulated state from ``tool_context.state``, merges *stage*,
    *stage_name*, *detail* and any extra *kwargs* (e.g. ``specialist_verdicts``,
    ``consensus``, ``evaluation``, ``nba``, ``emergency_override``), writes
    back, and publishes the full payload as an SSE signal.

    The frontend replaces ``triageProgress`` wholesale on each event, so
    every emission must include the complete pipeline state up to this point.
    """
    # 1. Read accumulated state from prior tools in this agent session
    cumulative: dict = dict(tool_context.state.get(_TRIAGE_CUMULATIVE_KEY) or {})

    # 2. Merge current stage data
    cumulative["stage"] = stage
    cumulative["stage_name"] = stage_name
    cumulative["detail"] = detail
    for key, value in kwargs.items():
        if key in _KNOWN_FIELDS and value is not None:
            cumulative[key] = value

    # 3. Write back for subsequent tools to read
    tool_context.state[_TRIAGE_CUMULATIVE_KEY] = cumulative

    # 4. Emit via host component
    try:
        a2a_context = tool_context.state.get("a2a_context")
        if not a2a_context:
            log.warning(
                "%s a2a_context not found in tool_context.state — "
                "SSE emission skipped (triage side panel will not update)",
                log_identifier,
            )
            return

        inv = getattr(tool_context, "_invocation_context", None)
        if not inv:
            log.warning(
                "%s _invocation_context not available — SSE emission skipped",
                log_identifier,
            )
            return

        agent = getattr(inv, "agent", None)
        host = getattr(agent, "host_component", None) if agent else None
        if not host:
            log.warning(
                "%s host_component not available — SSE emission skipped",
                log_identifier,
            )
            return

        from solace_agent_mesh.common.data_parts import TriageProgressData

        # Build from known fields only
        progress_kwargs = {k: v for k, v in cumulative.items() if k in _KNOWN_FIELDS}
        progress = TriageProgressData(**progress_kwargs)
        host.publish_data_signal_from_thread(
            a2a_context=a2a_context,
            signal_data=progress,
            skip_buffer_flush=False,
            log_identifier=log_identifier,
        )
    except Exception as exc:
        log.debug("%s Could not emit progress: %s", log_identifier, exc)
