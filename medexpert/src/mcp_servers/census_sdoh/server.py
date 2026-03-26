"""Census SDOH MCP Server (Social Determinants of Health).

Provides US Census American Community Survey (ACS) data for social
determinants of health indicators including poverty rates, insurance
coverage, education levels, and housing metrics. Used by the
EpidemiologySpecialist and EnvironmentalSpecialist agents.

Port: 9009
"""

import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get, CircuitOpenError, RetryExhaustedError, raise_or_return_error
from mcp_servers._security import sanitize_query
from lifesci_common.constants import CENSUS_ACS_URL

log = logging.getLogger(__name__)

mcp = FastMCP("Census SDOH")

CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "")

# ACS variable codes for SDOH indicators
SDOH_VARIABLES = {
    # Poverty
    "total_population": "B01003_001E",
    "poverty_total": "B17001_001E",
    "poverty_below": "B17001_002E",
    # Insurance
    "insured_total": "B27001_001E",
    "insured_yes": "B27001_002E",
    "uninsured": "B27001_005E",
    # Education
    "education_total": "B15003_001E",
    "education_bachelors": "B15003_022E",
    "education_graduate": "B15003_023E",
    # Income
    "median_household_income": "B19013_001E",
    # Housing
    "housing_total": "B25001_001E",
    "housing_occupied": "B25002_002E",
    "housing_vacant": "B25002_003E",
}

# Default ACS year (5-year estimates)
DEFAULT_ACS_YEAR = "2022"
ACS_DATASET = "acs/acs5"


def _build_census_url(year: str) -> str:
    """Build the Census ACS API URL for a given year."""
    return f"{CENSUS_ACS_URL}/{year}/{ACS_DATASET}"


def _add_api_key(params: dict) -> dict:
    """Add the Census API key to params if available."""
    if CENSUS_API_KEY:
        params["key"] = CENSUS_API_KEY
    return params


@mcp.tool()
async def get_sdoh_indicators(
    state_fips: str,
    year: int | None = None,
) -> dict:
    """Retrieve social determinants of health indicators for a US state.

    Fetches poverty, insurance, education, income, and housing
    indicators from the US Census ACS 5-year estimates.

    Args:
        state_fips: Two-digit FIPS state code (e.g., "06" for California, "36" for New York).
        year: ACS data year (default: 2022). Available: 2009-2022.

    Returns:
        Dictionary with SDOH indicators and metadata.
    """
    safe_state = sanitize_query(state_fips, max_len=2)
    if not safe_state or not safe_state.isdigit() or len(safe_state) != 2:
        return {"error": "Invalid state_fips. Must be a 2-digit FIPS code (e.g., '06')."}

    query_year = str(year) if year else DEFAULT_ACS_YEAR
    url = _build_census_url(query_year)

    variables = ",".join(SDOH_VARIABLES.values())
    params = _add_api_key({
        "get": variables,
        "for": f"state:{safe_state}",
    })

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            # Census API returns [[header_row], [data_row], ...]
            if len(data) < 2:
                return {
                    "state_fips": safe_state,
                    "year": query_year,
                    "error": "No data returned for this state/year combination.",
                    "indicators": {},
                }

            headers = data[0]
            values = data[1]
            raw = dict(zip(headers, values))

            # Build structured SDOH indicators
            indicators = {}
            for name, code in SDOH_VARIABLES.items():
                val = raw.get(code)
                if val is not None:
                    try:
                        indicators[name] = int(val)
                    except (ValueError, TypeError):
                        indicators[name] = val

            # Calculate derived rates
            if indicators.get("poverty_total") and indicators.get("poverty_below"):
                total = indicators["poverty_total"]
                below = indicators["poverty_below"]
                if total > 0:
                    indicators["poverty_rate_pct"] = round(below / total * 100, 1)

            return {
                "success": True,
                "state_fips": safe_state,
                "year": query_year,
                "indicators": indicators,
                "source": "US Census ACS 5-Year Estimates",
            }
        elif resp.status_code == 204:
            return {
                "state_fips": safe_state,
                "year": query_year,
                "error": "No data available for this state/year.",
                "indicators": {},
            }
        else:
            return {
                "error": f"Census API returned status {resp.status_code}",
                "indicators": {},
            }

    except (CircuitOpenError, RetryExhaustedError) as exc:
        return raise_or_return_error(exc, "census_sdoh", "get_sdoh_indicators", indicators={})
    except Exception as exc:
        log.error("Census SDOH query failed: %s", exc)
        return {"error": str(exc), "indicators": {}}


