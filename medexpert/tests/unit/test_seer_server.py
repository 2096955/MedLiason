"""Tests for SEER / NCI Cancer Statistics MCP Server.

Covers get_cancer_statistics and search_cancer_incidence tools with
mocked HTTP responses, error handling, and parameter validation.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# We test the tool functions directly, not through MCP transport
import sys
import os

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)
from mcp_servers.seer import server as seer_module
from mcp_servers.seer.server import _parse_cancer_record

# FastMCP @mcp.tool() wraps functions in FunctionTool objects.
# Access the underlying async function via the .fn attribute.
_get_cancer_statistics = seer_module.get_cancer_statistics.fn
_search_cancer_incidence = seer_module.search_cancer_incidence.fn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_INCIDENCE_RECORDS = [
    {
        "leading_cancer_sites": "Female Breast",
        "year": "2020",
        "sex": "Female",
        "race": "All Races",
        "count": "281550",
        "population": "167228000",
        "age_adjusted_rate": "126.8",
    },
    {
        "leading_cancer_sites": "Female Breast",
        "year": "2019",
        "sex": "Female",
        "race": "All Races",
        "count": "268600",
        "population": "166235000",
        "age_adjusted_rate": "125.9",
    },
]

SAMPLE_MORTALITY_RECORDS = [
    {
        "leading_cancer_sites": "Female Breast",
        "year": "2020",
        "sex": "Female",
        "race": "All Races",
        "count": "42170",
        "population": "167228000",
        "age_adjusted_rate": "19.3",
    },
]


def _mock_response(status_code: int, data: list | dict) -> httpx.Response:
    """Create a mock httpx.Response with JSON data."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


# ---------------------------------------------------------------------------
# Tests: _parse_cancer_record
# ---------------------------------------------------------------------------


class TestParseCancerRecord:
    def test_parse_standard_record(self):
        record = SAMPLE_INCIDENCE_RECORDS[0]
        parsed = _parse_cancer_record(record)
        assert parsed["cancer_site"] == "Female Breast"
        assert parsed["year"] == "2020"
        assert parsed["count"] == "281550"
        assert parsed["age_adjusted_rate"] == "126.8"

    def test_parse_empty_record(self):
        parsed = _parse_cancer_record({})
        assert parsed["cancer_site"] == ""
        assert parsed["year"] == ""
        assert parsed["count"] == ""

    def test_parse_record_with_alternate_fields(self):
        record = {"site": "Lung", "case_count": "100", "crude_rate": "5.2"}
        parsed = _parse_cancer_record(record)
        assert parsed["cancer_site"] == "Lung"
        assert parsed["count"] == "100"
        assert parsed["crude_rate"] == "5.2"


# ---------------------------------------------------------------------------
# Tests: get_cancer_statistics
# ---------------------------------------------------------------------------


class TestGetCancerStatistics:
    @pytest.mark.asyncio
    async def test_success_both_datasets(self):
        incidence_resp = _mock_response(200, SAMPLE_INCIDENCE_RECORDS)
        mortality_resp = _mock_response(200, SAMPLE_MORTALITY_RECORDS)

        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            side_effect=[incidence_resp, mortality_resp],
        ):
            result = await _get_cancer_statistics("breast")

        assert result["cancer_type"] == "breast"
        assert result["total_incidence_records"] == 2
        assert result["total_mortality_records"] == 1
        assert result["incidence"][0]["cancer_site"] == "Female Breast"
        assert result["mortality"][0]["cancer_site"] == "Female Breast"

    @pytest.mark.asyncio
    async def test_empty_cancer_type_returns_error(self):
        result = await _get_cancer_statistics("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_incidence_api_failure_still_returns_mortality(self):
        mortality_resp = _mock_response(200, SAMPLE_MORTALITY_RECORDS)

        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            side_effect=[Exception("Connection timeout"), mortality_resp],
        ):
            result = await _get_cancer_statistics("breast")

        assert result["total_incidence_records"] == 0
        assert result["total_mortality_records"] == 1

    @pytest.mark.asyncio
    async def test_non_200_status_returns_empty(self):
        bad_resp = _mock_response(404, [])

        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            return_value=bad_resp,
        ):
            result = await _get_cancer_statistics("rare_cancer")

        assert result["total_incidence_records"] == 0
        assert result["total_mortality_records"] == 0

    @pytest.mark.asyncio
    async def test_max_results_capped_at_100(self):
        resp = _mock_response(200, [])
        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ) as mock_get:
            await _get_cancer_statistics("lung", max_results=500)

        # Both calls should cap $limit at 100
        for call_args in mock_get.call_args_list:
            params = call_args[1].get("params", call_args[0][1] if len(call_args[0]) > 1 else {})
            assert int(params["$limit"]) <= 100

    @pytest.mark.asyncio
    async def test_injection_attempt_sanitized(self):
        """Ensure SoQL injection in cancer_type is neutralized.

        sanitize_query removes the '; DROP' pattern and quotes.
        escape_soql escapes remaining quotes/backslashes.
        The injection vector (semicolons + quotes) is neutralized even
        though the word 'TABLE' may remain as harmless text.
        """
        resp = _mock_response(200, [])
        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ) as mock_get:
            await _get_cancer_statistics("lung'; DROP TABLE --")

        # The injection vector ('; DROP) should be stripped by sanitize_query
        for call_args in mock_get.call_args_list:
            params = call_args[1].get("params", call_args[0][1] if len(call_args[0]) > 1 else {})
            where = params.get("$where", "")
            # Single quotes (the injection vector) are removed by sanitize_query
            assert "'" not in where.replace("upper('%", "").replace("%')", "")
            # The '; DROP' pattern is removed by sanitize_query
            assert "; DROP" not in where


# ---------------------------------------------------------------------------
# Tests: search_cancer_incidence
# ---------------------------------------------------------------------------


class TestSearchCancerIncidence:
    @pytest.mark.asyncio
    async def test_success_without_year(self):
        resp = _mock_response(200, SAMPLE_INCIDENCE_RECORDS)
        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await _search_cancer_incidence("breast")

        assert result["cancer_type"] == "breast"
        assert result["year_filter"] is None
        assert result["total_results"] == 2

    @pytest.mark.asyncio
    async def test_success_with_year_filter(self):
        resp = _mock_response(200, [SAMPLE_INCIDENCE_RECORDS[0]])
        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ) as mock_get:
            result = await _search_cancer_incidence("breast", year=2020)

        assert result["year_filter"] == 2020
        assert result["total_results"] == 1
        # Verify year was included in where clause
        call_params = mock_get.call_args[1].get("params", {})
        assert "2020" in call_params.get("$where", "")

    @pytest.mark.asyncio
    async def test_api_error_returns_error_dict(self):
        resp = _mock_response(500, {"error": "Internal server error"})
        with patch(
            "mcp_servers.seer.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await _search_cancer_incidence("lung")

        assert "error" in result
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        result = await _search_cancer_incidence("")
        assert "error" in result
