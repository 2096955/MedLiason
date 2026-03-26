"""Web search DynamicTool for orchestrator pre-search.

Primary: Firecrawl search API (requires ``FIRECRAWL_API_KEY``).
Fallback: Brave Search API (requires ``BRAVE_SEARCH_API_KEY``).

Both are already available on Cloud Run.  DDG and Google CSE are
unreliable from the deployment environment so are not used here.
"""

import logging
import os
from typing import Optional

import httpx
from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

log = logging.getLogger(__name__)

BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")


# ---------------------------------------------------------------------------
# Brave Search fallback
# ---------------------------------------------------------------------------

async def _brave_search(query: str, max_results: int) -> list[dict]:
    """Call Brave Web Search API.  Returns list of {title, url, description}."""
    if not BRAVE_SEARCH_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={
                    "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "description": r.get("description", ""),
                }
                for r in data.get("web", {}).get("results", [])[:max_results]
            ]
    except Exception as exc:
        log.warning("[brave_search] fallback failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------

def _format_results(results: list[dict], max_results: int) -> dict:
    formatted = []
    for i, r in enumerate(results[:max_results]):
        formatted.append(
            f"[{i+1}] {r.get('title', 'No title')}\n"
            f"    URL: {r.get('url', '')}\n"
            f"    {r.get('description', r.get('snippet', ''))}"
        )
    return {
        "success": True,
        "num_results": len(formatted),
        "results": "\n\n".join(formatted) if formatted else "No results found.",
        "raw": results[:max_results],
    }


# ---------------------------------------------------------------------------
# DynamicTool
# ---------------------------------------------------------------------------

class WebSearchFirecrawlTool(DynamicTool):
    tool_name = "web_search_firecrawl"
    tool_description = (
        "Search the open web using Firecrawl (with Brave fallback). Returns "
        "titles, URLs, and descriptions for up to 5 results. Use this to "
        "identify unknown drug names, brand names, or medical terms before "
        "routing to specialists."
    )
    parameters_schema = adk_types.Schema(
        type=adk_types.Type.OBJECT,
        properties={
            "query": adk_types.Schema(
                type=adk_types.Type.STRING,
                description="The search query (e.g., 'Arcoxia drug')",
            ),
            "max_results": adk_types.Schema(
                type=adk_types.Type.INTEGER,
                description="Maximum results to return (1-10, default 5)",
            ),
        },
        required=["query"],
    )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict:
        query = args.get("query", "")
        max_results = min(max(int(args.get("max_results", 5) or 5), 1), 10)
        log.info("[web_search_firecrawl] query=%r max_results=%d", query, max_results)

        if not query:
            return {"success": False, "error": "No query provided"}

        # --- Primary: Firecrawl ---
        try:
            from mcp_servers._firecrawl import firecrawl_search, is_configured
        except ImportError:
            is_configured = lambda: False  # noqa: E731

        if is_configured():
            try:
                results = await firecrawl_search(
                    query, params={"limit": max_results}
                )
                if isinstance(results, dict) and not results.get("success", True):
                    log.warning("[web_search_firecrawl] Firecrawl returned error, trying Brave")
                elif isinstance(results, list) and results:
                    log.info("[web_search_firecrawl] Firecrawl OK: %d results", len(results))
                    return _format_results(results, max_results)
            except Exception as exc:
                log.warning("[web_search_firecrawl] Firecrawl failed: %s — trying Brave", exc)

        # --- Fallback: Brave Search ---
        brave_results = await _brave_search(query, max_results)
        if brave_results:
            log.info("[web_search_firecrawl] Brave fallback OK: %d results", len(brave_results))
            return _format_results(brave_results, max_results)

        # --- Both failed ---
        return {
            "success": False,
            "error": "Web search unavailable (Firecrawl + Brave both failed)",
            "results": "No results found.",
        }
