"""Tests for PhiRedactorTool — PII/PHI detection and redaction."""

from unittest.mock import MagicMock

import pytest

from lifesci_tools.phi_redactor import (
    PhiRedactorTool,
    _detect_with_regex,
    _redact_text,
)


@pytest.fixture
def tool():
    return PhiRedactorTool()


@pytest.fixture
def ctx():
    return MagicMock()


# ── regex detection: SSN ──────────────────────────────────────


def test_detect_ssn():
    entities = _detect_with_regex("Patient SSN is 123-45-6789.", ["SSN"])
    assert len(entities) == 1
    assert entities[0]["type"] == "SSN"
    assert entities[0]["value"] == "123-45-6789"


def test_detect_ssn_no_dashes():
    entities = _detect_with_regex("SSN: 123456789", ["SSN"])
    assert len(entities) >= 1


# ── regex detection: PHONE ────────────────────────────────────


def test_detect_phone():
    entities = _detect_with_regex("Call (555) 123-4567 for info.", ["PHONE"])
    assert len(entities) == 1
    assert entities[0]["type"] == "PHONE"


def test_detect_phone_with_country_code():
    entities = _detect_with_regex("Phone: +1-555-123-4567", ["PHONE"])
    assert len(entities) >= 1


# ── regex detection: EMAIL ────────────────────────────────────


def test_detect_email():
    entities = _detect_with_regex("Contact john.doe@hospital.org please.", ["EMAIL"])
    assert len(entities) == 1
    assert entities[0]["type"] == "EMAIL"
    assert entities[0]["value"] == "john.doe@hospital.org"


# ── regex detection: MRN ──────────────────────────────────────


def test_detect_mrn():
    entities = _detect_with_regex("MRN: 12345678", ["MRN"])
    assert len(entities) == 1
    assert entities[0]["type"] == "MRN"


def test_detect_mrn_with_hash():
    entities = _detect_with_regex("Medical Record #987654", ["MRN"])
    assert len(entities) >= 1


# ── regex detection: DOB ──────────────────────────────────────


def test_detect_dob():
    entities = _detect_with_regex("DOB: 01/15/1990", ["DOB"])
    assert len(entities) == 1
    assert entities[0]["type"] == "DOB"


def test_detect_dob_spelled():
    entities = _detect_with_regex("Date of Birth: 3/25/1985", ["DOB"])
    assert len(entities) >= 1


# ── regex detection: PERSON ───────────────────────────────────


def test_detect_person_name():
    entities = _detect_with_regex("Dr. John Smith was the attending.", ["PERSON"])
    assert len(entities) >= 1
    assert entities[0]["type"] == "PERSON"


def test_detect_patient_name():
    entities = _detect_with_regex("Patient Jane Doe presented with symptoms.", ["PERSON"])
    assert len(entities) >= 1


# ── regex detection: IP_ADDRESS ───────────────────────────────


def test_detect_ip_address():
    entities = _detect_with_regex("Server at 192.168.1.100 responded.", ["IP_ADDRESS"])
    assert len(entities) == 1
    assert entities[0]["type"] == "IP_ADDRESS"


# ── all types detection ───────────────────────────────────────


def test_detect_multiple_types():
    text = "Dr. John Smith, SSN 123-45-6789, email john@hospital.org, DOB: 01/01/1980"
    entities = _detect_with_regex(text)
    types_found = {e["type"] for e in entities}
    assert "SSN" in types_found
    assert "EMAIL" in types_found


def test_no_entities_in_clean_text():
    entities = _detect_with_regex("Metformin is effective for diabetes treatment.")
    assert len(entities) == 0


# ── redaction ─────────────────────────────────────────────────


def test_redact_single_entity():
    entities = [{"type": "SSN", "value": "123-45-6789", "start": 15, "end": 26, "score": 0.9}]
    text = "Patient SSN is 123-45-6789 on file."
    redacted = _redact_text(text, entities)
    assert "[SSN]" in redacted
    assert "123-45-6789" not in redacted


def test_redact_multiple_entities():
    entities = [
        {"type": "SSN", "value": "123-45-6789", "start": 4, "end": 15, "score": 0.9},
        {"type": "EMAIL", "value": "a@b.com", "start": 20, "end": 27, "score": 0.9},
    ]
    text = "SSN 123-45-6789 and a@b.com here."
    redacted = _redact_text(text, entities)
    assert "[SSN]" in redacted
    assert "[EMAIL]" in redacted
    assert "123-45-6789" not in redacted


# ── full tool tests (regex path) ──────────────────────────────


async def test_tool_detects_phi(tool, ctx):
    result = await tool._run_async_impl(
        {"text": "Patient SSN 123-45-6789, phone (555) 123-4567"}, ctx
    )
    assert result["entity_count"] >= 2
    assert result["detection_method"] == "regex"  # Presidio not installed in test env


async def test_tool_empty_text(tool, ctx):
    result = await tool._run_async_impl({"text": ""}, ctx)
    assert result["entity_count"] == 0
    assert result["entities"] == []


async def test_tool_specific_entity_types(tool, ctx):
    result = await tool._run_async_impl(
        {"text": "SSN 123-45-6789 and email test@example.com", "entity_types": ["SSN"]}, ctx
    )
    # Should only detect SSN, not email
    types = {e["type"] for e in result["entities"]}
    assert "SSN" in types
    assert "EMAIL" not in types


async def test_tool_return_redacted(tool, ctx):
    result = await tool._run_async_impl(
        {"text": "SSN is 123-45-6789 on file.", "return_redacted": True}, ctx
    )
    assert "redacted_text" in result
    assert "[SSN]" in result["redacted_text"]
    assert "123-45-6789" not in result["redacted_text"]


async def test_tool_no_redacted_when_not_requested(tool, ctx):
    result = await tool._run_async_impl(
        {"text": "SSN is 123-45-6789", "return_redacted": False}, ctx
    )
    assert "redacted_text" not in result


async def test_tool_no_redacted_when_no_entities(tool, ctx):
    result = await tool._run_async_impl(
        {"text": "Clean medical text about diabetes.", "return_redacted": True}, ctx
    )
    assert "redacted_text" not in result


async def test_entity_positions_correct(tool, ctx):
    text = "Email: test@example.com is on file."
    result = await tool._run_async_impl({"text": text, "entity_types": ["EMAIL"]}, ctx)
    for entity in result["entities"]:
        assert text[entity["start"]:entity["end"]] == entity["value"]


async def test_entity_score_present(tool, ctx):
    result = await tool._run_async_impl(
        {"text": "SSN 123-45-6789"}, ctx
    )
    for entity in result["entities"]:
        assert "score" in entity
        assert 0 <= entity["score"] <= 1
