"""Tests for the NHS 111 MCP server.

Covers search_nhs_conditions, get_nhs_condition_page,
get_nhs_treatment_guidance with mocked Firecrawl responses.
Tests graceful degradation, input sanitization, URL validation,
content truncation, and redirect protection.
"""

from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.nhs_111.server as nhs_mod
from mcp_servers._firecrawl import FirecrawlCircuitOpenError

# Unwrap FastMCP FunctionTool wrappers to get the raw async callable.
search_nhs_conditions = nhs_mod.search_nhs_conditions.fn
get_nhs_condition_page = nhs_mod.get_nhs_condition_page.fn
get_nhs_treatment_guidance = nhs_mod.get_nhs_treatment_guidance.fn


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the module-level cache between tests."""
    nhs_mod._cache._cache.clear()
    yield
    nhs_mod._cache._cache.clear()


# ---------------------------------------------------------------------------
# search_nhs_conditions
# ---------------------------------------------------------------------------


class TestSearchNhsConditions:
    async def test_successful_search(self):
        results = [
            {"title": "Common cold", "url": "https://www.nhs.uk/conditions/common-cold/", "markdown": "Symptoms include..."},
            {"title": "Flu", "url": "https://www.nhs.uk/conditions/flu/", "markdown": "Flu is caused..."},
        ]
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_search", new_callable=AsyncMock, return_value=results),
        ):
            resp = await search_nhs_conditions("cold symptoms")
        assert resp["query"] == "cold symptoms"
        assert resp["returned_count"] == 2
        assert resp["results"][0]["title"] == "Common cold"

    async def test_empty_query_returns_error(self):
        resp = await search_nhs_conditions("")
        assert "error" in resp

    async def test_not_configured_returns_degraded(self):
        with patch("mcp_servers.nhs_111.server.is_configured", return_value=False):
            resp = await search_nhs_conditions("headache")
        assert resp["status"] == "not_configured"
        assert "FIRECRAWL_API_KEY" in resp["reason"]

    async def test_query_is_sanitized(self):
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_search", new_callable=AsyncMock, return_value=[]) as mock_search,
        ):
            await search_nhs_conditions("headache; DROP TABLE users")
        call_query = mock_search.call_args[0][0]
        assert "DROP" not in call_query

    async def test_max_results_capped_at_10(self):
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_search", new_callable=AsyncMock, return_value=[]) as mock_search,
        ):
            await search_nhs_conditions("test", max_results=50)
        assert mock_search.call_args[1]["params"]["limit"] == 10

    async def test_circuit_breaker_error_handled(self):
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_search", new_callable=AsyncMock, side_effect=FirecrawlCircuitOpenError("open")),
        ):
            resp = await search_nhs_conditions("test")
        assert resp["success"] is False
        assert resp["error_category"] == "circuit_open"
        assert resp["is_retryable"] is True
        assert resp["results"] == []

    async def test_cache_hit(self):
        results = [{"title": "Cached", "url": "https://nhs.uk/c", "markdown": "x"}]
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_search", new_callable=AsyncMock, return_value=results) as mock_search,
        ):
            resp1 = await search_nhs_conditions("cold")
            resp2 = await search_nhs_conditions("cold")
        assert resp1 == resp2
        assert mock_search.call_count == 1  # Second call served from cache


# ---------------------------------------------------------------------------
# get_nhs_condition_page
# ---------------------------------------------------------------------------


class TestGetNhsConditionPage:
    async def test_successful_scrape(self):
        scrape_result = {
            "markdown": "# Common Cold\n\nA common cold is a mild viral infection.",
            "metadata": {"title": "Common cold - NHS", "sourceURL": "https://www.nhs.uk/conditions/common-cold/"},
        }
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_nhs_condition_page("common-cold")
        assert resp["condition"] == "common-cold"
        assert resp["url"] == "https://www.nhs.uk/conditions/common-cold/"
        assert "Common Cold" in resp["content_markdown"]
        assert resp["source"] == "NHS UK"

    async def test_invalid_slug_rejected(self):
        resp = await get_nhs_condition_page("../../../etc/passwd")
        assert "error" in resp

    async def test_slug_with_special_chars_rejected(self):
        resp = await get_nhs_condition_page("cold<script>")
        assert "error" in resp

    async def test_valid_slugs_accepted(self):
        for slug in ("common-cold", "earache", "type-2-diabetes", "covid-19"):
            assert nhs_mod._CONDITION_SLUG_RE.match(slug), f"Slug {slug!r} should be valid"

    async def test_empty_condition_returns_error(self):
        resp = await get_nhs_condition_page("")
        assert "error" in resp

    async def test_not_configured(self):
        with patch("mcp_servers.nhs_111.server.is_configured", return_value=False):
            resp = await get_nhs_condition_page("cold")
        assert resp["status"] == "not_configured"

    async def test_content_truncated(self):
        long_content = "This is a sentence. " * 1000  # ~20000 chars
        scrape_result = {
            "markdown": long_content,
            "metadata": {"title": "Long page", "sourceURL": "https://www.nhs.uk/conditions/test/"},
        }
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_nhs_condition_page("test")
        assert len(resp["content_markdown"]) <= nhs_mod.MAX_CONTENT_LENGTH + 1
        assert resp.get("content_truncated") is True

    async def test_scrape_error_returns_fallback(self):
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_scrape", new_callable=AsyncMock, return_value={"error": "404 Not Found"}),
        ):
            resp = await get_nhs_condition_page("nonexistent-condition")
        assert "error" in resp
        assert "fallback_search_query" in resp


# ---------------------------------------------------------------------------
# get_nhs_treatment_guidance
# ---------------------------------------------------------------------------


class TestGetNhsTreatmentGuidance:
    async def test_successful_scrape(self):
        scrape_result = {
            "markdown": "# Treatment\n\nRest and drink fluids.",
            "metadata": {"title": "Treatment - NHS", "sourceURL": "https://www.nhs.uk/conditions/cold/treatment/"},
        }
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_nhs_treatment_guidance("https://www.nhs.uk/conditions/cold/treatment/")
        assert resp["source"] == "NHS UK"
        assert "Treatment" in resp["content_markdown"]

    async def test_rejects_non_nhs_url(self):
        resp = await get_nhs_treatment_guidance("https://evil.com/phishing")
        assert "error" in resp
        assert "nhs.uk" in resp["error"].lower()

    async def test_rejects_http_url(self):
        resp = await get_nhs_treatment_guidance("http://www.nhs.uk/conditions/cold/")
        assert "error" in resp

    async def test_rejects_url_with_credentials(self):
        resp = await get_nhs_treatment_guidance("https://user:pass@www.nhs.uk/conditions/cold/")
        assert "error" in resp

    async def test_not_configured(self):
        with patch("mcp_servers.nhs_111.server.is_configured", return_value=False):
            resp = await get_nhs_treatment_guidance("https://www.nhs.uk/conditions/cold/")
        assert resp["status"] == "not_configured"

    async def test_redirect_outside_nhs_rejected(self):
        scrape_result = {
            "markdown": "# Redirected content",
            "metadata": {"title": "Evil", "sourceURL": "https://evil.com/phished"},
        }
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_nhs_treatment_guidance("https://www.nhs.uk/conditions/redirect-test/")
        assert "error" in resp
        assert "redirect" in resp["error"].lower()

    async def test_content_truncated(self):
        long_content = "Treatment advice. " * 1000
        scrape_result = {
            "markdown": long_content,
            "metadata": {"title": "Long", "sourceURL": "https://www.nhs.uk/conditions/long/"},
        }
        with (
            patch("mcp_servers.nhs_111.server.is_configured", return_value=True),
            patch("mcp_servers.nhs_111.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_nhs_treatment_guidance("https://www.nhs.uk/conditions/long/")
        assert resp.get("content_truncated") is True


# ---------------------------------------------------------------------------
# _truncate_at_sentence
# ---------------------------------------------------------------------------


class TestTruncateAtSentence:
    def test_short_text_unchanged(self):
        text, truncated = nhs_mod._truncate_at_sentence("Hello world.", 100)
        assert text == "Hello world."
        assert truncated is False

    def test_truncates_at_sentence_boundary(self):
        text = "First sentence. Second sentence. Third sentence."
        result, truncated = nhs_mod._truncate_at_sentence(text, 35)
        # rfind(". ") at idx 30 → text[:31] keeps through the period
        assert result == "First sentence. Second sentence."
        assert truncated is True

    def test_hard_truncate_when_no_boundary(self):
        text = "a" * 200
        result, truncated = nhs_mod._truncate_at_sentence(text, 100)
        assert len(result) == 100
        assert truncated is True
