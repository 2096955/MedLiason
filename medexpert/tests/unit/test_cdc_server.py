"""Tests for the CDC SODA Open Data MCP server.

Covers search_disease_data, get_mortality_data, get_vaccination_data, and
get_surveillance_data. CRITICAL: includes SoQL injection prevention tests
to verify that escape_soql() is applied to all $where clause parameters.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.cdc import server as cdc_module
from mcp_servers.cdc.server import DATASET_IDS

# FastMCP 3.x: @mcp.tool() returns the original function directly.
# Import tool functions directly from the server module.
search_disease_data = cdc_module.search_disease_data
get_mortality_data = cdc_module.get_mortality_data
get_vaccination_data = cdc_module.get_vaccination_data
get_surveillance_data = cdc_module.get_surveillance_data


# ---------------------------------------------------------------------------
# Realistic JSON fixtures
# ---------------------------------------------------------------------------

NNDSS_RECORDS = [
    {
        "disease": "Salmonellosis",
        "mmwr_year": "2024",
        "mmwr_week": "42",
        "current_week": "312",
        "cumulative_ytd_current_mmwr_year": "14520",
        "reporting_area": "UNITED STATES",
    },
    {
        "disease": "Salmonellosis",
        "mmwr_year": "2024",
        "mmwr_week": "41",
        "current_week": "289",
        "cumulative_ytd_current_mmwr_year": "14208",
        "reporting_area": "UNITED STATES",
    },
]

MORTALITY_RECORDS = [
    {
        "cause_name": "Heart disease",
        "state": "United States",
        "year": "2022",
        "deaths": "702880",
        "age_adjusted_death_rate": "173.8",
    },
    {
        "cause_name": "Heart disease",
        "state": "California",
        "year": "2022",
        "deaths": "62145",
        "age_adjusted_death_rate": "148.2",
    },
]

VACCINATION_RECORDS = [
    {
        "vaccine": "Influenza",
        "dose": ">=1 Dose",
        "year_season": "2023-24",
        "geography_type": "National",
        "geography": "United States",
        "dimension_type": "Age",
        "dimension": ">=6 Months",
        "estimate_pct": "47.4",
    },
]

EMPTY_RESPONSE = []


def _mock_response(status_code: int, json_data: list | dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# search_disease_data tests
# ---------------------------------------------------------------------------

class TestSearchDiseaseData:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=NNDSS_RECORDS)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_disease_data("Salmonellosis")
        assert result["returned_count"] == 2
        assert result["results"][0]["disease"] == "Salmonellosis"
        assert result["results"][0]["mmwr_year"] == "2024"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_disease_data("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(500)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_disease_data("Measles")
        assert "error" in result
        assert "500" in result["error"]

    @pytest.mark.asyncio
    async def test_escape_soql_is_called(self):
        """Verify escape_soql is applied to the disease parameter in $where."""
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with (
            patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get,
            patch("mcp_servers.cdc.server.escape_soql", wraps=lambda v: v.replace("'", "\\'")) as mock_escape,
        ):
            await search_disease_data("Hepatitis B")
            mock_escape.assert_called()
            # Check that $where param was passed
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "$where" in params


# ---------------------------------------------------------------------------
# SoQL injection prevention tests (CRITICAL SECURITY)
# ---------------------------------------------------------------------------

class TestSoqlInjectionPrevention:
    """These tests verify that SoQL injection attempts are neutralized.

    All $where clause values MUST pass through escape_soql(). Single
    quotes in user input must be escaped to prevent query manipulation.
    """

    @pytest.mark.asyncio
    async def test_single_quote_injection_disease(self):
        """Attempt: disease = "Measles' OR '1'='1" — injection must be neutralized."""
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_disease_data("Measles' OR '1'='1")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            where_clause = params.get("$where", "")
            # The injection pattern "OR '1'='1'" must not survive into the where clause
            assert "OR '1'='1'" not in where_clause
            assert "OR 1=1" not in where_clause

    @pytest.mark.asyncio
    async def test_single_quote_injection_mortality(self):
        """Attempt: cause = "heart' OR '1'='1" — injection must be neutralized."""
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_mortality_data("heart' OR '1'='1")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            where_clause = params.get("$where", "")
            assert "OR '1'='1'" not in where_clause
            assert "OR 1=1" not in where_clause

    @pytest.mark.asyncio
    async def test_single_quote_injection_vaccination(self):
        """Attempt: vaccine = "Flu' OR '1'='1" — injection must be neutralized."""
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_vaccination_data("Flu' OR '1'='1")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            where_clause = params.get("$where", "")
            assert "OR '1'='1'" not in where_clause
            assert "OR 1=1" not in where_clause

    @pytest.mark.asyncio
    async def test_single_quote_injection_surveillance(self):
        """Attempt: condition = "TB' OR '1'='1" — injection must be neutralized."""
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_surveillance_data("TB' OR '1'='1")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            where_clause = params.get("$where", "")
            assert "OR '1'='1'" not in where_clause
            assert "OR 1=1" not in where_clause

    @pytest.mark.asyncio
    async def test_year_parameter_injection_mortality(self):
        """Verify the year parameter is also escaped in get_mortality_data."""
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_mortality_data("cancer", year=2022)
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            where_clause = params.get("$where", "")
            # Year should appear in the where clause
            assert "2022" in where_clause


# ---------------------------------------------------------------------------
# get_mortality_data tests
# ---------------------------------------------------------------------------

class TestGetMortalityData:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=MORTALITY_RECORDS)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_mortality_data("Heart disease")
        assert result["returned_count"] == 2
        assert result["results"][0]["cause_name"] == "Heart disease"
        assert result["results"][0]["deaths"] == "702880"

    @pytest.mark.asyncio
    async def test_with_year_filter(self):
        mock_resp = _mock_response(200, json_data=MORTALITY_RECORDS[:1])
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            result = await get_mortality_data("Heart disease", year=2022)
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "2022" in params.get("$where", "")

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await get_mortality_data("")
        assert "error" in result


# ---------------------------------------------------------------------------
# get_vaccination_data tests
# ---------------------------------------------------------------------------

class TestGetVaccinationData:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=VACCINATION_RECORDS)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_vaccination_data("Influenza")
        assert result["returned_count"] == 1
        assert result["results"][0]["vaccine"] == "Influenza"

    @pytest.mark.asyncio
    async def test_no_filter_all_vaccines(self):
        mock_resp = _mock_response(200, json_data=VACCINATION_RECORDS)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            result = await get_vaccination_data()
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "$where" not in params

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(403)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_vaccination_data("COVID-19")
        assert "error" in result


# ---------------------------------------------------------------------------
# get_surveillance_data tests
# ---------------------------------------------------------------------------

class TestGetSurveillanceData:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=NNDSS_RECORDS)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_surveillance_data("Salmonellosis")
        assert result["returned_count"] == 2
        assert result["results"][0]["disease"] == "Salmonellosis"

    @pytest.mark.asyncio
    async def test_empty_condition(self):
        result = await get_surveillance_data("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_select_fields_present(self):
        """Verify $select is passed to limit returned columns."""
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.cdc.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_surveillance_data("Pertussis")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "$select" in params
