"""Tests for extract_json_from_text — resilient JSON extraction from LLM responses."""

import pytest

from lifesci_common.triage_utils import extract_json_from_text


@pytest.mark.parametrize(
    "case_name, text, expected_type",
    [
        (
            "clean_json",
            '{"diagnosis": "cold", "confidence": 75}',
            dict,
        ),
        (
            "markdown_fenced",
            '```json\n{"diagnosis": "cold", "confidence": 75}\n```',
            dict,
        ),
        (
            "markdown_fenced_uppercase",
            '```JSON\n{"diagnosis": "cold", "confidence": 75}\n```',
            dict,
        ),
        (
            "trailing_prose",
            '{"diagnosis": "cold", "confidence": 75} Hope this helps.',
            dict,
        ),
        (
            "leading_prose",
            'Here is my analysis: {"diagnosis": "cold", "confidence": 75}',
            dict,
        ),
        (
            "array_returns_none",
            '[{"diagnosis": "cold"}]',
            type(None),
        ),
        (
            "plain_text_no_json",
            "I cannot determine a diagnosis.",
            type(None),
        ),
        (
            "empty_string",
            "",
            type(None),
        ),
        (
            "nested_json_object",
            '{"diagnosis": "cold", "confidence": 75, "meta": {"source": "exam"}}',
            dict,
        ),
        (
            "prose_with_balanced_braces_before_json",
            'Findings: {uncertain} \u2014 {"diagnosis": "cold", "confidence": 75}',
            dict,
        ),
        (
            "truncated_json",
            '{"diagnosis": "col',
            type(None),
        ),
    ],
    ids=[
        "1-clean_json",
        "2-markdown_fenced",
        "3-markdown_fenced_uppercase",
        "4-trailing_prose",
        "5-leading_prose",
        "6-array_returns_none",
        "7-plain_text_no_json",
        "8-empty_string",
        "9-nested_json_object",
        "10-prose_with_balanced_braces",
        "11-truncated_json",
    ],
)
def test_extract_json_from_text(case_name, text, expected_type):
    result = extract_json_from_text(text)
    assert isinstance(result, expected_type), (
        f"Case '{case_name}': expected {expected_type.__name__}, "
        f"got {type(result).__name__} — value: {result!r}"
    )
    if expected_type is dict:
        assert "diagnosis" in result