@mcp.tool()
async def get_poverty_data(
    state_fips: str,
    county_fips: str | None = None,
) -> dict:
    """Retrieve poverty data for a US state or county.

    Fetches poverty population counts and rates from the US Census
    ACS 5-year estimates, optionally at county level.

    Args:
        state_fips: Two-digit FIPS state code (e.g., "06").
        county_fips: Optional three-digit FIPS county code (e.g., "037").

    Returns:
        Dictionary with poverty statistics.
    """
    safe_state = sanitize_query(state_fips, max_len=2)
    if not safe_state or not safe_state.isdigit() or len(safe_state) != 2:
        return {"error": "Invalid state_fips. Must be a 2-digit FIPS code."}

    if county_fips:
        safe_county = sanitize_query(county_fips, max_len=3)
        if not safe_county or not safe_county.isdigit():
            return {"error": "Invalid county_fips. Must be a 3-digit FIPS code."}
    else:
        safe_county = None

    url = _build_census_url(DEFAULT_ACS_YEAR)
    poverty_vars = f"{SDOH_VARIABLES['poverty_total']},{SDOH_VARIABLES['poverty_below']},{SDOH_VARIABLES['total_population']}"

    if safe_county:
        geo = f"county:{safe_county}"
        in_clause = f"state:{safe_state}"
        params = _add_api_key({
            "get": poverty_vars,
            "for": geo,
            "in": in_clause,
        })
    else:
        params = _add_api_key({
            "get": poverty_vars,
            "for": f"state:{safe_state}",
        })

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            if len(data) < 2:
                return {
                    "state_fips": safe_state,
                    "county_fips": safe_county,
                    "error": "No poverty data available.",
                }

            headers = data[0]
            results = []
            for row in data[1:]:
                raw = dict(zip(headers, row))
                total = int(raw.get(SDOH_VARIABLES["poverty_total"], 0) or 0)
                below = int(raw.get(SDOH_VARIABLES["poverty_below"], 0) or 0)
                population = int(raw.get(SDOH_VARIABLES["total_population"], 0) or 0)
                rate = round(below / total * 100, 1) if total > 0 else 0.0
                results.append({
                    "total_assessed": total,
                    "below_poverty": below,
                    "poverty_rate_pct": rate,
                    "total_population": population,
                    "state": raw.get("state", safe_state),
                    "county": raw.get("county", safe_county or ""),
                })

            return {
                "success": True,
                "state_fips": safe_state,
                "county_fips": safe_county,
                "year": DEFAULT_ACS_YEAR,
                "results": results,
                "source": "US Census ACS 5-Year Estimates",
            }
        else:
            return {
                "error": f"Census API returned status {resp.status_code}",
                "results": [],
            }

    except (CircuitOpenError, RetryExhaustedError) as exc:
        return raise_or_return_error(exc, "census_sdoh", "get_poverty_data", results=[])
    except Exception as exc:
        log.error("Census poverty query failed: %s", exc)
        return {"error": str(exc), "results": []}


@mcp.tool()
async def get_insurance_data(state_fips: str) -> dict:
    """Retrieve health insurance coverage data for a US state.

    Fetches insurance coverage statistics from the US Census ACS
    5-year estimates.

    Args:
        state_fips: Two-digit FIPS state code (e.g., "06").

    Returns:
        Dictionary with insurance coverage statistics.
    """
    safe_state = sanitize_query(state_fips, max_len=2)
    if not safe_state or not safe_state.isdigit() or len(safe_state) != 2:
        return {"error": "Invalid state_fips. Must be a 2-digit FIPS code."}

    url = _build_census_url(DEFAULT_ACS_YEAR)
    insurance_vars = (
        f"{SDOH_VARIABLES['insured_total']},"
        f"{SDOH_VARIABLES['insured_yes']},"
        f"{SDOH_VARIABLES['uninsured']},"
        f"{SDOH_VARIABLES['total_population']}"
    )
    params = _add_api_key({
        "get": insurance_vars,
        "for": f"state:{safe_state}",
    })

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            if len(data) < 2:
                return {
                    "state_fips": safe_state,
                    "error": "No insurance data available.",
                }

            headers = data[0]
            values = data[1]
            raw = dict(zip(headers, values))

            total = int(raw.get(SDOH_VARIABLES["insured_total"], 0) or 0)
            insured = int(raw.get(SDOH_VARIABLES["insured_yes"], 0) or 0)
            uninsured = int(raw.get(SDOH_VARIABLES["uninsured"], 0) or 0)
            population = int(raw.get(SDOH_VARIABLES["total_population"], 0) or 0)

            insured_rate = round(insured / total * 100, 1) if total > 0 else 0.0
            uninsured_rate = round(uninsured / total * 100, 1) if total > 0 else 0.0

            return {
                "success": True,
                "state_fips": safe_state,
                "year": DEFAULT_ACS_YEAR,
                "total_assessed": total,
                "insured": insured,
                "uninsured": uninsured,
                "insured_rate_pct": insured_rate,
                "uninsured_rate_pct": uninsured_rate,
                "total_population": population,
                "source": "US Census ACS 5-Year Estimates",
            }
        else:
            return {
                "error": f"Census API returned status {resp.status_code}",
            }

    except (CircuitOpenError, RetryExhaustedError) as exc:
        return raise_or_return_error(exc, "census_sdoh", "get_insurance_data")
    except Exception as exc:
        log.error("Census insurance query failed: %s", exc)
        return {"error": str(exc)}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9009)
