"""SEER / NCI Cancer Statistics MCP Server.

Provides public cancer statistics via CDC SODA datasets that mirror
SEER-style incidence and mortality data. Uses resilient HTTP with
exponential backoff for all API calls.

Port: 9006
"""

import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get
from mcp_servers._security import sanitize_query, escape_soql
from lifesci_common.constants import SEER_API_URL

log = logging.getLogger(__name__)

mcp = FastMCP("SEER Cancer Statistics")

# CDC SODA dataset identifiers for cancer statistics
# United States Cancer Statistics (USCS) — Incidence
USCS_INCIDENCE_DATASET = "9kva-bt6k"
# United States Cancer Statistics (USCS) — Mortality
USCS_MORTALITY_DATASET = "9dgy-fxgq"


def _build_soda_url(dataset_id: str) -> str:
    """Build a CDC SODA API URL for the given dataset."""
    return f"{SEER_API_URL}/{dataset_id}.json"


def _parse_cancer_record(record: dict) -> dict:
    """Normalize a raw CDC SODA cancer record into a structured result."""
    return {
        "cancer_site": record.get("leading_cancer_sites", record.get("site", "")),
        "year": record.get("year", ""),
        "sex": record.get("sex", ""),
        "race": record.get("race", ""),
        "count": record.get("count", record.get("case_count", "")),
        "population": record.get("population", ""),
        "crude_rate": record.get("crude_rate", record.get("age_adjusted_rate", "")),
        "age_adjusted_rate": record.get("age_adjusted_rate", ""),
    }


@mcp.tool()
async def get_cancer_statistics(
    cancer_type: str,
    max_results: int = 20,
) -> dict:
    """Retrieve public cancer statistics for a given cancer type.

    Searches CDC USCS incidence and mortality datasets for statistics
    matching the specified cancer type. Returns aggregated counts,
    crude rates, and age-adjusted rates.

    Args:
        cancer_type: Cancer type to search (e.g., "lung", "breast", "colorectal").
        max_results: Maximum number of records to return (default 20).

    Returns:
        Dictionary with incidence and mortality data arrays.
    """
    safe_cancer = escape_soql(sanitize_query(cancer_type))
    if not safe_cancer:
        return {"error": "Invalid cancer type parameter", "results": []}

    incidence_url = _build_soda_url(USCS_INCIDENCE_DATASET)
    mortality_url = _build_soda_url(USCS_MORTALITY_DATASET)

    incidence_params = {
        "$where": f"upper(leading_cancer_sites) like upper('%{safe_cancer}%')",
        "$limit": str(min(max_results, 100)),
        "$order": "year DESC",
    }
    mortality_params = {
        "$where": f"upper(leading_cancer_sites) like upper('%{safe_cancer}%')",
        "$limit": str(min(max_results, 100)),
        "$order": "year DESC",
    }

    incidence_results = []
    mortality_results = []

    try:
        resp = await resilient_get(incidence_url, params=incidence_params)
        if resp.status_code == 200:
            incidence_results = [_parse_cancer_record(r) for r in resp.json()]
        else:
            log.warning("SEER incidence API returned %d", resp.status_code)
    except Exception as exc:
        log.error("Failed to fetch incidence data: %s", exc)

    try:
        resp = await resilient_get(mortality_url, params=mortality_params)
        if resp.status_code == 200:
            mortality_results = [_parse_cancer_record(r) for r in resp.json()]
        else:
            log.warning("SEER mortality API returned %d", resp.status_code)
    except Exception as exc:
        log.error("Failed to fetch mortality data: %s", exc)

    return {
        "cancer_type": cancer_type,
        "incidence": incidence_results,
        "mortality": mortality_results,
        "total_incidence_records": len(incidence_results),
        "total_mortality_records": len(mortality_results),
    }


@mcp.tool()
async def search_cancer_incidence(
    cancer_type: str,
    year: int | None = None,
    max_results: int = 20,
) -> dict:
    """Search cancer incidence data with optional year filter.

    Queries the CDC USCS incidence dataset for cancer incidence
    statistics, optionally filtering by year.

    Args:
        cancer_type: Cancer type to search (e.g., "lung", "breast").
        year: Optional year to filter results (e.g., 2020).
        max_results: Maximum number of records to return (default 20).

    Returns:
        Dictionary with incidence records and metadata.
    """
    safe_cancer = escape_soql(sanitize_query(cancer_type))
    if not safe_cancer:
        return {"error": "Invalid cancer type parameter", "results": []}

    where_clause = f"upper(leading_cancer_sites) like upper('%{safe_cancer}%')"
    if year is not None:
        escaped_year = escape_soql(str(year))
        where_clause += f" AND year = '{escaped_year}'"

    url = _build_soda_url(USCS_INCIDENCE_DATASET)
    params = {
        "$where": where_clause,
        "$limit": str(min(max_results, 100)),
        "$order": "year DESC",
    }

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            records = [_parse_cancer_record(r) for r in resp.json()]
            return {
                "cancer_type": cancer_type,
                "year_filter": year,
                "results": records,
                "total_results": len(records),
            }
        else:
            return {
                "error": f"API returned status {resp.status_code}",
                "results": [],
            }
    except Exception as exc:
        log.error("Failed to search cancer incidence: %s", exc)
        return {
            "error": str(exc),
            "results": [],
        }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9006)
