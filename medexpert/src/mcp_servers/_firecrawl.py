"""Shared Firecrawl SDK wrapper with circuit breaker and concurrency control.

All MCP servers that use Firecrawl MUST use this module rather than
instantiating ``AsyncFirecrawlApp`` directly.  Provides:

* Lazy SDK import — server starts even if ``firecrawl-py`` is not installed.
* Per-host circuit breaker mirroring ``_http.py`` behaviour.
* ``asyncio.Semaphore`` to cap concurrent Firecrawl calls and avoid 429s
  when multiple model stacks share the same API key.
* Retry with exponential backoff (2 attempts, 1 s base delay).
"""

import asyncio
import logging
import os
import random
import time

log = logging.getLogger(__name__)

FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")

# Concurrency / resilience tunables
_MAX_CONCURRENT = 3
_CIRCUIT_FAILURE_THRESHOLD = 5
_CIRCUIT_RECOVERY_SECONDS = 60.0
_MAX_RETRIES = 2
_BASE_DELAY = 1.0

# Module-level singleton state
_semaphore: asyncio.Semaphore | None = None
_failure_count: int = 0
_circuit_open_until: float = 0.0
_client = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    """Return ``True`` if ``FIRECRAWL_API_KEY`` is set."""
    return bool(FIRECRAWL_API_KEY)


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    return _semaphore


def _get_client():
    """Lazy-initialise and return an ``AsyncFirecrawlApp`` instance."""
    global _client
    if _client is not None:
        return _client
    try:
        from firecrawl import AsyncFirecrawlApp  # type: ignore[import-untyped]
    except ImportError:
        log.error("firecrawl-py is not installed — run: pip install 'firecrawl-py>=1.5,<2.0'")
        return None
    _client = AsyncFirecrawlApp(api_key=FIRECRAWL_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Circuit breaker (simplified, single-host — all Firecrawl calls share it)
# ---------------------------------------------------------------------------


def _circuit_is_open() -> bool:
    if _failure_count < _CIRCUIT_FAILURE_THRESHOLD:
        return False
    if time.monotonic() >= _circuit_open_until:
        # Half-open: allow one probe
        return False
    return True


def _record_failure() -> None:
    global _failure_count, _circuit_open_until
    _failure_count += 1
    if _failure_count >= _CIRCUIT_FAILURE_THRESHOLD:
        _circuit_open_until = time.monotonic() + _CIRCUIT_RECOVERY_SECONDS
        log.warning(
            "Firecrawl circuit OPEN after %d failures (recovery in %.0fs)",
            _failure_count,
            _CIRCUIT_RECOVERY_SECONDS,
        )


def _record_success() -> None:
    global _failure_count, _circuit_open_until
    _failure_count = 0
    _circuit_open_until = 0.0


def reset_for_testing() -> None:
    """Reset all module-level state — for unit tests only."""
    global _failure_count, _circuit_open_until, _client, _semaphore
    _failure_count = 0
    _circuit_open_until = 0.0
    _client = None
    _semaphore = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class FirecrawlCircuitOpenError(Exception):
    """Raised when the Firecrawl circuit breaker is open."""


async def firecrawl_search(
    query: str,
    params: dict | None = None,
) -> list[dict]:
    """Search via Firecrawl with circuit breaker, semaphore, and retry.

    Returns a list of result dicts (may be empty on failure).
    Raises ``FirecrawlCircuitOpenError`` if the circuit is open.
    """
    if _circuit_is_open():
        raise FirecrawlCircuitOpenError("Firecrawl circuit breaker is open")

    client = _get_client()
    if client is None:
        return []

    sem = _get_semaphore()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        async with sem:
            try:
                result = await client.search(query, params=params or {})
                _record_success()
                if isinstance(result, list):
                    return result
                if isinstance(result, dict):
                    return result.get("data", [])
                return []
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "Firecrawl search failed (attempt %d/%d): %s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    exc,
                )
                _record_failure()
        # Sleep OUTSIDE semaphore to avoid holding the slot during backoff
        if last_exc is not None and attempt < _MAX_RETRIES:
            delay = _BASE_DELAY * (2**attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)

    log.error("Firecrawl search exhausted all retries: %s", last_exc)
    return []


async def firecrawl_scrape(
    url: str,
    params: dict | None = None,
) -> dict:
    """Scrape a URL via Firecrawl with circuit breaker, semaphore, and retry.

    Returns a dict with at least ``markdown``, ``title``, ``sourceURL`` keys
    on success, or an ``{"error": ...}`` dict on failure.
    Raises ``FirecrawlCircuitOpenError`` if the circuit is open.
    """
    if _circuit_is_open():
        raise FirecrawlCircuitOpenError("Firecrawl circuit breaker is open")

    client = _get_client()
    if client is None:
        return {"error": "Firecrawl SDK not available"}

    sem = _get_semaphore()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        async with sem:
            try:
                result = await client.scrape_url(url, params=params or {})
                _record_success()
                if isinstance(result, dict):
                    return result
                return {"error": "Unexpected response type from Firecrawl"}
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "Firecrawl scrape failed (attempt %d/%d): %s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    exc,
                )
                _record_failure()
        # Sleep OUTSIDE semaphore to avoid holding the slot during backoff
        if last_exc is not None and attempt < _MAX_RETRIES:
            delay = _BASE_DELAY * (2**attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)

    log.error("Firecrawl scrape exhausted all retries: %s", last_exc)
    return {"error": f"Firecrawl scrape failed after {_MAX_RETRIES + 1} attempts: {last_exc}"}
