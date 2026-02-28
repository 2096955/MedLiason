"""Shared utilities for the Medical Triage pipeline.

Provides a single SSE emission helper used by all triage tools, avoiding
boilerplate duplication and implementing cumulative emission (D2).

Also provides ``extract_json_from_text`` — a resilient JSON extractor for
parsing LLM responses that may contain markdown fencing, leading/trailing
prose, or stray balanced braces in chain-of-thought reasoning.

Cumulative emission: each SSE event carries the full accumulated pipeline
state.  The helper reads/writes ``tool_context.state["_triage_cumulative"]``
so that later stages automatically include earlier stage data.
"""

import json
import logging
import re

from google.adk.tools import ToolContext

# Compiled regex for detecting markdown-fenced JSON blocks.
# re.IGNORECASE handles ```JSON (uppercase).
# \s* handles trailing content on the opening fence line.
# \r?\n handles Windows line endings.
_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*\r?\n(.*?)\r?\n\s*```",
    re.DOTALL | re.IGNORECASE,
)

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

        from lifesci_common.triage_progress import TriageProgressData

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


def extract_json_from_text(text: str) -> dict | None:
    """Extract a JSON **dict** from an LLM response string.

    Three-stage fallback:

    1. Raw ``json.loads`` on the stripped text.
    2. Markdown fence extraction via ``_FENCE_PATTERN``.
    3. Loop-over-candidates balanced-brace scan — finds each ``{`` in the
       text, locates its matching ``}``, and attempts ``json.loads`` on the
       substring.  Advances past failed candidates so that prose containing
       balanced braces before the actual JSON object (e.g.
       ``"Findings: {uncertain} — {\"diagnosis\": ...}"``) is handled.

    Returns the parsed ``dict``, or ``None`` if no valid dict can be
    extracted.  Never raises.
    """
    if not text or not isinstance(text, str):
        return None

    stripped = text.strip()
    if not stripped:
        return None

    # Stage 1: raw parse
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
        # Valid JSON but wrong structural type (array, scalar, null).
        # Return None immediately — do not scan for inner brace pairs,
        # which risks silent partial extraction from multi-element arrays.
        return None
    except (json.JSONDecodeError, ValueError):
        pass

    # Stage 2: markdown fence extraction
    match = _FENCE_PATTERN.search(text)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # Stage 3: loop-over-candidates balanced-brace scan
    start = 0
    while start < len(stripped):
        idx = stripped.find("{", start)
        if idx == -1:
            break

        # Walk forward tracking brace depth to find the matching '}'
        depth = 0
        end = idx
        in_string = False
        escape_next = False
        for i in range(idx, len(stripped)):
            ch = stripped[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if depth != 0:
            # Unbalanced — no more valid candidates possible
            break

        candidate = stripped[idx : end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Advance past this candidate's closing brace
        start = end + 1

    return None
