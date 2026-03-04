"""Tests for structured MCP error responses.

Validates _status_to_category(), structured_error_response(), and the
enriched attributes on CircuitOpenError and RetryExhaustedError.
"""

import sys
import os

import pytest

# Ensure the MCP servers package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers._http import (
    CircuitOpenError,
    RetryExhaustedError,
    _status_to_category,
    structured_error_response,
)


# ---------------------------------------------------------------------------
# _status_to_category
# ---------------------------------------------------------------------------


class TestStatusToCategory:
    """Test _status_to_category() with all documented status codes."""

    def test_rate_limited(self):
        assert _status_to_category(429) == "rate_limited"

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_service_unavailable(self, status):
        assert _status_to_category(status) == "service_unavailable"

    def test_not_found(self):
        assert _status_to_category(404) == "not_found"

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_error(self, status):
        assert _status_to_category(status) == "auth_error"

    def test_network_error(self):
        assert _status_to_category(0) == "network_error"

    @pytest.mark.parametrize("status", [400, 405, 408, 418, 422, 451])
    def test_default_api_error(self, status):
        assert _status_to_category(status) == "api_error"


# ---------------------------------------------------------------------------
# CircuitOpenError attributes
# ---------------------------------------------------------------------------


class TestCircuitOpenErrorAttributes:
    """Verify enriched attributes on CircuitOpenError."""

    def test_attributes(self):
        exc = CircuitOpenError("https://example.com/api")
        assert exc.url == "https://example.com/api"
        assert exc.error_category == "circuit_open"
        assert exc.is_retryable is True
        assert isinstance(exc.retry_after_seconds, (int, float))
        assert exc.retry_after_seconds > 0

    def test_str(self):
        exc = CircuitOpenError("https://example.com/api")
        assert "Circuit breaker OPEN" in str(exc)
        assert "example.com" in str(exc)


# ---------------------------------------------------------------------------
# RetryExhaustedError attributes
# ---------------------------------------------------------------------------


class TestRetryExhaustedErrorAttributes:
    """Verify enriched attributes on RetryExhaustedError."""

    def test_retryable_status(self):
        exc = RetryExhaustedError("https://example.com/api", 429, 4)
        assert exc.url == "https://example.com/api"
        assert exc.last_status == 429
        assert exc.attempts == 4
        assert exc.error_category == "rate_limited"
        assert exc.is_retryable is True

    def test_non_retryable_status(self):
        exc = RetryExhaustedError("https://example.com/api", 404, 4)
        assert exc.error_category == "not_found"
        assert exc.is_retryable is False

    def test_network_error_status(self):
        exc = RetryExhaustedError("https://example.com/api", 0, 4)
        assert exc.error_category == "network_error"
        assert exc.is_retryable is False  # 0 not in RETRYABLE_STATUS_CODES

    def test_server_error_status(self):
        exc = RetryExhaustedError("https://example.com/api", 503, 3)
        assert exc.error_category == "service_unavailable"
        assert exc.is_retryable is True

    def test_str(self):
        exc = RetryExhaustedError("https://example.com/api", 500, 4)
        assert "4 retries exhausted" in str(exc)
        assert "500" in str(exc)


# ---------------------------------------------------------------------------
# structured_error_response
# ---------------------------------------------------------------------------


REQUIRED_FIELDS = {"success", "error", "error_category", "is_retryable", "server", "tool"}


class TestStructuredErrorResponse:
    """Verify structured_error_response() output schema."""

    def test_circuit_open_error(self):
        exc = CircuitOpenError("https://api.example.com/v1")
        result = structured_error_response(exc, "pubmed", "search_pubmed")

        # All required fields present
        assert REQUIRED_FIELDS.issubset(result.keys())

        # Values
        assert result["success"] is False
        assert result["server"] == "pubmed"
        assert result["tool"] == "search_pubmed"
        assert result["error_category"] == "circuit_open"
        assert result["is_retryable"] is True
        assert "retry_after_seconds" in result
        assert result["retry_after_seconds"] > 0
        assert "Circuit breaker OPEN" in result["error"]

    def test_retry_exhausted_error(self):
        exc = RetryExhaustedError("https://api.example.com/v1", 429, 4)
        result = structured_error_response(exc, "openfda", "search_drug_events")

        assert REQUIRED_FIELDS.issubset(result.keys())
        assert result["success"] is False
        assert result["server"] == "openfda"
        assert result["tool"] == "search_drug_events"
        assert result["error_category"] == "rate_limited"
        assert result["is_retryable"] is True
        assert result["last_status"] == 429
        assert result["attempts"] == 4

    def test_retry_exhausted_server_error(self):
        exc = RetryExhaustedError("https://api.example.com/v1", 503, 3)
        result = structured_error_response(exc, "cdc", "search_disease_data")

        assert result["error_category"] == "service_unavailable"
        assert result["is_retryable"] is True
        assert result["last_status"] == 503
        assert result["attempts"] == 3

    def test_retry_exhausted_not_found(self):
        exc = RetryExhaustedError("https://api.example.com/v1", 404, 4)
        result = structured_error_response(exc, "clinicaltrials", "get_trial_details")

        assert result["error_category"] == "not_found"
        assert result["is_retryable"] is False

    def test_generic_exception(self):
        exc = ValueError("something broke")
        result = structured_error_response(exc, "genomic", "search_variants")

        assert REQUIRED_FIELDS.issubset(result.keys())
        assert result["success"] is False
        assert result["server"] == "genomic"
        assert result["tool"] == "search_variants"
        assert result["error_category"] == "api_error"
        assert result["is_retryable"] is False
        assert "something broke" in result["error"]

    def test_httpx_error(self):
        """httpx.HTTPError should produce network_error category."""
        import httpx

        exc = httpx.ConnectError("Connection refused")
        result = structured_error_response(exc, "regulatory", "search_510k")

        assert REQUIRED_FIELDS.issubset(result.keys())
        assert result["success"] is False
        assert result["error_category"] == "network_error"
        assert result["is_retryable"] is True
        assert result["server"] == "regulatory"
        assert result["tool"] == "search_510k"

    def test_merge_with_results_key(self):
        """Verify the {**structured_error_response(...), "results": []} pattern works."""
        exc = CircuitOpenError("https://api.example.com")
        result = {**structured_error_response(exc, "pubmed", "search_pubmed"), "results": []}

        assert result["results"] == []
        assert result["success"] is False
        assert result["error_category"] == "circuit_open"
        assert result["server"] == "pubmed"
        assert result["tool"] == "search_pubmed"


# ---------------------------------------------------------------------------
# FirecrawlCircuitOpenError attributes
# ---------------------------------------------------------------------------


class TestFirecrawlCircuitOpenErrorAttributes:
    """Verify enriched attributes on FirecrawlCircuitOpenError."""

    def test_class_attributes(self):
        from mcp_servers._firecrawl import FirecrawlCircuitOpenError

        exc = FirecrawlCircuitOpenError("circuit open")
        assert exc.error_category == "circuit_open"
        assert exc.is_retryable is True
        assert exc.retry_after_seconds == 60
