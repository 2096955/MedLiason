"""Tests for the BMA Library MCP server.

Covers search_bma_guidelines, get_bma_article, search_clinical_guidelines
with mocked Firecrawl responses.  Tests graceful degradation, input
sanitization, URL validation, paywall detection, and content truncation.
"""

from unittest.mock import AsyncMock, patch

import pytest

import mcp_servers.bma_library.server as bma_mod
from mcp_servers._firecrawl import FirecrawlCircuitOpenError

# Unwrap FastMCP FunctionTool wrappers to get the raw async callable.
search_bma_guidelines = bma_mod.search_bma_guidelines.fn
get_bma_article = bma_mod.get_bma_article.fn
search_clinical_guidelines = bma_mod.search_clinical_guidelines.fn


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the module-level cache between tests."""
    bma_mod._cache._cache.clear()
    yield
    bma_mod._cache._cache.clear()


# ---------------------------------------------------------------------------
# search_bma_guidelines
# ---------------------------------------------------------------------------


class TestSearchBmaGuidelines:
    async def test_successful_search(self):
        results = [
            {"title": "Hypertension guideline", "url": "https://www.bma.org.uk/guideline/1", "markdown": "BP targets..."},
        ]
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_search", new_callable=AsyncMock, return_value=results),
        ):
            resp = await search_bma_guidelines("hypertension management")
        assert resp["query"] == "hypertension management"
        assert resp["returned_count"] == 1
        assert resp["source"] == "BMA Library"

    async def test_empty_query_returns_error(self):
        resp = await search_bma_guidelines("")
        assert "error" in resp

    async def test_not_configured(self):
        with patch("mcp_servers.bma_library.server.is_configured", return_value=False):
            resp = await search_bma_guidelines("test")
        assert resp["status"] == "not_configured"

    async def test_query_is_sanitized(self):
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_search", new_callable=AsyncMock, return_value=[]) as mock_search,
        ):
            await search_bma_guidelines("test; DROP TABLE")
        call_query = mock_search.call_args[0][0]
        assert "DROP" not in call_query

    async def test_circuit_breaker_error(self):
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_search", new_callable=AsyncMock, side_effect=FirecrawlCircuitOpenError("open")),
        ):
            resp = await search_bma_guidelines("test")
        assert resp["success"] is False
        assert resp["error_category"] == "circuit_open"
        assert resp["is_retryable"] is True
        assert resp["results"] == []

    async def test_cache_hit(self):
        results = [{"title": "Cached", "url": "https://bma.org.uk/1", "markdown": "x"}]
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_search", new_callable=AsyncMock, return_value=results) as mock_search,
        ):
            await search_bma_guidelines("diabetes")
            await search_bma_guidelines("diabetes")
        assert mock_search.call_count == 1


# ---------------------------------------------------------------------------
# get_bma_article
# ---------------------------------------------------------------------------


class TestGetBmaArticle:
    async def test_successful_scrape(self):
        scrape_result = {
            "markdown": "# Antibiotic Prescribing\n\nKey recommendations...",
            "metadata": {"title": "Antibiotic Prescribing", "sourceURL": "https://www.bma.org.uk/library/article-1"},
        }
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_bma_article("https://www.bma.org.uk/library/article-1")
        assert resp["source"] == "BMA Library"
        assert "Antibiotic" in resp["content_markdown"]
        assert "access_restricted" not in resp

    async def test_paywall_detected(self):
        scrape_result = {
            "markdown": "# Restricted\n\nSign in to access full content. BMA members only.",
            "metadata": {"title": "Restricted Guideline", "sourceURL": "https://www.bma.org.uk/library/restricted"},
        }
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_bma_article("https://www.bma.org.uk/library/restricted")
        assert resp["access_restricted"] is True
        assert "membership" in resp["reason"].lower()

    async def test_rejects_non_bma_url(self):
        resp = await get_bma_article("https://evil.com/fake")
        assert "error" in resp
        assert "bma.org.uk" in resp["error"].lower()

    async def test_rejects_http_url(self):
        resp = await get_bma_article("http://www.bma.org.uk/library/test")
        assert "error" in resp

    async def test_not_configured(self):
        with patch("mcp_servers.bma_library.server.is_configured", return_value=False):
            resp = await get_bma_article("https://www.bma.org.uk/library/test")
        assert resp["status"] == "not_configured"

    async def test_content_truncated(self):
        long_content = "Clinical recommendation. " * 1000
        scrape_result = {
            "markdown": long_content,
            "metadata": {"title": "Long", "sourceURL": "https://www.bma.org.uk/library/long"},
        }
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_scrape", new_callable=AsyncMock, return_value=scrape_result),
        ):
            resp = await get_bma_article("https://www.bma.org.uk/library/long")
        assert resp.get("content_truncated") is True
        assert len(resp["content_markdown"]) <= bma_mod.MAX_CONTENT_LENGTH + 1

    async def test_scrape_error(self):
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_scrape", new_callable=AsyncMock, return_value={"error": "404"}),
        ):
            resp = await get_bma_article("https://www.bma.org.uk/library/missing")
        assert "error" in resp


# ---------------------------------------------------------------------------
# search_clinical_guidelines
# ---------------------------------------------------------------------------


class TestSearchClinicalGuidelines:
    async def test_successful_search(self):
        results = [
            {"title": "NICE Asthma", "url": "https://www.nice.org.uk/guidance/ng80", "markdown": "NICE says..."},
            {"title": "BMA Asthma", "url": "https://www.bma.org.uk/library/asthma", "markdown": "BMA says..."},
        ]
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_search", new_callable=AsyncMock, return_value=results),
        ):
            resp = await search_clinical_guidelines("asthma management")
        assert resp["returned_count"] == 2
        assert resp["source"] == "UK Clinical Guidelines"

    async def test_query_augmented_with_uk_guidelines(self):
        with (
            patch("mcp_servers.bma_library.server.is_configured", return_value=True),
            patch("mcp_servers.bma_library.server.firecrawl_search", new_callable=AsyncMock, return_value=[]) as mock_search,
        ):
            await search_clinical_guidelines("diabetes")
        call_query = mock_search.call_args[0][0]
        assert "clinical practice guidelines UK" in call_query

    async def test_not_configured(self):
        with patch("mcp_servers.bma_library.server.is_configured", return_value=False):
            resp = await search_clinical_guidelines("test")
        assert resp["status"] == "not_configured"

    async def test_empty_query(self):
        resp = await search_clinical_guidelines("")
        assert "error" in resp


# ---------------------------------------------------------------------------
# _detect_paywall
# ---------------------------------------------------------------------------


class TestDetectPaywall:
    def test_detects_sign_in(self):
        assert bma_mod._detect_paywall("Please Sign in to access this content") is True

    def test_detects_members_only(self):
        assert bma_mod._detect_paywall("This resource is for Members Only") is True

    def test_normal_content_not_flagged(self):
        assert bma_mod._detect_paywall("# Clinical Guidelines\n\nKey recommendations...") is False

    def test_empty_content(self):
        assert bma_mod._detect_paywall("") is False
