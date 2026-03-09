"""Environmental Health MCP Server (EPA AQS / EJScreen).

Provides air quality data from EPA Air Quality System (AQS) and
environmental justice screening data from EJScreen. Used by the
EnvironmentalSpecialist agent for exposure and environmental risk
assessments.

Port: 9008
"""

import json
import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get, CircuitOpenError, RetryExhaustedError, raise_or_return_error
from mcp_servers._security import sanitize_query
from lifesci_common.constants import EPA_AQS_URL, EPA_EJSCREEN_URL

log = logging.getLogger(__name__)

mcp = FastMCP("Environmental Health")

# EPA AQS requires registration; fall back to sample data endpoint if no key
EPA_AQS_EMAIL = os.environ.get("EPA_AQS_EMAIL", "")
EPA_AQS_KEY = os.environ.get("EPA_AQS_KEY", "")

# Common AQI parameter codes
AQI_PARAM_CODES = {
    "ozone": "44201",
    "pm25": "88101",
    "pm10": "81102",
    "co": "42101",
    "so2": "42401",
    "no2": "42602",
    "lead": "14129",
}


@mcp.tool()
async def get_air_quality(
    state_code: str,
    county_code: str | None = None,
    year: int | None = None,
) -> dict:
    """Retrieve air quality data for a US state or county.

    Queries the EPA AQS API for annual summary air quality data.
    Requires EPA_AQS_EMAIL and EPA_AQS_KEY environment variables
    for authenticated access; returns an informative message if
    credentials are not configured.

    Args:
        state_code: Two-digit FIPS state code (e.g., "06" for California).
        county_code: Optional three-digit FIPS county code (e.g., "037" for Los Angeles).
        year: Year for data retrieval (default: most recent available).

    Returns:
        Dictionary with air quality measurements and metadata.
    """
    safe_state = sanitize_query(state_code, max_len=2)
    if not safe_state or not safe_state.isdigit() or len(safe_state) != 2:
        return {"error": "Invalid state_code. Must be a 2-digit FIPS code (e.g., '06')."}

    if county_code:
        safe_county = sanitize_query(county_code, max_len=3)
        if not safe_county or not safe_county.isdigit():
            return {"error": "Invalid county_code. Must be a 3-digit FIPS code."}
    else:
        safe_county = None

    if not EPA_AQS_EMAIL or not EPA_AQS_KEY:
        return {
            "status": "credentials_required",
            "message": (
                "EPA AQS API requires registration. Set EPA_AQS_EMAIL and "
                "EPA_AQS_KEY environment variables. Register at: "
                "https://aqs.epa.gov/aqsweb/documents/data_api.html"
            ),
            "fallback_search_query": (
                f"EPA air quality state {safe_state} "
                f"{'county ' + safe_county if safe_county else ''} "
                "site:epa.gov"
            ),
        }

    query_year = str(year) if year else "2023"

    # Build the AQS annualData endpoint
    if safe_county:
        endpoint = f"{EPA_AQS_URL}/annualData/byCounty"
        params = {
            "email": EPA_AQS_EMAIL,
            "key": EPA_AQS_KEY,
            "param": "88101",  # PM2.5 as default pollutant
            "bdate": f"{query_year}0101",
            "edate": f"{query_year}1231",
            "state": safe_state,
            "county": safe_county,
        }
    else:
        endpoint = f"{EPA_AQS_URL}/annualData/byState"
        params = {
            "email": EPA_AQS_EMAIL,
            "key": EPA_AQS_KEY,
            "param": "88101",
            "bdate": f"{query_year}0101",
            "edate": f"{query_year}1231",
            "state": safe_state,
        }

    try:
        resp = await resilient_get(endpoint, params=params)
        if resp.status_code == 200:
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError) as exc:
                log.error("Failed to parse AQS JSON: %s", exc)
                return {"error": "Invalid JSON response from EPA AQS API", "results": []}
            # AQS returns {"Header": [...], "Data": [...]}
            records = data.get("Data", [])
            parsed = []
            for rec in records[:50]:  # Cap at 50 records
                parsed.append({
                    "site": rec.get("site_number", ""),
                    "parameter": rec.get("parameter_name", ""),
                    "year": rec.get("year", ""),
                    "arithmetic_mean": rec.get("arithmetic_mean", ""),
                    "first_max_value": rec.get("first_max_value", ""),
                    "observation_count": rec.get("observation_count", ""),
                    "units": rec.get("units_of_measure", ""),
                    "county": rec.get("county_name", ""),
                    "state": rec.get("state_name", ""),
                })
            return {
                "success": True,
                "state_code": safe_state,
                "county_code": safe_county,
                "year": query_year,
                "results": parsed,
                "total_results": len(parsed),
            }
        else:
            return {
                "error": f"EPA AQS API returned status {resp.status_code}",
                "results": [],
            }

    except (CircuitOpenError, RetryExhaustedError) as exc:
        return raise_or_return_error(exc, "environmental", "get_air_quality", results=[])
    except Exception as exc:
        log.error("EPA AQS query failed: %s", exc)
        return {"error": str(exc), "results": []}


@mcp.tool()
async def get_environmental_justice_data(location: str) -> dict:
    """Retrieve environmental justice screening data for a location.

    Queries EPA EJScreen for environmental justice indices including
    pollution burden, demographic indicators, and EJ index scores.

    Args:
        location: Location description (city name, ZIP code, or FIPS code).

    Returns:
        Dictionary with environmental justice indicators and metadata.
    """
    safe_location = sanitize_query(location, max_len=100)
    if not safe_location:
        return {"error": "Invalid location parameter"}

    # EJScreen REST broker accepts various geometry types
    # For simplicity, we use the namestr parameter for location lookup
    params = {
        "namestr": safe_location,
        "geometry": "",
        "distance": "1",
        "unit": "9035",  # Miles
        "session": "",
        "f": "json",
    }

    try:
        resp = await resilient_get(EPA_EJSCREEN_URL, params=params)
        if resp.status_code == 200:
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError) as exc:
                log.error("Failed to parse EJScreen JSON: %s", exc)
                return {"error": "Invalid JSON response from EJScreen", "results": []}
            # EJScreen returns demographic and environmental indicators
            if isinstance(data, dict) and data.get("error"):
                return {
                    "location": safe_location,
                    "error": data["error"],
                    "results": [],
                }

            return {
                "success": True,
                "location": safe_location,
                "data": data,
                "source": "EPA EJScreen",
            }
        else:
            return {
                "error": f"EJScreen API returned status {resp.status_code}",
                "location": safe_location,
                "results": [],
            }

    except (CircuitOpenError, RetryExhaustedError) as exc:
        return raise_or_return_error(exc, "environmental", "get_environmental_justice_data", results=[])
    except Exception as exc:
        log.error("EJScreen query failed: %s", exc)
        return {"error": str(exc), "results": []}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9008)
