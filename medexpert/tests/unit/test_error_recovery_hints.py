"""Tests for error recovery hints (Phase 2A)."""

import pytest
from lifesci_tools.error_recovery_hints import get_recovery_hint, enrich_mcp_failures


class TestGetRecoveryHint:
    """Tests for hint lookup logic."""

    def test_exact_server_match(self):
        hint = get_recovery_hint("openfda", "rate_limited")
        assert hint is not None
        assert "40 req/min" in hint

    def test_pubmed_rate_limited(self):
        hint = get_recovery_hint("pubmed", "rate_limited")
        assert "NCBI_API_KEY" in hint

    def test_wildcard_match(self):
        hint = get_recovery_hint("unknown_server", "circuit_open")
        assert hint is not None
        assert "circuit breaker" in hint

    def test_exact_match_preferred_over_wildcard(self):
        """Server-specific hint should be returned instead of wildcard."""
        hint = get_recovery_hint("openfda", "rate_limited")
        assert "OpenFDA" in hint  # Server-specific, not generic wildcard

    def test_no_match_returns_none(self):
        hint = get_recovery_hint("unknown_server", "unknown_category")
        assert hint is None

    def test_auth_error_wildcard(self):
        hint = get_recovery_hint("some_server", "auth_error")
        assert hint is not None
        assert ".env" in hint

    def test_service_unavailable_specific(self):
        hint = get_recovery_hint("clinicaltrials", "service_unavailable")
        assert "ClinicalTrials.gov" in hint

    def test_service_unavailable_wildcard(self):
        hint = get_recovery_hint("new_server", "service_unavailable")
        assert hint is not None
        assert "temporarily" in hint


class TestEnrichMcpFailures:
    """Tests for in-place enrichment of MCP failure dicts."""

    def test_adds_recovery_hint(self):
        failures = [
            {"server": "openfda", "error_category": "rate_limited", "is_retryable": True},
        ]
        result = enrich_mcp_failures(failures)
        assert result is failures  # in-place
        assert "recovery_hint" in failures[0]
        assert "OpenFDA" in failures[0]["recovery_hint"]

    def test_no_hint_no_key(self):
        failures = [
            {"server": "unknown", "error_category": "unknown_cat", "is_retryable": False},
        ]
        enrich_mcp_failures(failures)
        assert "recovery_hint" not in failures[0]

    def test_multiple_failures(self):
        failures = [
            {"server": "pubmed", "error_category": "rate_limited", "is_retryable": True},
            {"server": "openfda", "error_category": "service_unavailable", "is_retryable": False},
            {"server": "unknown", "error_category": "mystery", "is_retryable": False},
        ]
        enrich_mcp_failures(failures)
        assert "recovery_hint" in failures[0]
        assert "recovery_hint" in failures[1]
        assert "recovery_hint" not in failures[2]

    def test_skips_non_dict_entries(self):
        failures = [None, "bad", {"server": "pubmed", "error_category": "rate_limited", "is_retryable": True}]
        enrich_mcp_failures(failures)  # should not raise
        assert "recovery_hint" in failures[2]

    def test_empty_list(self):
        result = enrich_mcp_failures([])
        assert result == []
