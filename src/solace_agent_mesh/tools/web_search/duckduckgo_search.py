"""DuckDuckGo Search implementation using the duckduckgo-search library."""

import logging
import time
from typing import Literal, Optional

from .base import WebSearchTool
from .models import SearchResult, SearchSource

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 5
MAX_RETRIES = 3
RETRY_DELAY = 1.0


class DuckDuckGoSearchTool(WebSearchTool):
    """DuckDuckGo search implementation using the DDGS library."""

    def __init__(self, **kwargs):
        super().__init__(api_key=None, **kwargs)

    async def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        search_depth: Literal["basic", "advanced"] = "basic",
        **kwargs,
    ) -> SearchResult:
        """Execute DuckDuckGo search with retry logic for corporate proxy environments.

        Args:
            query: Search query string
            max_results: Maximum number of results (1-10)
            search_depth: Not used for DDG (kept for interface compatibility)
            **kwargs: Additional parameters

        Returns:
            SearchResult object
        """
        try:
            try:
                max_results = int(max_results)
            except (TypeError, ValueError):
                max_results = DEFAULT_MAX_RESULTS

            num_results = min(max(max_results, 1), 10)

            logger.info("Executing DuckDuckGo search: query='%s', num=%d", query, num_results)

            try:
                from duckduckgo_search import DDGS
            except ImportError:
                error_msg = (
                    "duckduckgo-search package is not installed. "
                    "Install it with: pip install duckduckgo-search"
                )
                logger.error(error_msg)
                return SearchResult(success=False, query=query, error=error_msg)

            # Retry loop for flaky network/proxy environments
            raw_results = []
            last_error = None
            for attempt in range(MAX_RETRIES):
                try:
                    ddgs = DDGS(verify=False)
                    raw_results = list(ddgs.text(query, max_results=num_results))
                    if raw_results:
                        break
                    # Got 0 results — retry after a short delay
                    logger.warning(
                        "DuckDuckGo returned 0 results (attempt %d/%d), retrying...",
                        attempt + 1, MAX_RETRIES,
                    )
                    time.sleep(RETRY_DELAY * (attempt + 1))
                except Exception as e:
                    last_error = e
                    logger.warning(
                        "DuckDuckGo search attempt %d/%d failed: %s",
                        attempt + 1, MAX_RETRIES, e,
                    )
                    time.sleep(RETRY_DELAY * (attempt + 1))

            if not raw_results and last_error:
                error_msg = f"DuckDuckGo search failed after {MAX_RETRIES} attempts: {last_error}"
                logger.error(error_msg)
                return SearchResult(success=False, query=query, error=error_msg)

            organic = []
            for item in raw_results:
                try:
                    source = SearchSource(
                        link=item.get("href", ""),
                        title=item.get("title", ""),
                        snippet=item.get("body", ""),
                        attribution=self._extract_domain(item.get("href", "")),
                    )
                    organic.append(source)
                except Exception as e:
                    logger.warning("Failed to parse DDG result: %s", e)
                    continue

            logger.info("DuckDuckGo search successful: %d results", len(organic))

            return SearchResult(
                success=True,
                query=query,
                organic=organic,
                metadata={
                    "search_engine": "duckduckgo",
                    "total_results": len(organic),
                },
            )

        except Exception as e:
            error_msg = f"DuckDuckGo search failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return SearchResult(success=False, query=query, error=error_msg)

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract clean domain from URL."""
        try:
            domain = url.replace("https://", "").replace("http://", "")
            domain = domain.split("/")[0]
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return url
