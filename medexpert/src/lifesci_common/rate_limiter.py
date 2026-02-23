"""Token-bucket rate limiter and LLM budget tracker.

In-memory dict backend for local/dev use.  Swap to Redis
INCR/EXPIRE for multi-replica production deployments.
"""

import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger("medexpert.rate_limiter")


# ---------------------------------------------------------------------------
# Token-bucket rate limiter
# ---------------------------------------------------------------------------

@dataclass
class _Bucket:
    """Sliding-window token bucket."""

    tokens: float
    max_tokens: float
    last_refill: float
    refill_rate: float  # tokens per second


_buckets: dict[str, _Bucket] = {}


def check_rate_limit(
    user_id: str,
    resource: str,
    max_requests: int,
    window_seconds: int,
) -> bool:
    """Return ``True`` if the request is ALLOWED, ``False`` if DENIED."""
    key = f"{user_id}:{resource}"
    now = time.monotonic()

    bucket = _buckets.get(key)
    if bucket is None:
        _buckets[key] = _Bucket(
            tokens=max_requests - 1,
            max_tokens=max_requests,
            last_refill=now,
            refill_rate=max_requests / window_seconds,
        )
        return True

    # Refill tokens based on elapsed time
    elapsed = now - bucket.last_refill
    bucket.tokens = min(
        bucket.max_tokens,
        bucket.tokens + elapsed * bucket.refill_rate,
    )
    bucket.last_refill = now

    if bucket.tokens >= 1.0:
        bucket.tokens -= 1.0
        return True

    log.warning("Rate limit exceeded for user=%s resource=%s", user_id, resource)
    return False


# ---------------------------------------------------------------------------
# LLM budget tracker
# ---------------------------------------------------------------------------


class LLMBudgetTracker:
    """Track cumulative LLM token spend per session.

    Hard-stops if cumulative estimated cost exceeds the threshold set
    by ``MEDEXPERT_LLM_BUDGET_USD`` (default ``$5.00``).
    """

    _sessions: dict[str, dict] = {}

    @classmethod
    def reset_for_testing(cls) -> None:
        """Clear all session budget data (for tests)."""
        cls._sessions.clear()

    @classmethod
    def record_usage(
        cls,
        session_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
    ) -> dict:
        """Record token usage.  Returns current session totals."""
        from lifesci_common.observability import _get_cost_rates

        rates = _get_cost_rates(model)
        cost = (
            prompt_tokens * rates["prompt"]
            + completion_tokens * rates["completion"]
        ) / 1000.0

        entry = cls._sessions.setdefault(
            session_id,
            {"total_prompt": 0, "total_completion": 0, "total_cost_usd": 0.0},
        )
        entry["total_prompt"] += prompt_tokens
        entry["total_completion"] += completion_tokens
        entry["total_cost_usd"] += cost

        threshold = float(os.environ.get("MEDEXPERT_LLM_BUDGET_USD", "5.0"))
        entry["budget_exceeded"] = entry["total_cost_usd"] >= threshold
        if entry["budget_exceeded"]:
            log.warning(
                "LLM budget exceeded for session %s: $%.4f >= $%.2f",
                session_id,
                entry["total_cost_usd"],
                threshold,
            )
        return dict(entry)

    @classmethod
    def get_session_budget(cls, session_id: str) -> dict:
        """Return current budget state for a session."""
        return dict(
            cls._sessions.get(
                session_id,
                {
                    "total_prompt": 0,
                    "total_completion": 0,
                    "total_cost_usd": 0.0,
                    "budget_exceeded": False,
                },
            )
        )


def reset_for_testing() -> None:
    """Clear all rate limiter and budget tracker state (for tests)."""
    _buckets.clear()
    LLMBudgetTracker._sessions.clear()
