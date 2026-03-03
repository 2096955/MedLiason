"""BMA Library Clinical Guidelines MCP Server (via Firecrawl).

Scrapes BMA Library pages for clinical guidelines, evidence summaries,
and best-practice recommendations.  Used by the research pipeline
(literature specialist) for UK clinical evidence.

API: Firecrawl SDK → bma.org.uk (JS-rendered scraping).
Port: 9017
"""

import logging
import os
import sys
import time

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._firecrawl import (
    FirecrawlCircuitOpenError,
    firecrawl_scrape,
    firecrawl_search,
    is_configured,
)
from mcp_servers._security import sanitize_query, validate_allowed_url

log = logging.getLogger(__name__)

mcp = FastMCP("bma_library")

MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", "8000"))

_BMA_NETLOC_PATTERN = r"www\.bma\.org\.uk$"
_PAYWALL_INDICATORS = [
    "sign in to access",
    "members only",
    "log in to view",
    "bma members can access",
    "please log in",
]


# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------


class _ScrapeCache:
    """Simple in-memory cache with TTL and maxsize eviction."""

    def __init__(self, ttl_seconds: int = 3600, maxsize: int = 500):
        self._cache: dict[str, tuple[float, dict | list]] = {}
        self._ttl = ttl_seconds
        self._maxsize = maxsize

    def get(self, key: str):
        entry = self._cache.get(key)
        if entry and (time.monotonic() - entry[0]) < self._ttl:
            return entry[1]
        if entry:
            del self._cache[key]
        return None

    def set(self, key: str, value) -> None:
        if len(self._cache) >= self._maxsize:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (time.monotonic(), value)


