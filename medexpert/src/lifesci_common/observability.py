"""Observability utilities for MedExpert — structured logging for protocol
steps and LLM token usage.

Uses stdlib ``logging`` only (no new dependencies).  All functions emit
structured log records that can be consumed by JSON log formatters or
plain-text handlers alike.
"""

import logging
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("medexpert.observability")

# ---------------------------------------------------------------------------
# Cost rates (USD per 1 K tokens) — prefix-matched against model strings
# ---------------------------------------------------------------------------

# Ordered longest-prefix-first to avoid "gpt-4o" matching "gpt-4o-mini".
_COST_RATES: list[tuple[str, dict[str, float]]] = [
    ("gpt-4o-mini", {"prompt": 0.00015, "completion": 0.0006}),
    ("gpt-4o", {"prompt": 0.0025, "completion": 0.01}),
    ("gemini-2.5-pro", {"prompt": 0.00125, "completion": 0.005}),
    ("gemini-2.5-flash", {"prompt": 0.000075, "completion": 0.0003}),
    ("gemini-2.0-flash", {"prompt": 0.0001, "completion": 0.0004}),
    ("gemini-1.5-pro", {"prompt": 0.00125, "completion": 0.005}),
    ("gemini-1.5-flash", {"prompt": 0.000075, "completion": 0.0003}),
]


def _get_cost_rates(model: str) -> dict[str, float]:
    """Return ``{prompt, completion}`` cost-per-1K-token rates for *model*.

    Uses prefix matching: ``"openai/gemini-2.5-flash-001"`` matches the
    ``"gemini-2.5-flash"`` key.  Returns zeros when no match is found.
    """
    normalised = model.lower()
    for prefix, rates in _COST_RATES:
        if prefix in normalised:
            return rates
    return {"prompt": 0.0, "completion": 0.0}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def log_step(
    session_id: str,
    step: str,
    duration_ms: float,
    **extra: Any,
) -> dict:
    """Log a protocol step with timing information.

    Returns the structured record dict for downstream consumption.
    """
    record = {
        "event": "protocol_step",
        "session_id": session_id,
        "step": step,
        "duration_ms": round(duration_ms, 1),
        **extra,
    }
    logger.info("step=%s session=%s duration_ms=%.1f", step, session_id, duration_ms, extra={"structured": record})
    return record


def log_tokens(
    session_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict:
    """Log LLM token usage with estimated cost.

    Returns the structured record dict.
    """
    rates = _get_cost_rates(model)
    cost = (prompt_tokens * rates["prompt"] + completion_tokens * rates["completion"]) / 1000.0
    record = {
        "event": "llm_tokens",
        "session_id": session_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": round(cost, 6),
    }
    logger.info(
        "tokens model=%s prompt=%d completion=%d cost=$%.6f session=%s",
        model,
        prompt_tokens,
        completion_tokens,
        cost,
        session_id,
        extra={"structured": record},
    )
    return record


@contextmanager
def step_timer(session_id: str, step: str, **extra: Any):
    """Context manager that logs a protocol step with its wall-clock duration.

    Usage::

        with step_timer("sess-1", "DECOMPOSE"):
            do_work()

    Yields the start time so callers can read it if needed.
    """
    start = time.monotonic()
    try:
        yield start
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        log_step(session_id, step, elapsed_ms, **extra)
