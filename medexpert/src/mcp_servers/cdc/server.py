"""CDC SODA Open Data MCP server.

Provides tools for querying CDC public health datasets including disease
surveillance, mortality, and vaccination data via the SODA (Socrata) API.

API: CDC SODA Open Data -- JSON responses, free.
CRITICAL SECURITY: All $where clause values MUST use escape_soql()
to prevent SoQL injection.
"""

import os
import sys
import logging

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get, CircuitOpenError, RetryExhaustedError, structured_error_response
from mcp_servers._security import sanitize_query, escape_soql
from lifesci_common.constants import CDC_WONDER_BASE_URL

log = logging.getLogger(__name__)

mcp = FastMCP("cdc")

# Known CDC SODA dataset IDs
DATASET_IDS = {
    "mortality": "bi63-dtpu",
    "vaccinations": "rqy8-7eub",
    "nndss": "r8kw-7aab",  # National Notifiable Diseases Surveillance System
    "wonder_mortality": "bi63-dtpu",
}

# SODA API app token (optional, increases rate limit)
CDC_APP_TOKEN = os.environ.get("CDC_APP_TOKEN")


def _build_url(dataset_id: str) -> str:
    """Build full URL for a CDC SODA dataset."""
    return f"{CDC_WONDER_BASE_URL}/{dataset_id}.json"


def _base_params(limit: int) -> dict:
    """Return base SODA API params."""
    params = {"$limit": str(limit)}
    if CDC_APP_TOKEN:
        params["$$app_token"] = CDC_APP_TOKEN
    return params


def _base_headers() -> dict:
    """Return base headers for CDC SODA requests."""
    headers = {"Accept": "application/json"}
    if CDC_APP_TOKEN:
        headers["X-App-Token"] = CDC_APP_TOKEN
    return headers