_cache = _ScrapeCache(ttl_seconds=3600, maxsize=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _not_configured_response(query: str) -> dict:
    return {
        "status": "not_configured",
        "reason": "FIRECRAWL_API_KEY not set. Set the environment variable to enable BMA Library scraping.",
        "fallback_search_query": f"{query} site:bma.org.uk",
        "results": [],
    }


def _truncate_at_sentence(text: str, max_len: int) -> tuple[str, bool]:
    """Truncate *text* at the nearest sentence boundary before *max_len*."""
    if len(text) <= max_len:
        return text, False
    truncated = text[:max_len]
    for sep in (". ", ".\n", "? ", "! "):
        idx = truncated.rfind(sep)
        if idx > max_len // 2:
            return truncated[: idx + 1], True
    return truncated, True


def _detect_paywall(markdown: str) -> bool:
    """Return ``True`` if the scraped content appears to be paywalled."""
    lower = markdown.lower()
    return any(indicator in lower for indicator in _PAYWALL_INDICATORS)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_bma_guidelines(
    query: str,
    max_results: int = 5,
) -> dict:
    """Search BMA Library for clinical guidelines matching a query.

    Uses Firecrawl to search bma.org.uk for guidelines and evidence
    summaries.

    Args:
        query: The clinical topic to search for (e.g. ``"hypertension
            management"``, ``"antibiotic prescribing"``).
        max_results: Maximum number of results (default 5, max 10).

    Returns:
        Dictionary with ``query``, ``total_count``, ``returned_count``,
        and ``results`` keys.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    if not is_configured():
        return _not_configured_response(safe_query)

    cache_key = f"bma_search:{safe_query}:{max_results}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    capped = min(max_results, 10)
    try:
        raw_results = await firecrawl_search(
            f"{safe_query} site:bma.org.uk",
            params={"limit": capped},
        )
    except FirecrawlCircuitOpenError:
        return {
            "error": "Firecrawl circuit breaker open — too many recent failures",
            "fallback_search_query": f"{safe_query} site:bma.org.uk",
            "results": [],
        }

    results = []
    for item in raw_results[:capped]:
        if isinstance(item, dict):
            meta = item.get("metadata", {})
            entry = {
                "title": item.get("title", meta.get("title", "")),
                "url": item.get("url", item.get("sourceURL", meta.get("sourceURL", ""))),
                "snippet": (item.get("markdown", "") or "")[:500],
            }
            pub_date = (
                meta.get("publishedDate")
                or meta.get("ogDate")
                or meta.get("date")
                or item.get("publishedDate")
            )
            if pub_date:
                entry["published_date"] = pub_date
            results.append(entry)

    response = {
        "query": safe_query,
        "total_count": len(results),
        "returned_count": len(results),
        "results": results,
        "source": "BMA Library",
    }
    _cache.set(cache_key, response)
    return response


@mcp.tool()
async def get_bma_article(url: str) -> dict:
    """Scrape a specific BMA Library article or guideline page.

    The URL must be on ``www.bma.org.uk``.  Detects membership-gated
    content and flags it with ``access_restricted: true``.

    Args:
        url: Full HTTPS URL on www.bma.org.uk to scrape.

    Returns:
        Dictionary with ``url``, ``title``, ``content_markdown``,
        ``source``, and optional ``access_restricted`` or
        ``content_truncated`` flags.
    """
    validated = validate_allowed_url(url, _BMA_NETLOC_PATTERN)
    if validated is None:
        return {"error": "URL must be https://www.bma.org.uk/..."}

    if not is_configured():
        return _not_configured_response(validated)

    cache_key = f"bma_article:{validated}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        result = await firecrawl_scrape(
            validated,
            params={"formats": ["markdown"], "onlyMainContent": True},
        )
    except FirecrawlCircuitOpenError:
        return {"error": "Firecrawl circuit breaker open", "url": validated}

    if "error" in result:
        return {"error": f"Failed to scrape: {result['error']}", "url": validated}

    # Post-scrape redirect protection
    source_url = (
        result.get("metadata", {}).get("sourceURL", "")
        or result.get("sourceURL", "")
    )
    if source_url and validate_allowed_url(source_url, _BMA_NETLOC_PATTERN) is None:
        return {
            "error": "Page redirected outside bma.org.uk — response rejected for security",
            "url": validated,
            "redirect_target": source_url,
        }

    markdown = result.get("markdown", "") or ""

    # Paywall detection
    if _detect_paywall(markdown):
        response = {
            "url": validated,
            "title": result.get("metadata", {}).get("title", result.get("title", "")),
            "content_markdown": markdown[:500],
            "access_restricted": True,
            "reason": "BMA membership required for full content",
            "source": "BMA Library",
        }
        _cache.set(cache_key, response)
        return response

    content, truncated = _truncate_at_sentence(markdown, MAX_CONTENT_LENGTH)

    meta = result.get("metadata", {})
    response = {
        "url": validated,
        "title": meta.get("title", result.get("title", "")),
        "content_markdown": content,
        "source": "BMA Library",
    }
    pub_date = meta.get("publishedDate") or meta.get("ogDate") or meta.get("date")
    if pub_date:
        response["published_date"] = pub_date
    if truncated:
        response["content_truncated"] = True

    _cache.set(cache_key, response)
    return response


@mcp.tool()
async def search_clinical_guidelines(
    query: str,
    max_results: int = 5,
) -> dict:
    """Search broadly for UK clinical practice guidelines.

    Unlike ``search_bma_guidelines`` this search is not restricted to
    bma.org.uk — it returns results from NICE, BMA, Royal Colleges,
    and other UK clinical guideline publishers.

    Args:
        query: The clinical topic (e.g. ``"asthma management guidelines"``).
        max_results: Maximum number of results (default 5, max 10).

    Returns:
        Dictionary with ``query``, ``total_count``, ``returned_count``,
        and ``results`` keys.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    if not is_configured():
        return _not_configured_response(safe_query)

    cache_key = f"uk_guidelines:{safe_query}:{max_results}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    capped = min(max_results, 10)
    try:
        raw_results = await firecrawl_search(
            f"{safe_query} clinical practice guidelines UK",
            params={"limit": capped},
        )
    except FirecrawlCircuitOpenError:
        return {
            "error": "Firecrawl circuit breaker open — too many recent failures",
            "fallback_search_query": f"{safe_query} clinical practice guidelines UK",
            "results": [],
        }

    results = []
    for item in raw_results[:capped]:
        if isinstance(item, dict):
            meta = item.get("metadata", {})
            entry = {
                "title": item.get("title", meta.get("title", "")),
                "url": item.get("url", item.get("sourceURL", meta.get("sourceURL", ""))),
                "snippet": (item.get("markdown", "") or "")[:500],
            }
            pub_date = (
                meta.get("publishedDate")
                or meta.get("ogDate")
                or meta.get("date")
                or item.get("publishedDate")
            )
            if pub_date:
                entry["published_date"] = pub_date
            results.append(entry)

    response = {
        "query": safe_query,
        "total_count": len(results),
        "returned_count": len(results),
        "results": results,
        "source": "UK Clinical Guidelines",
    }
    _cache.set(cache_key, response)
    return response


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9017)
