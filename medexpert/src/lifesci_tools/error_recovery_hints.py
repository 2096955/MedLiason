"""Error recovery hints — maps MCP failure patterns to user-actionable suggestions.

Used by source_collector to attach recovery_hint to each mcp_failure entry,
and surfaced in the frontend RAGInfoPanel and ResearchInconclusivePanel.
"""

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# (server_name, error_category) → hint text.
# Use "*" as server wildcard to match any server with that error category.
_RECOVERY_HINTS: Dict[Tuple[str, str], str] = {
    # Server-specific hints
    ("openfda", "rate_limited"): (
        "OpenFDA has a 40 req/min limit per IP. "
        "Try again in 60 seconds or narrow your drug query to a single active ingredient."
    ),
    ("pubmed", "rate_limited"): (
        "PubMed rate limit hit. Set NCBI_API_KEY in your .env file "
        "to increase from 3 req/s to 10 req/s."
    ),
    ("clinicaltrials", "rate_limited"): (
        "ClinicalTrials.gov rate limit hit. "
        "Try narrowing your search to a specific condition or intervention."
    ),
    ("clinicaltrials", "service_unavailable"): (
        "ClinicalTrials.gov is temporarily down. "
        "Trial data may also be available via PubMed literature search."
    ),
    ("pubmed", "service_unavailable"): (
        "PubMed/NCBI is temporarily unavailable. "
        "Try again in a few minutes — NCBI outages are usually brief."
    ),
    ("openfda", "service_unavailable"): (
        "OpenFDA API is temporarily unavailable. "
        "Drug label and adverse event data may be available through PubMed."
    ),
    ("regulatory", "auth_error"): (
        "Regulatory API authentication failed. "
        "Verify API credentials in your .env configuration."
    ),
    ("environmental", "service_unavailable"): (
        "EPA environmental data is temporarily unavailable. "
        "CDC may have related exposure and health data."
    ),
    ("seer", "service_unavailable"): (
        "SEER cancer statistics are temporarily unavailable. "
        "Try PubMed for published epidemiology studies instead."
    ),
    ("genomic", "service_unavailable"): (
        "ClinVar/genomic data is temporarily unavailable. "
        "PubMed indexes many genomic studies as an alternative."
    ),
    ("census_sdoh", "rate_limited"): (
        "Census Bureau rate limit hit. "
        "SDOH data queries may be available through CDC WONDER."
    ),
    ("knowledge_graph", "service_unavailable"): (
        "Knowledge graph (Memgraph) is not running. "
        "KG search results will be skipped — research continues with live sources."
    ),
    ("knowledge_graph", "circuit_open"): (
        "Knowledge graph circuit breaker open. "
        "KG search skipped — research continues with live sources."
    ),
    # Wildcard hints (match any server)
    ("*", "circuit_open"): (
        "This data source is temporarily unavailable (circuit breaker triggered "
        "after repeated failures). It will automatically retry in 60 seconds."
    ),
    ("*", "rate_limited"): (
        "This data source is rate-limited. "
        "Try again in 60 seconds or simplify your query."
    ),
    ("*", "auth_error"): (
        "Authentication failed for this data source. "
        "Check API key configuration in medexpert/.env."
    ),
    ("*", "service_unavailable"): (
        "This data source is temporarily down. "
        "Try again in a few minutes."
    ),
    ("*", "network_error"): (
        "Could not connect to this data source. "
        "Check network connectivity and try again."
    ),
    ("*", "api_error"): (
        "Unexpected error from this data source. "
        "Try simplifying your query or rephrasing your question."
    ),
    ("*", "not_found"): (
        "This data source returned no results for your query. "
        "Try broadening your search terms."
    ),
    ("*", "validation_error"): (
        "Invalid input for this data source. "
        "Check query parameters — a required field may be missing or malformed."
    ),
    ("*", "business_error"): (
        "This query was rejected by the data source's policy rules. "
        "Try rephrasing without personal identifiers or restricted terms."
    ),
}


def get_recovery_hint(server: str, error_category: str) -> Optional[str]:
    """Look up a recovery hint for a given server + error category.

    Tries server-specific match first, then wildcard.
    Returns None if no hint is available.
    """
    # Try exact match first
    hint = _RECOVERY_HINTS.get((server, error_category))
    if hint:
        return hint

    # Try wildcard match
    return _RECOVERY_HINTS.get(("*", error_category))


def enrich_mcp_failures(mcp_failures: list) -> list:
    """Add recovery_hint field to each MCP failure dict in-place and return the list."""
    for failure in mcp_failures:
        if not isinstance(failure, dict):
            continue
        server = failure.get("server", "")
        category = failure.get("error_category", "")
        hint = get_recovery_hint(server, category)
        if hint:
            failure["recovery_hint"] = hint
    return mcp_failures
