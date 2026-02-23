"""Tests for lifesci_common.observability — structured logging utilities."""

import logging
import time
from unittest.mock import patch

import pytest

from lifesci_common.observability import (
    _get_cost_rates,
    log_step,
    log_tokens,
    step_timer,
)


# ── _get_cost_rates ──────────────────────────────────────────


def test_cost_rates_flash():
    rates = _get_cost_rates("openai/gemini-2.5-flash-001")
    assert rates["prompt"] > 0
    assert rates["completion"] > 0


def test_cost_rates_pro():
    rates = _get_cost_rates("openai/gemini-2.5-pro-001")
    assert rates["prompt"] > 0
    assert rates["completion"] > rates["prompt"]  # completion > prompt for pro


def test_cost_rates_unknown_model():
    rates = _get_cost_rates("unknown/model-v1")
    assert rates["prompt"] == 0.0
    assert rates["completion"] == 0.0


def test_cost_rates_case_insensitive():
    rates = _get_cost_rates("openai/Gemini-2.5-Flash-001")
    assert rates["prompt"] > 0


def test_cost_rates_gpt4o_mini_not_confused_with_gpt4o():
    """gpt-4o-mini should get its own (cheaper) rates, not gpt-4o rates."""
    mini_rates = _get_cost_rates("gpt-4o-mini")
    full_rates = _get_cost_rates("gpt-4o")
    assert mini_rates["prompt"] < full_rates["prompt"]
    assert mini_rates["completion"] < full_rates["completion"]


# ── log_step ──────────────────────────────────────────────────


def test_log_step_returns_record():
    record = log_step("sess-1", "DECOMPOSE", 150.5, sub_questions=3)
    assert record["event"] == "protocol_step"
    assert record["session_id"] == "sess-1"
    assert record["step"] == "DECOMPOSE"
    assert record["duration_ms"] == 150.5
    assert record["sub_questions"] == 3


def test_log_step_emits_log(caplog):
    with caplog.at_level(logging.INFO, logger="medexpert.observability"):
        log_step("sess-2", "COLLECT", 200.0)
    assert "COLLECT" in caplog.text


# ── log_tokens ────────────────────────────────────────────────


def test_log_tokens_returns_record():
    record = log_tokens("sess-1", "openai/gemini-2.5-flash-001", 100, 50)
    assert record["event"] == "llm_tokens"
    assert record["prompt_tokens"] == 100
    assert record["completion_tokens"] == 50
    assert record["estimated_cost_usd"] >= 0.0


def test_log_tokens_cost_estimation():
    record = log_tokens("sess-1", "openai/gemini-2.5-flash-001", 1000, 1000)
    # Cost should be non-zero for known models
    assert record["estimated_cost_usd"] > 0.0


def test_log_tokens_unknown_model_zero_cost():
    record = log_tokens("sess-1", "unknown/model", 1000, 1000)
    assert record["estimated_cost_usd"] == 0.0


# ── step_timer ────────────────────────────────────────────────


def test_step_timer_measures_duration():
    records = []
    with patch("lifesci_common.observability.log_step", side_effect=lambda *a, **kw: records.append({"duration_ms": a[2]})):
        with step_timer("sess-1", "REFLECT"):
            time.sleep(0.01)  # 10ms

    assert len(records) == 1
    assert records[0]["duration_ms"] >= 5.0  # At least ~5ms (generous margin)


def test_step_timer_logs_on_exception():
    records = []
    with patch("lifesci_common.observability.log_step", side_effect=lambda *a, **kw: records.append({"step": a[1]})):
        with pytest.raises(ValueError):
            with step_timer("sess-1", "FAIL_STEP"):
                raise ValueError("boom")

    # Should still log even when exception occurs
    assert len(records) == 1
    assert records[0]["step"] == "FAIL_STEP"


def test_step_timer_yields_start_time():
    with step_timer("sess-1", "TEST") as start:
        assert isinstance(start, float)
        assert start > 0
