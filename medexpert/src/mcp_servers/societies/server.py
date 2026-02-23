"""Medical Societies Guidelines MCP Server — Honest Degradation.

Provides access to clinical practice guidelines from medical societies
(ASCO, AHA, WHO, etc.) when configured scrapers or API endpoints are
available. Returns structured "not_implemented" responses with fallback
search queries when no scraper is configured.

NEVER fabricates API URLs. Societies without configured endpoints
return honest degradation responses.

Port: 9012
"""

import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get
from mcp_servers._security import sanitize_query

log = logging.getLogger(__name__)

mcp = FastMCP("Medical Societies Guidelines")

# Society configurations from environment variables.
# Each society needs a configured API URL or scraper endpoint.
SOCIETY_CONFIGS = {
    "asco": {
        "name": "American Society of Clinical Oncology",
        "url_env": "SOCIETY_ASCO_API_URL",
        "site": "asco.org",
    },
    "aha": {
        "name": "American Heart Association",
        "url_env": "SOCIETY_AHA_API_URL",
        "site": "heart.org",
    },
    "who": {
        "name": "World Health Organization",
        "url_env": "SOCIETY_WHO_API_URL",
        "site": "who.int",
    },
    "nice": {
        "name": "National Institute for Health and Care Excellence",
        "url_env": "SOCIETY_NICE_API_URL",
        "site": "nice.org.uk",
    },
    "nccn": {
        "name": "National Comprehensive Cancer Network",
        "url_env": "SOCIETY_NCCN_API_URL",
        "site": "nccn.org",
    },
    "acc": {
        "name": "American College of Cardiology",
        "url_env": "SOCIETY_ACC_API_URL",
        "site": "acc.org",
    },
    "idsa": {
        "name": "Infectious Diseases Society of America",
        "url_env": "SOCIETY_IDSA_API_URL",
        "site": "idsociety.org",
    },
    "ada": {
        "name": "American Diabetes Association",
        "url_env": "SOCIETY_ADA_API_URL",
        "site": "diabetes.org",
    },
}


def _get_configured_societies() -> dict[str, str]:
    """Return a dict of society_key -> api_url for configured societies."""
    configured = {}
    for key, config in SOCIETY_CONFIGS.items():
        url = os.environ.get(config["url_env"], "")
        if url:
            configured[key] = url
    return configured


def _not_implemented_response(
    society: str,
    query: str,
) -> dict:
    """Return a structured not_implemented response with fallback search query."""
    config = SOCIETY_CONFIGS.get(society.lower())
    configured = _get_configured_societies()

    if config:
        site_url = config["site"]
        fallback = f"{query} site:{site_url}"
        society_name = config["name"]
    else:
        fallback = f"{query} clinical practice guidelines"
        society_name = society

    return {
        "status": "not_implemented",
        "reason": f"No scraper configured for {society_name}",
        "society": society_name,
        "fallback_search_query": fallback,
        "available_societies": list(configured.keys()),
    }


@mcp.tool()
async def search_society_guidelines(
    society: str,
    query: str,
) -> dict:
    """Search clinical practice guidelines from a medical society.

    Queries configured society API endpoints or scrapers for guidelines
    matching the search query. If the society has no configured endpoint,
    returns a structured response with a fallback search query.

    Args:
        society: Society identifier (e.g., "asco", "aha", "who", "nice").
        query: Search query for guidelines (e.g., "breast cancer screening").

    Returns:
        Dictionary with guideline results or not_implemented response.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Invalid query parameter"}

    safe_society = sanitize_query(society, max_len=20).lower()
    configured = _get_configured_societies()

    if safe_society not in configured:
        return _not_implemented_response(safe_society, safe_query)

    # Society has a configured endpoint — query it
    api_url = configured[safe_society]

    try:
        resp = await resilient_get(
            f"{api_url}/search",
            params={"q": safe_query, "limit": "10"},
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data if isinstance(data, list) else data.get("results", [])
            return {
                "society": safe_society,
                "query": safe_query,
                "results": results,
                "total_results": len(results),
                "source": SOCIETY_CONFIGS[safe_society]["name"],
            }
        else:
            log.warning(
                "Society %s API returned %d", safe_society, resp.status_code
            )
            return _not_implemented_response(safe_society, safe_query)

    except Exception as exc:
        log.error("Society guidelines search failed: %s", exc)
        return _not_implemented_response(safe_society, safe_query)


@mcp.tool()
async def get_guideline_recommendations(
    society: str,
    topic: str,
) -> dict:
    """Get specific guideline recommendations from a medical society.

    Retrieves recommendation statements and evidence grades for a
    clinical topic from a configured society endpoint.

    Args:
        society: Society identifier (e.g., "asco", "aha", "who").
        topic: Clinical topic (e.g., "hypertension management", "lung cancer screening").

    Returns:
        Dictionary with recommendations or not_implemented response.
    """
    safe_topic = sanitize_query(topic)
    if not safe_topic:
        return {"error": "Invalid topic parameter"}

    safe_society = sanitize_query(society, max_len=20).lower()
    configured = _get_configured_societies()

    if safe_society not in configured:
        return _not_implemented_response(safe_society, safe_topic)

    api_url = configured[safe_society]

    try:
        resp = await resilient_get(
            f"{api_url}/recommendations",
            params={"topic": safe_topic, "limit": "10"},
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data if isinstance(data, list) else data.get("recommendations", [])
            return {
                "society": safe_society,
                "topic": safe_topic,
                "recommendations": results,
                "total_recommendations": len(results),
                "source": SOCIETY_CONFIGS[safe_society]["name"],
            }
        else:
            log.warning(
                "Society %s recommendations API returned %d",
                safe_society,
                resp.status_code,
            )
            return _not_implemented_response(safe_society, safe_topic)

    except Exception as exc:
        log.error("Society recommendations lookup failed: %s", exc)
        return _not_implemented_response(safe_society, safe_topic)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9012)
