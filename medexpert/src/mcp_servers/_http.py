"""HTTP retry utility with exponential backoff, jitter, and circuit breaker.

Provides resilient_get() and resilient_post() for all MCP servers to use
against external APIs.  Includes a per-host circuit breaker that opens
after repeated failures and probes after a recovery timeout.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

import httpx

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
        super().__init__(f"Circuit breaker OPEN for {url}")


# Module-level singleton
_circuit_breaker = CircuitBreaker()


# ---------------------------------------------------------------------------
# Retry exceptions
# ---------------------------------------------------------------------------


class RetryExhaustedError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, url: str, last_status: int, attempts: int):
        self.url = url
        self.last_status = last_status
        self.attempts = attempts
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
