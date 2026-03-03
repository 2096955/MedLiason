"""Tests for the shared Firecrawl SDK wrapper (_firecrawl.py).

Covers circuit breaker, concurrency semaphore, retry logic,
graceful degradation, and is_configured() checks.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_module():
    """Reset module-level singleton state before each test."""
    import mcp_servers._firecrawl as mod

    mod.reset_for_testing()
    yield
    mod.reset_for_testing()


# ---------------------------------------------------------------------------
# is_configured()
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_returns_true_when_key_set(self):
        import mcp_servers._firecrawl as mod

        with patch.object(mod, "FIRECRAWL_API_KEY", "fc-test-key"):
            assert mod.is_configured() is True

    def test_returns_false_when_key_empty(self):
        import mcp_servers._firecrawl as mod

        with patch.object(mod, "FIRECRAWL_API_KEY", ""):
            assert mod.is_configured() is False


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_circuit_starts_closed(self):
        import mcp_servers._firecrawl as mod

        assert mod._circuit_is_open() is False

    def test_circuit_opens_after_threshold_failures(self):
        import mcp_servers._firecrawl as mod

        for _ in range(mod._CIRCUIT_FAILURE_THRESHOLD):
            mod._record_failure()
        assert mod._circuit_is_open() is True

    def test_circuit_stays_closed_below_threshold(self):
        import mcp_servers._firecrawl as mod

        for _ in range(mod._CIRCUIT_FAILURE_THRESHOLD - 1):
            mod._record_failure()
        assert mod._circuit_is_open() is False

    def test_success_resets_failure_count(self):
        import mcp_servers._firecrawl as mod

        for _ in range(mod._CIRCUIT_FAILURE_THRESHOLD - 1):
            mod._record_failure()
        mod._record_success()
        assert mod._failure_count == 0
        assert mod._circuit_is_open() is False

    def test_circuit_recovers_after_timeout(self, monkeypatch):
        import mcp_servers._firecrawl as mod

        for _ in range(mod._CIRCUIT_FAILURE_THRESHOLD):
            mod._record_failure()
        assert mod._circuit_is_open() is True

        # Fast-forward past recovery timeout
        monkeypatch.setattr(mod, "_circuit_open_until", 0.0)
        assert mod._circuit_is_open() is False


# ---------------------------------------------------------------------------
# firecrawl_search()
# ---------------------------------------------------------------------------


class TestFirecrawlSearch:
    async def test_successful_search_returns_results(self):
        import mcp_servers._firecrawl as mod

        mock_client = AsyncMock()
        mock_client.search.return_value = [
            {"title": "Page 1", "url": "https://example.com/1", "markdown": "content 1"},
            {"title": "Page 2", "url": "https://example.com/2", "markdown": "content 2"},
        ]
        with (
            patch.object(mod, "FIRECRAWL_API_KEY", "fc-test"),
            patch.object(mod, "_get_client", return_value=mock_client),
        ):
            results = await mod.firecrawl_search("test query")
        assert len(results) == 2
        assert results[0]["title"] == "Page 1"

    async def test_search_returns_empty_on_no_client(self):
        import mcp_servers._firecrawl as mod

        with patch.object(mod, "_get_client", return_value=None):
            results = await mod.firecrawl_search("test")
        assert results == []

    async def test_search_raises_on_circuit_open(self):
        import mcp_servers._firecrawl as mod

        for _ in range(mod._CIRCUIT_FAILURE_THRESHOLD):
            mod._record_failure()

        with pytest.raises(mod.FirecrawlCircuitOpenError):
            await mod.firecrawl_search("test")

    async def test_search_retries_on_failure(self):
        import mcp_servers._firecrawl as mod

        mock_client = AsyncMock()
        mock_client.search.side_effect = [
            RuntimeError("transient"),
            [{"title": "Recovered", "url": "https://example.com"}],
        ]
        with (
            patch.object(mod, "FIRECRAWL_API_KEY", "fc-test"),
            patch.object(mod, "_get_client", return_value=mock_client),
            patch.object(mod, "_BASE_DELAY", 0.01),
        ):
            results = await mod.firecrawl_search("test")
        assert len(results) == 1
        assert results[0]["title"] == "Recovered"
        assert mock_client.search.call_count == 2

    async def test_search_returns_empty_after_all_retries_exhausted(self):
        import mcp_servers._firecrawl as mod

        mock_client = AsyncMock()
        mock_client.search.side_effect = RuntimeError("persistent failure")
        with (
            patch.object(mod, "FIRECRAWL_API_KEY", "fc-test"),
            patch.object(mod, "_get_client", return_value=mock_client),
            patch.object(mod, "_BASE_DELAY", 0.01),
        ):
            results = await mod.firecrawl_search("test")
        assert results == []

    async def test_search_handles_dict_response(self):
        import mcp_servers._firecrawl as mod

        mock_client = AsyncMock()
        mock_client.search.return_value = {
            "data": [{"title": "Result", "url": "https://example.com"}]
        }
        with (
            patch.object(mod, "FIRECRAWL_API_KEY", "fc-test"),
            patch.object(mod, "_get_client", return_value=mock_client),
        ):
            results = await mod.firecrawl_search("test")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# firecrawl_scrape()
# ---------------------------------------------------------------------------


class TestFirecrawlScrape:
    async def test_successful_scrape(self):
        import mcp_servers._firecrawl as mod

        mock_client = AsyncMock()
        mock_client.scrape_url.return_value = {
            "markdown": "# Title\nContent here",
            "metadata": {"title": "Test Page", "sourceURL": "https://example.com"},
        }
        with (
            patch.object(mod, "FIRECRAWL_API_KEY", "fc-test"),
            patch.object(mod, "_get_client", return_value=mock_client),
        ):
            result = await mod.firecrawl_scrape("https://example.com")
        assert "markdown" in result
        assert result["markdown"] == "# Title\nContent here"

    async def test_scrape_returns_error_on_no_client(self):
        import mcp_servers._firecrawl as mod

        with patch.object(mod, "_get_client", return_value=None):
            result = await mod.firecrawl_scrape("https://example.com")
        assert "error" in result

    async def test_scrape_raises_on_circuit_open(self):
        import mcp_servers._firecrawl as mod

        for _ in range(mod._CIRCUIT_FAILURE_THRESHOLD):
            mod._record_failure()

        with pytest.raises(mod.FirecrawlCircuitOpenError):
            await mod.firecrawl_scrape("https://example.com")

    async def test_scrape_retries_on_failure(self):
        import mcp_servers._firecrawl as mod

        mock_client = AsyncMock()
        mock_client.scrape_url.side_effect = [
            RuntimeError("transient"),
            {"markdown": "recovered", "metadata": {}},
        ]
        with (
            patch.object(mod, "FIRECRAWL_API_KEY", "fc-test"),
            patch.object(mod, "_get_client", return_value=mock_client),
            patch.object(mod, "_BASE_DELAY", 0.01),
        ):
            result = await mod.firecrawl_scrape("https://example.com")
        assert result["markdown"] == "recovered"
        assert mock_client.scrape_url.call_count == 2


# ---------------------------------------------------------------------------
# Concurrency semaphore
# ---------------------------------------------------------------------------


class TestSemaphore:
    async def test_semaphore_limits_concurrency(self):
        import mcp_servers._firecrawl as mod

        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()

        original_search = AsyncMock()

        async def track_concurrency(*args, **kwargs):
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                if current > max_concurrent:
                    max_concurrent = current
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1
            return [{"title": "r", "url": "u"}]

        mock_client = MagicMock()
        mock_client.search = track_concurrency

        with (
            patch.object(mod, "FIRECRAWL_API_KEY", "fc-test"),
            patch.object(mod, "_get_client", return_value=mock_client),
        ):
            tasks = [mod.firecrawl_search(f"q{i}") for i in range(6)]
            await asyncio.gather(*tasks)

        assert max_concurrent <= mod._MAX_CONCURRENT
