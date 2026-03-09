"""HTTP retry utility with exponential backoff, jitter, and circuit breaker.

Provides resilient_get() and resilient_post() for all MCP servers to use
against external APIs.  Includes a per-host circuit breaker that opens
after repeated failures and probes after a recovery timeout.
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

import httpx
from fastmcp.exceptions import ToolError

log = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _CircuitEntry:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    success_count: int = 0


class CircuitBreaker:
    """Per-host circuit breaker.

    CLOSED --[N failures]--> OPEN --[timeout]--> HALF_OPEN
    HALF_OPEN --[success]--> CLOSED
    HALF_OPEN --[failure]--> OPEN
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._circuits: dict[str, _CircuitEntry] = {}

    def reset_for_testing(self) -> None:
        self._circuits.clear()

    def _get_host(self, url: str) -> str:
        try:
            return urlparse(url).netloc or url
        except Exception:
            return url

    def allow_request(self, url: str) -> bool:
        """Return ``True`` if a request to *url* is allowed."""
        host = self._get_host(url)
        entry = self._circuits.get(host)
        if entry is None:
            return True

        if entry.state == CircuitState.CLOSED:
            return True

        if entry.state == CircuitState.OPEN:
            elapsed = time.monotonic() - entry.last_failure_time
            if elapsed >= self.recovery_timeout:
                entry.state = CircuitState.HALF_OPEN
                entry.success_count = 0
                log.info("Circuit HALF_OPEN for host=%s after %.1fs", host, elapsed)
                return True
            return False

        # HALF_OPEN: allow limited probe calls
        return entry.success_count < self.half_open_max_calls

    def record_success(self, url: str) -> None:
        host = self._get_host(url)
        entry = self._circuits.get(host)
        if entry is None:
            return

        if entry.state == CircuitState.HALF_OPEN:
            entry.success_count += 1
            if entry.success_count >= self.half_open_max_calls:
                entry.state = CircuitState.CLOSED
                entry.failure_count = 0
                log.info("Circuit CLOSED for host=%s (probe succeeded)", host)
        elif entry.state == CircuitState.CLOSED:
            entry.failure_count = 0

    def record_failure(self, url: str) -> None:
        host = self._get_host(url)
        entry = self._circuits.setdefault(host, _CircuitEntry())

        if entry.state == CircuitState.HALF_OPEN:
            entry.state = CircuitState.OPEN
            entry.last_failure_time = time.monotonic()
            log.warning("Circuit OPEN for host=%s (probe failed)", host)
            return

        entry.failure_count += 1
        entry.last_failure_time = time.monotonic()

        if entry.failure_count >= self.failure_threshold:
            entry.state = CircuitState.OPEN
            log.warning(
                "Circuit OPEN for host=%s after %d failures",
                host,
                entry.failure_count,
            )


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is OPEN for a given host."""

    def __init__(self, url: str):
        self.url = url
        self.error_category = "circuit_open"
        self.is_retryable = True
        self.retry_after_seconds = _circuit_breaker.recovery_timeout
        super().__init__(f"Circuit breaker OPEN for {url}")


# Module-level singleton
_circuit_breaker = CircuitBreaker()


# ---------------------------------------------------------------------------
# Validation exception (defined before structured_error_response)
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    """Raised for invalid input (HTTP 400/422 equivalent)."""

    def __init__(self, message: str):
        self.error_category = "validation_error"
        self.is_retryable = False
        super().__init__(message)


# ---------------------------------------------------------------------------
# Status-to-category mapping
# ---------------------------------------------------------------------------


def _status_to_category(status_code: int) -> str:
    """Map an HTTP status code to a human-readable error category."""
    if status_code == 429:
        return "rate_limited"
    if status_code in (500, 502, 503, 504):
        return "service_unavailable"
    if status_code == 404:
        return "not_found"
    if status_code in (400, 422):
        return "validation_error"
    if status_code in (401, 403):
        return "auth_error"
    if status_code == 0:
        return "network_error"
    return "api_error"


# ---------------------------------------------------------------------------
# Structured error response builder
# ---------------------------------------------------------------------------


def structured_error_response(
    exc: Exception,
    server_name: str,
    tool_name: str,
) -> dict:
    """Build a structured error dict from an exception.

    Returns a dict with at least: ``success``, ``error``, ``error_category``,
    ``is_retryable``, ``server``, ``tool``.  Additional keys depend on the
    exception type (e.g. ``retry_after_seconds``, ``last_status``, ``attempts``).
    """
    base = {
        "success": False,
        "server": server_name,
        "tool": tool_name,
    }

    if isinstance(exc, CircuitOpenError):
        base.update({
            "error": str(exc),
            "error_category": exc.error_category,
            "is_retryable": exc.is_retryable,
            "retry_after_seconds": exc.retry_after_seconds,
        })
    elif isinstance(exc, RetryExhaustedError):
        base.update({
            "error": str(exc),
            "error_category": exc.error_category,
            "is_retryable": exc.is_retryable,
            "last_status": exc.last_status,
            "attempts": exc.attempts,
        })
    elif isinstance(exc, ValidationError):
        base.update({
            "error": str(exc),
            "error_category": exc.error_category,
            "is_retryable": exc.is_retryable,
        })
    elif isinstance(exc, httpx.HTTPError):
        base.update({
            "error": str(exc),
            "error_category": "network_error",
            "is_retryable": True,
        })
    else:
        base.update({
            "error": str(exc),
            "error_category": "api_error",
            "is_retryable": False,
        })

    return base


# Categories where the MCP isError flag should be set — these are
# unambiguously server-side failures that the LLM should not attempt
# to interpret as partial data.
_FATAL_ERROR_CATEGORIES = frozenset({
    "circuit_open",
    "rate_limited",
    "service_unavailable",
    "network_error",
    "auth_error",
})


def raise_or_return_error(
    exc: Exception,
    server_name: str,
    tool_name: str,
    **extra_fields: object,
) -> dict:
    """Build a structured error and raise ``ToolError`` for fatal categories.

    For fatal errors (circuit_open, rate_limited, service_unavailable,
    network_error, auth_error), raises ``fastmcp.exceptions.ToolError``
    with the structured JSON as the message.  This sets ``isError=True``
    on the MCP ``CallToolResult`` so the LLM immediately knows the tool
    call failed — it does not have to parse the JSON to detect failure.

    For non-fatal errors (not_found, validation_error, api_error), returns
    the structured dict as a normal tool result.  These represent expected
    boundary conditions the LLM should reason about (e.g. "no results
    found" or "invalid input").

    *extra_fields* are merged into the dict before raising/returning,
    allowing callers to include domain-specific keys like ``"results": []``.
    """
    err = structured_error_response(exc, server_name, tool_name)
    if extra_fields:
        err.update(extra_fields)

    if err.get("error_category") in _FATAL_ERROR_CATEGORIES:
        raise ToolError(json.dumps(err))

    return err


# ---------------------------------------------------------------------------
# Retry exceptions
# ---------------------------------------------------------------------------


class RetryExhaustedError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, url: str, last_status: int, attempts: int):
        self.url = url
        self.last_status = last_status
        self.attempts = attempts
        self.error_category = _status_to_category(last_status)
        self.is_retryable = last_status in RETRYABLE_STATUS_CODES or last_status == 0
        super().__init__(
            f"All {attempts} retries exhausted for {url} (last status: {last_status})"
        )


# ---------------------------------------------------------------------------
# Resilient HTTP functions
# ---------------------------------------------------------------------------


async def resilient_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.Response:
    """GET request with exponential backoff, jitter, and circuit breaker.

    Retries on 429, 500, 502, 503, 504 status codes.
    Delay formula: base_delay * 2^attempt + random(0, 0.5)
    """
    if not _circuit_breaker.allow_request(url):
        raise CircuitOpenError(url)

    last_response = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(url, params=params, headers=headers)
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    _circuit_breaker.record_success(url)
                    return response
                last_response = response
                log.warning(
                    "Retryable status %d from GET %s (attempt %d/%d)",
                    response.status_code,
                    url,
                    attempt + 1,
                    max_retries + 1,
                )
            except httpx.HTTPError as exc:
                log.warning(
                    "HTTP error on GET %s (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                if attempt == max_retries:
                    _circuit_breaker.record_failure(url)
                    raise

            if attempt < max_retries:
                delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
                log.debug("Backing off %.2fs before retry", delay)
                await asyncio.sleep(delay)

    _circuit_breaker.record_failure(url)
    if last_response is not None:
        raise RetryExhaustedError(url, last_response.status_code, max_retries + 1)
    raise RetryExhaustedError(url, 0, max_retries + 1)


async def resilient_post(
    url: str,
    json: dict | None = None,
    headers: dict | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.Response:
    """POST request with exponential backoff, jitter, and circuit breaker.

    Retries on 429, 500, 502, 503, 504 status codes.
    Delay formula: base_delay * 2^attempt + random(0, 0.5)
    """
    if not _circuit_breaker.allow_request(url):
        raise CircuitOpenError(url)

    last_response = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(url, json=json, headers=headers)
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    _circuit_breaker.record_success(url)
                    return response
                last_response = response
                log.warning(
                    "Retryable status %d from POST %s (attempt %d/%d)",
                    response.status_code,
                    url,
                    attempt + 1,
                    max_retries + 1,
                )
            except httpx.HTTPError as exc:
                log.warning(
                    "HTTP error on POST %s (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                if attempt == max_retries:
                    _circuit_breaker.record_failure(url)
                    raise

            if attempt < max_retries:
                delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
                log.debug("Backing off %.2fs before retry", delay)
                await asyncio.sleep(delay)

    _circuit_breaker.record_failure(url)
    if last_response is not None:
        raise RetryExhaustedError(url, last_response.status_code, max_retries + 1)
    raise RetryExhaustedError(url, 0, max_retries + 1)
