"""Specialist response validator — validates structure of peer agent responses.

Registered as an after_tool_callback on the orchestrator. Checks that
specialist responses contain required fields and respect size constraints.
Logs warnings but does NOT reject responses — graceful degradation.

Only activates for tool names matching 'peer_*' (specialist delegation tools).
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Required fields in a specialist response
_REQUIRED_FIELDS = {"execution_status", "findings", "sources"}

# Warn if response exceeds this character count (2x the 2000-char soft limit)
_MAX_RESPONSE_CHARS = 4000

# Valid execution_status values
_VALID_STATUSES = {"success", "partial", "failed"}

# Verifier/Reviser tools have a different response schema — exclude them
_EXCLUDED_SUBSTRINGS = ("Verifier", "Reviser")


def _parse_response(tool_response: Any) -> Optional[Dict]:
    """Try to extract a dict from tool_response (string or dict)."""
    if isinstance(tool_response, dict):
        return tool_response
    if isinstance(tool_response, str):
        try:
            parsed = json.loads(tool_response)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def validate_specialist_response(
    tool_name: str,
    tool_response: Any,
) -> List[str]:
    """Validate a specialist response and return a list of warnings.

    Returns an empty list if the response is valid. Does not raise.
    """
    warnings: List[str] = []

    # Only validate peer_* specialist responses (not Verifier/Reviser)
    if not tool_name.startswith("peer_"):
        return warnings
    if any(sub in tool_name for sub in _EXCLUDED_SUBSTRINGS):
        return warnings

    parsed = _parse_response(tool_response)
    if parsed is None:
        warnings.append(f"{tool_name}: response is not valid JSON")
        return warnings

    # Check required fields
    missing = _REQUIRED_FIELDS - set(parsed.keys())
    if missing:
        warnings.append(
            f"{tool_name}: missing required fields: {', '.join(sorted(missing))}"
        )

    # Validate execution_status value
    status = parsed.get("execution_status")
    if status and status not in _VALID_STATUSES:
        warnings.append(
            f"{tool_name}: invalid execution_status '{status}' "
            f"(expected one of: {', '.join(sorted(_VALID_STATUSES))})"
        )

    # Check response size (always use normalized JSON for consistency)
    response_str = json.dumps(parsed)
    if len(response_str) > _MAX_RESPONSE_CHARS:
        warnings.append(
            f"{tool_name}: response exceeds {_MAX_RESPONSE_CHARS} chars "
            f"({len(response_str)} chars)"
        )

    # Validate sources array if status is "success"
    if status == "success":
        sources = parsed.get("sources", [])
        if not sources:
            warnings.append(
                f"{tool_name}: execution_status is 'success' but sources array is empty"
            )

    return warnings


async def specialist_response_validator_callback(
    tool,
    args: Dict,
    tool_context,
    tool_response: Any,
    **kwargs,
) -> Any:
    """After-tool callback that validates specialist responses.

    Logs warnings for invalid responses but always returns the original
    response unchanged (graceful degradation).
    """
    tool_name = getattr(tool, "name", "") or ""

    if not tool_name.startswith("peer_"):
        return tool_response

    warnings = validate_specialist_response(tool_name, tool_response)
    for w in warnings:
        logger.warning("[specialist_validator] %s", w)

    # Attach warnings to tool_context state for downstream observability
    if warnings and hasattr(tool_context, "state") and tool_context.state is not None:
        existing = tool_context.state.get("_validation_warnings", [])
        tool_context.state["_validation_warnings"] = existing + warnings

    return tool_response