@mcp.tool()
async def search_disease_data(disease: str, max_results: int = 20) -> dict:
    """Search CDC National Notifiable Diseases Surveillance System (NNDSS) data.

    Returns weekly disease surveillance reports for the specified disease.
    Data includes case counts by jurisdiction and reporting period.
    """
    safe_disease = sanitize_query(disease)
    if not safe_disease:
        return {"error": "Empty disease query after sanitization", "results": []}

    # SECURITY: escape_soql on all values used in $where
    escaped_disease = escape_soql(safe_disease)

    url = _build_url(DATASET_IDS["nndss"])
    params = _base_params(max_results)
    params["$where"] = f"upper(disease) like upper('%{escaped_disease}%')"
    params["$order"] = "mmwr_year DESC, mmwr_week DESC"

    try:
        response = await resilient_get(url, params=params, headers=_base_headers())
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "cdc", "search_disease_data"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"CDC SODA API returned {response.status_code}",
            "results": [],
        }

    data = response.json()
    results = []
    for record in data:
        results.append({
            "disease": record.get("disease", ""),
            "mmwr_year": record.get("mmwr_year", ""),
            "mmwr_week": record.get("mmwr_week", ""),
            "current_week": record.get("current_week", ""),
            "cum_2024": record.get("cum_2024", record.get("cumulative_ytd_current_mmwr_year", "")),
            "reporting_area": record.get("reporting_area", ""),
        })

    return {
        "success": True,
        "disease": safe_disease,
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def get_mortality_data(
    cause: str, year: int | None = None, max_results: int = 20
) -> dict:
    """Query CDC mortality statistics by cause of death.

    Returns age-adjusted death rates and counts by cause, optionally
    filtered by year. Uses the CDC WONDER mortality dataset.
    """
    safe_cause = sanitize_query(cause)
    if not safe_cause:
        return {"error": "Empty cause query after sanitization", "results": []}

    # SECURITY: escape_soql on all values used in $where
    escaped_cause = escape_soql(safe_cause)

    url = _build_url(DATASET_IDS["mortality"])
    params = _base_params(max_results)

    where_clause = f"upper(cause_name) like upper('%{escaped_cause}%')"
    if year is not None:
        where_clause += f" AND year = '{escape_soql(str(year))}'"

    params["$where"] = where_clause
    params["$order"] = "year DESC"

    try:
        response = await resilient_get(url, params=params, headers=_base_headers())
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "cdc", "get_mortality_data"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"CDC mortality API returned {response.status_code}",
            "results": [],
        }

    data = response.json()
    results = []
    for record in data:
        results.append({
            "cause_name": record.get("cause_name", ""),
            "state": record.get("state", ""),
            "year": record.get("year", ""),
            "deaths": record.get("deaths", ""),
            "age_adjusted_death_rate": record.get("aadr", record.get("age_adjusted_death_rate", "")),
        })

    return {
        "success": True,
        "cause": safe_cause,
        "year": year,
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def get_vaccination_data(
    vaccine: str | None = None, max_results: int = 20
) -> dict:
    """Query CDC vaccination coverage data.

    Returns vaccination rates and coverage statistics. Optionally filter
    by specific vaccine type.
    """
    url = _build_url(DATASET_IDS["vaccinations"])
    params = _base_params(max_results)

    if vaccine:
        safe_vaccine = sanitize_query(vaccine)
        if not safe_vaccine:
            return {"error": "Empty vaccine query after sanitization", "results": []}
        # SECURITY: escape_soql on all values used in $where
        escaped_vaccine = escape_soql(safe_vaccine)
        params["$where"] = f"upper(vaccine) like upper('%{escaped_vaccine}%')"

    params["$order"] = "year_season DESC"

    try:
        response = await resilient_get(url, params=params, headers=_base_headers())
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "cdc", "get_vaccination_data"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"CDC vaccination API returned {response.status_code}",
            "results": [],
        }

    data = response.json()
    results = []
    for record in data:
        results.append({
            "vaccine": record.get("vaccine", ""),
            "dose": record.get("dose", ""),
            "year_season": record.get("year_season", ""),
            "geography_type": record.get("geography_type", ""),
            "geography": record.get("geography", ""),
            "dimension_type": record.get("dimension_type", ""),
            "dimension": record.get("dimension", ""),
            "estimate": record.get("estimate_pct", record.get("estimate", "")),
        })

    return {
        "success": True,
        "vaccine": vaccine,
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def get_surveillance_data(condition: str, max_results: int = 20) -> dict:
    """Query CDC surveillance data for notifiable conditions.

    Returns surveillance reports including case counts, incidence rates,
    and temporal trends for specified conditions.
    """
    safe_condition = sanitize_query(condition)
    if not safe_condition:
        return {"error": "Empty condition query after sanitization", "results": []}

    # SECURITY: escape_soql on all values used in $where
    escaped_condition = escape_soql(safe_condition)

    url = _build_url(DATASET_IDS["nndss"])
    params = _base_params(max_results)
    params["$where"] = f"upper(disease) like upper('%{escaped_condition}%')"
    params["$order"] = "mmwr_year DESC, mmwr_week DESC"
    params["$select"] = (
        "disease, reporting_area, mmwr_year, mmwr_week, "
        "current_week, previous_52_week_max, cumulative_ytd_current_mmwr_year"
    )

    try:
        response = await resilient_get(url, params=params, headers=_base_headers())
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "cdc", "get_surveillance_data"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"CDC surveillance API returned {response.status_code}",
            "results": [],
        }

    data = response.json()
    results = []
    for record in data:
        results.append({
            "disease": record.get("disease", ""),
            "reporting_area": record.get("reporting_area", ""),
            "mmwr_year": record.get("mmwr_year", ""),
            "mmwr_week": record.get("mmwr_week", ""),
            "current_week_cases": record.get("current_week", ""),
            "previous_52_week_max": record.get("previous_52_week_max", ""),
            "cumulative_ytd": record.get("cumulative_ytd_current_mmwr_year", ""),
        })

    return {
        "success": True,
        "condition": safe_condition,
        "returned_count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9004)
