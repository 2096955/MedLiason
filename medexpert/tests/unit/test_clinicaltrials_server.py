"""Tests for the ClinicalTrials.gov MCP server.

Covers search_trials, get_trial_details, search_by_condition, and
search_by_intervention with mocked HTTP responses. Tests JSON parsing,
error handling, and input sanitization.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.clinicaltrials import server as clinicaltrials_module
from mcp_servers.clinicaltrials.server import (
    _parse_study,
    _parse_study_list,
)

# FastMCP 2.x wraps @mcp.tool() decorated functions in FunctionTool objects.
# Access the underlying async function via the .fn attribute.
search_trials = clinicaltrials_module.search_trials.fn
get_trial_details = clinicaltrials_module.get_trial_details.fn
search_by_condition = clinicaltrials_module.search_by_condition.fn
search_by_intervention = clinicaltrials_module.search_by_intervention.fn


# ---------------------------------------------------------------------------
# Realistic JSON fixtures
# ---------------------------------------------------------------------------

SAMPLE_STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT06123456",
            "briefTitle": "A Phase 3 Study of Pembrolizumab in Advanced NSCLC",
            "officialTitle": "Randomized, Double-Blind Phase 3 Study of Pembrolizumab vs Placebo in Advanced Non-Small Cell Lung Cancer",
        },
        "statusModule": {
            "overallStatus": "RECRUITING",
            "startDateStruct": {"date": "2024-01-15"},
            "completionDateStruct": {"date": "2027-06-30"},
        },
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE3"],
            "enrollmentInfo": {"count": 800, "type": "ESTIMATED"},
        },
        "sponsorCollaboratorsModule": {
            "leadSponsor": {"name": "Merck Sharp & Dohme LLC"},
        },
        "conditionsModule": {
            "conditions": ["Non-Small Cell Lung Cancer", "NSCLC"],
        },
        "armsInterventionsModule": {
            "interventions": [
                {"name": "Pembrolizumab", "type": "DRUG"},
                {"name": "Placebo", "type": "DRUG"},
            ],
        },
        "eligibilityModule": {
            "eligibilityCriteria": "Inclusion: Age >= 18, confirmed NSCLC",
            "sex": "ALL",
            "minimumAge": "18 Years",
            "maximumAge": "N/A",
        },
        "descriptionModule": {
            "briefSummary": "This study evaluates pembrolizumab in advanced NSCLC.",
        },
    }
}

STUDIES_RESPONSE = {
    "totalCount": 245,
    "studies": [SAMPLE_STUDY],
}

EMPTY_STUDIES_RESPONSE = {
    "totalCount": 0,
    "studies": [],
}


def _mock_response(status_code: int, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# Parsing unit tests
# ---------------------------------------------------------------------------

class TestParseStudy:
    def test_parses_all_fields(self):
        result = _parse_study(SAMPLE_STUDY)
        assert result["nct_id"] == "NCT06123456"
        assert "Pembrolizumab" in result["brief_title"]
        assert result["overall_status"] == "RECRUITING"
        assert result["phase"] == ["PHASE3"]
        assert result["enrollment"] == 800
        assert result["lead_sponsor"] == "Merck Sharp & Dohme LLC"
        assert "Non-Small Cell Lung Cancer" in result["conditions"]
        assert len(result["interventions"]) == 2
        assert result["interventions"][0]["name"] == "Pembrolizumab"
        assert result["eligibility"]["minimum_age"] == "18 Years"

    def test_handles_missing_modules(self):
        minimal = {"protocolSection": {}}
        result = _parse_study(minimal)
        assert result["nct_id"] == ""
        assert result["conditions"] == []
        assert result["interventions"] == []

    def test_parse_study_list(self):
        results = _parse_study_list(STUDIES_RESPONSE)
        assert len(results) == 1
        assert results[0]["nct_id"] == "NCT06123456"


# ---------------------------------------------------------------------------
# search_trials tests
# ---------------------------------------------------------------------------

class TestSearchTrials:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=STUDIES_RESPONSE)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_trials("pembrolizumab NSCLC", max_results=10)
        assert result["total_count"] == 245
        assert result["returned_count"] == 1
        assert result["results"][0]["nct_id"] == "NCT06123456"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_trials("")
        assert result["error"] == "Empty query after sanitization"

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(503)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_trials("cancer")
        assert "error" in result
        assert "503" in result["error"]

    @pytest.mark.asyncio
    async def test_sanitizes_query(self):
        mock_resp = _mock_response(200, json_data=EMPTY_STUDIES_RESPONSE)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_trials("cancer; DROP TABLE trials")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "DROP" not in params.get("query.term", "")

    @pytest.mark.asyncio
    async def test_max_results_capped(self):
        mock_resp = _mock_response(200, json_data=EMPTY_STUDIES_RESPONSE)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_trials("diabetes", max_results=999)
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert params["pageSize"] == 100


# ---------------------------------------------------------------------------
# get_trial_details tests
# ---------------------------------------------------------------------------

class TestGetTrialDetails:
    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        mock_resp = _mock_response(200, json_data=SAMPLE_STUDY)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_trial_details("NCT06123456")
        assert result["nct_id"] == "NCT06123456"
        assert "Pembrolizumab" in result["brief_title"]

    @pytest.mark.asyncio
    async def test_invalid_nct_id(self):
        result = await get_trial_details("INVALID123")
        assert "error" in result
        assert "NCT" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_nct_id(self):
        result = await get_trial_details("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        mock_resp = _mock_response(404)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_trial_details("NCT00000001")
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_case_insensitive_nct_id(self):
        mock_resp = _mock_response(200, json_data=SAMPLE_STUDY)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_trial_details("nct06123456")
            url_called = mock_get.call_args[0][0]
            assert "NCT06123456" in url_called


# ---------------------------------------------------------------------------
# search_by_condition tests
# ---------------------------------------------------------------------------

class TestSearchByCondition:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=STUDIES_RESPONSE)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            result = await search_by_condition("lung cancer")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "query.cond" in params
        assert result["returned_count"] == 1

    @pytest.mark.asyncio
    async def test_empty_condition(self):
        result = await search_by_condition("")
        assert "error" in result


# ---------------------------------------------------------------------------
# search_by_intervention tests
# ---------------------------------------------------------------------------

class TestSearchByIntervention:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=STUDIES_RESPONSE)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            result = await search_by_intervention("pembrolizumab")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "query.intr" in params
        assert result["returned_count"] == 1

    @pytest.mark.asyncio
    async def test_empty_intervention(self):
        result = await search_by_intervention("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(500)
        with patch("mcp_servers.clinicaltrials.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_by_intervention("nivolumab")
        assert "error" in result
        assert "500" in result["error"]
