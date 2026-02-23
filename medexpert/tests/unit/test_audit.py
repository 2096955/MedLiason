"""Tests for the audit logging module."""

import json
from pathlib import Path

import pytest

from lifesci_common.audit import audit_event, init_audit, _compute_hash


@pytest.fixture(autouse=True)
def audit_to_tmp(tmp_path):
    """Redirect audit log to a temp file for every test."""
    path = str(tmp_path / "test-audit.jsonl")
    init_audit(path)
    return path


def _read_events(path: str) -> list[dict]:
    lines = Path(path).read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


def test_audit_event_writes_jsonl(audit_to_tmp):
    audit_event(session_id="s1", action="memory_store", resource="key1")
    events = _read_events(audit_to_tmp)
    assert len(events) == 1
    assert events[0]["action"] == "memory_store"
    assert events[0]["session_id"] == "s1"
    assert "ts" in events[0]
    # Timestamp should be ISO 8601 format
    assert "T" in events[0]["ts"]  # ISO 8601 contains 'T' separator


def test_audit_event_multiple_lines(audit_to_tmp):
    audit_event(session_id="s1", action="llm_call", resource="gemini")
    audit_event(session_id="s1", action="report_generate", resource="full_synthesis")
    events = _read_events(audit_to_tmp)
    assert len(events) == 2


def test_audit_event_returns_record():
    record = audit_event(session_id="s1", action="test", outcome="success")
    assert record["action"] == "test"
    assert record["outcome"] == "success"


def test_input_hash_computed_correctly():
    h = _compute_hash("hello world")
    assert len(h) == 64  # SHA-256 hex
    assert h == _compute_hash("hello world")  # deterministic
    assert h != _compute_hash("different text")


def test_audit_event_with_metadata(audit_to_tmp):
    audit_event(
        session_id="s1",
        action="llm_call",
        resource="gemini-2.5-pro",
        metadata={"tokens": 500, "model": "gemini"},
    )
    events = _read_events(audit_to_tmp)
    assert events[0]["meta"]["tokens"] == 500


def test_audit_event_with_input_hash(audit_to_tmp):
    h = _compute_hash("patient query")
    audit_event(session_id="s1", action="source_publish", input_hash=h)
    events = _read_events(audit_to_tmp)
    assert events[0]["input_hash"] == h


def test_audit_event_denied_outcome(audit_to_tmp):
    audit_event(session_id="s1", action="rate_limit_deny", outcome="denied")
    events = _read_events(audit_to_tmp)
    assert events[0]["outcome"] == "denied"


def test_audit_event_all_fields(audit_to_tmp):
    record = audit_event(
        session_id="s1",
        user_id="u1",
        agent_id="OrchestratorAgent",
        action="critical_withhold",
        resource="report",
        outcome="withheld",
        latency_ms=1234.5,
        input_hash="abc123",
        metadata={"reason": "verification_failed"},
    )
    assert record["user_id"] == "u1"
    assert record["agent_id"] == "OrchestratorAgent"
    assert record["latency_ms"] == 1234.5
