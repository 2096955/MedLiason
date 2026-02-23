"""Tests for the rate limiter and LLM budget tracker."""

import pytest

from lifesci_common.rate_limiter import (
    LLMBudgetTracker,
    check_rate_limit,
    reset_for_testing,
)


@pytest.fixture(autouse=True)
def clean_state():
    reset_for_testing()
    yield
    reset_for_testing()


# ── token bucket ─────────────────────────────────────────────


def test_allows_within_limit():
    for _ in range(5):
        assert check_rate_limit("user1", "api", max_requests=5, window_seconds=60)


def test_denies_over_limit():
    for _ in range(3):
        check_rate_limit("user1", "api", max_requests=3, window_seconds=60)
    assert check_rate_limit("user1", "api", max_requests=3, window_seconds=60) is False


def test_separate_users():
    for _ in range(3):
        check_rate_limit("user1", "api", max_requests=3, window_seconds=60)
    assert check_rate_limit("user2", "api", max_requests=3, window_seconds=60) is True


def test_separate_resources():
    for _ in range(3):
        check_rate_limit("user1", "llm", max_requests=3, window_seconds=60)
    assert check_rate_limit("user1", "mcp", max_requests=3, window_seconds=60) is True


# ── LLM budget tracker ──────────────────────────────────────


def test_budget_tracker_records_usage():
    result = LLMBudgetTracker.record_usage(
        session_id="s1",
        prompt_tokens=1000,
        completion_tokens=500,
        model="openai/gemini-2.5-flash-001",
    )
    assert result["total_prompt"] == 1000
    assert result["total_completion"] == 500
    assert result["total_cost_usd"] > 0
    assert result["budget_exceeded"] is False


def test_budget_tracker_cumulative():
    LLMBudgetTracker.record_usage("s1", 1000, 500, "openai/gemini-2.5-flash-001")
    result = LLMBudgetTracker.record_usage("s1", 2000, 1000, "openai/gemini-2.5-flash-001")
    assert result["total_prompt"] == 3000
    assert result["total_completion"] == 1500


def test_budget_tracker_exceeds_threshold(monkeypatch):
    monkeypatch.setenv("MEDEXPERT_LLM_BUDGET_USD", "0.001")
    LLMBudgetTracker.reset_for_testing()
    result = LLMBudgetTracker.record_usage(
        "s1", 100_000, 100_000, "openai/gemini-2.5-pro-001"
    )
    assert result["budget_exceeded"] is True


def test_budget_tracker_separate_sessions():
    LLMBudgetTracker.record_usage("s1", 1000, 500, "openai/gemini-2.5-flash-001")
    result = LLMBudgetTracker.get_session_budget("s2")
    assert result["total_prompt"] == 0
    assert result["budget_exceeded"] is False


def test_get_session_budget_unknown_session():
    result = LLMBudgetTracker.get_session_budget("nonexistent")
    assert result["total_prompt"] == 0
    assert result["budget_exceeded"] is False
