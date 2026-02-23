"""Tests for the HTTP circuit breaker."""

import time

import pytest

from mcp_servers._http import CircuitBreaker, CircuitOpenError, CircuitState


@pytest.fixture
def cb():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
    yield breaker
    breaker.reset_for_testing()


# ── CLOSED state ─────────────────────────────────────────────


def test_initial_state_allows_requests(cb):
    assert cb.allow_request("https://api.example.com/data") is True


def test_stays_closed_below_threshold(cb):
    url = "https://api.example.com/data"
    cb.record_failure(url)
    cb.record_failure(url)
    assert cb.allow_request(url) is True


# ── CLOSED -> OPEN transition ────────────────────────────────


def test_opens_after_threshold_failures(cb):
    url = "https://api.example.com/data"
    for _ in range(3):
        cb.record_failure(url)
    assert cb.allow_request(url) is False


def test_open_rejects_requests(cb):
    url = "https://api.example.com/data"
    for _ in range(3):
        cb.record_failure(url)
    assert cb.allow_request(url) is False
    assert cb.allow_request(url) is False


# ── OPEN -> HALF_OPEN transition ─────────────────────────────


def test_transitions_to_half_open_after_timeout(cb):
    url = "https://api.example.com/data"
    for _ in range(3):
        cb.record_failure(url)
    assert cb.allow_request(url) is False

    host = cb._get_host(url)
    cb._circuits[host].last_failure_time = time.monotonic() - 2.0
    assert cb.allow_request(url) is True  # now HALF_OPEN


# ── HALF_OPEN -> CLOSED (probe success) ─────────────────────


def test_half_open_success_closes_circuit(cb):
    url = "https://api.example.com/data"
    for _ in range(3):
        cb.record_failure(url)

    host = cb._get_host(url)
    cb._circuits[host].last_failure_time = time.monotonic() - 2.0
    cb.allow_request(url)

    cb.record_success(url)
    assert cb._circuits[host].state == CircuitState.CLOSED
    assert cb.allow_request(url) is True


# ── HALF_OPEN -> OPEN (probe failure) ────────────────────────


def test_half_open_failure_reopens_circuit(cb):
    url = "https://api.example.com/data"
    for _ in range(3):
        cb.record_failure(url)

    host = cb._get_host(url)
    cb._circuits[host].last_failure_time = time.monotonic() - 2.0
    cb.allow_request(url)

    cb.record_failure(url)
    assert cb._circuits[host].state == CircuitState.OPEN
    assert cb.allow_request(url) is False


# ── per-host isolation ───────────────────────────────────────


def test_circuits_are_per_host(cb):
    url_a = "https://api-a.example.com/data"
    url_b = "https://api-b.example.com/data"

    for _ in range(3):
        cb.record_failure(url_a)

    assert cb.allow_request(url_a) is False
    assert cb.allow_request(url_b) is True


# ── success resets failure count ─────────────────────────────


def test_success_resets_failure_count(cb):
    url = "https://api.example.com/data"
    cb.record_failure(url)
    cb.record_failure(url)
    cb.record_success(url)
    cb.record_failure(url)
    cb.record_failure(url)
    assert cb.allow_request(url) is True


# ── CircuitOpenError ─────────────────────────────────────────


def test_circuit_open_error_message():
    err = CircuitOpenError("https://api.example.com")
    assert "OPEN" in str(err)
    assert "api.example.com" in str(err)


# ── integration with resilient_get ───────────────────────────


async def test_resilient_get_raises_circuit_open():
    """resilient_get should raise CircuitOpenError when circuit is open."""
    from mcp_servers._http import _circuit_breaker, resilient_get

    url = "https://api.test-cb-integration.example.com/data"
    # Force the circuit open
    for _ in range(5):  # default threshold is 5
        _circuit_breaker.record_failure(url)

    with pytest.raises(CircuitOpenError):
        await resilient_get(url)

    # Clean up
    _circuit_breaker.reset_for_testing()
