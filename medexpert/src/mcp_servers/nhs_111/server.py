"""NHS 111 Clinical Guidance MCP Server (via Firecrawl).

Scrapes NHS 111 condition pages for clinical guidance, symptoms, treatments,
and self-care advice.  Used by the triage pipeline to enrich triage decisions
with NHS-verified clinical information.

API: Firecrawl SDK → nhs.uk (JS-rendered scraping).
Port: 9016
"""

import logging
import os
import re
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

mcp = FastMCP("nhs_111")

MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", "8000"))

_CONDITION_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?$")
_NHS_NETLOC_PATTERN = r"www\.nhs\.uk$"


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
        "reason": "FIRECRAWL_API_KEY not set. Set the environment variable to enable NHS 111 scraping.",
        "fallback_search_query": f"{query} site:nhs.uk/conditions",
        "results": [],
    }


def _truncate_at_sentence(text: str, max_len: int) -> tuple[str, bool]:
    """Truncate *text* at the nearest sentence boundary before *max_len*."""
    if len(text) <= max_len:
        return text, False
    truncated = text[:max_len]
    # Find last sentence-ending punctuation
    for sep in (". ", ".\n", "? ", "! "):
        idx = truncated.rfind(sep)
        if idx > max_len // 2:
            return truncated[: idx + 1], True
    return truncated, True


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_nhs_conditions(
    symptom: str,
    max_results: int = 5,
) -> dict:
    """Search NHS 111 condition pages for a symptom or condition name.

    Uses Firecrawl to search nhs.uk/conditions for pages matching the
    symptom query.  Returns titles, URLs, and snippets for the top results.

    Args:
        symptom: The symptom or condition to search for (e.g. "headache",
            "sore throat", "chest pain").
        max_results: Maximum number of results to return (default 5, max 10).

    Returns:
        Dictionary with ``query``, ``total_count``, ``returned_count``,
        and ``results`` keys.
    """
    safe_query = sanitize_query(symptom)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    if not is_configured():
        return _not_configured_response(safe_query)

    cache_key = f"search:{safe_query}:{max_results}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    capped = min(max_results, 10)
    try:
        raw_results = await firecrawl_search(
            f"{safe_query} site:nhs.uk/conditions",
            params={"limit": capped},
        )
    except FirecrawlCircuitOpenError:
        return {
            "error": "Firecrawl circuit breaker open — too many recent failures",
            "fallback_search_query": f"{safe_query} site:nhs.uk/conditions",
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
            # Extract publication/review date if available
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
    }
    _cache.set(cache_key, response)
    return response


@mcp.tool()
async def get_nhs_condition_page(condition: str) -> dict:
    """Scrape a specific NHS condition page by condition slug.

    Constructs the canonical URL ``https://www.nhs.uk/conditions/{slug}/``
    and scrapes the main content as markdown.

    Args:
        condition: The condition slug (e.g. ``"common-cold"``,
            ``"earache"``, ``"type-2-diabetes"``).

    Returns:
        Dictionary with ``condition``, ``url``, ``title``,
        ``content_markdown``, and optional ``content_truncated`` flag.
    """
    if not condition or not condition.strip():
        return {"error": "Empty condition parameter"}

    slug = condition.strip().lower()
    if not _CONDITION_SLUG_RE.match(slug):
        return {"error": f"Invalid condition slug: must be lowercase alphanumeric with hyphens"}

    if not is_configured():
        return _not_configured_response(slug)

    url = f"https://www.nhs.uk/conditions/{slug}/"

    cache_key = f"page:{slug}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        result = await firecrawl_scrape(
            url,
            params={"formats": ["markdown"], "onlyMainContent": True},
        )
    except FirecrawlCircuitOpenError:
        return {
            "error": "Firecrawl circuit breaker open",
            "url": url,
            "fallback_search_query": f"{slug} site:nhs.uk/conditions",
        }

    if "error" in result:
        return {
            "error": f"Failed to scrape condition page: {result['error']}",
            "url": url,
            "fallback_search_query": f"{slug} site:nhs.uk/conditions",
        }

    markdown = result.get("markdown", "") or ""
    content, truncated = _truncate_at_sentence(markdown, MAX_CONTENT_LENGTH)

    meta = result.get("metadata", {})
    response = {
        "condition": slug,
        "url": url,
        "title": meta.get("title", result.get("title", "")),
        "content_markdown": content,
        "source": "NHS UK",
    }
    pub_date = meta.get("publishedDate") or meta.get("ogDate") or meta.get("date")
    if pub_date:
        response["published_date"] = pub_date
    if truncated:
        response["content_truncated"] = True

    _cache.set(cache_key, response)
    return response


@mcp.tool()
async def get_nhs_treatment_guidance(url: str) -> dict:
    """Scrape a specific NHS URL for treatment guidance.

    The URL must be on ``www.nhs.uk``.  Returns the page content as
    markdown with metadata.

    Args:
        url: Full HTTPS URL on www.nhs.uk to scrape.

    Returns:
        Dictionary with ``url``, ``title``, ``content_markdown``,
        ``source``, and optional ``content_truncated`` flag.
    """
    validated = validate_allowed_url(url, _NHS_NETLOC_PATTERN)
    if validated is None:
        return {"error": "URL must be https://www.nhs.uk/..."}

    if not is_configured():
        return _not_configured_response(validated)

    cache_key = f"guidance:{validated}"
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

    # Post-scrape redirect protection: verify the response URL is still NHS
    source_url = (
        result.get("metadata", {}).get("sourceURL", "")
        or result.get("sourceURL", "")
    )
    if source_url and validate_allowed_url(source_url, _NHS_NETLOC_PATTERN) is None:
        return {
            "error": "Page redirected outside nhs.uk — response rejected for security",
            "url": validated,
            "redirect_target": source_url,
        }

    markdown = result.get("markdown", "") or ""
    content, truncated = _truncate_at_sentence(markdown, MAX_CONTENT_LENGTH)

    meta = result.get("metadata", {})
    response = {
        "url": validated,
        "title": meta.get("title", result.get("title", "")),
        "content_markdown": content,
        "source": "NHS UK",
    }
    pub_date = meta.get("publishedDate") or meta.get("ogDate") or meta.get("date")
    if pub_date:
        response["published_date"] = pub_date
    if truncated:
        response["content_truncated"] = True

    _cache.set(cache_key, response)
    return response


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9016)
