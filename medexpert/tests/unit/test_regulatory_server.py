"""Tests for the FDA Regulatory Data MCP server.

Covers search_510k, search_pma, get_device_classification, and
search_guidance_documents with mocked HTTP responses. Tests JSON parsing,
error handling, and input sanitization.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.regulatory import server as regulatory_module
from mcp_servers.regulatory.server import (
    _parse_510k,
    _parse_pma,
    _parse_classification,
    _build_search_query,
)

# FastMCP 3.x: @mcp.tool() returns the original function directly.
# Import tool functions directly from the server module.
search_510k = regulatory_module.search_510k
search_pma = regulatory_module.search_pma
get_device_classification = regulatory_module.get_device_classification
search_guidance_documents = regulatory_module.search_guidance_documents


# ---------------------------------------------------------------------------
# Realistic JSON fixtures
# ---------------------------------------------------------------------------

RECORD_510K = {
    "k_number": "K241234",
    "applicant": "Medtronic Inc",
    "device_name": "Cardiac Rhythm Management System",
    "date_received": "20240115",
    "decision_date": "20240601",
    "decision_code": "SESE",
    "decision_description": "Substantially Equivalent",
    "product_code": "DRT",
    "statement_or_summary": "Summary",
    "clearance_type": "Traditional",
    "advisory_committee": "CV",
    "advisory_committee_description": "Cardiovascular",
    "review_panel": "CV",
    "openfda": {
        "regulation_number": ["870.1025"],
        "device_class": "2",
        "medical_specialty_description": "Cardiovascular",
    },
}

RESPONSE_510K = {
    "meta": {"results": {"total": 45}},
    "results": [RECORD_510K],
}

RECORD_PMA = {
    "pma_number": "P240001",
    "applicant": "Abbott Laboratories",
    "trade_name": "FreeStyle Libre 4",
    "generic_name": "Continuous Glucose Monitor",
    "date_received": "20240301",
    "decision_date": "20240815",
    "decision_code": "APPR",
    "product_code": "QAS",
    "advisory_committee": "CH",
    "advisory_committee_description": "Clinical Chemistry",
    "supplement_type": "",
    "supplement_number": "",
    "openfda": {
        "regulation_number": ["862.1345"],
        "device_class": "3",
    },
}

RESPONSE_PMA = {
    "meta": {"results": {"total": 18}},
    "results": [RECORD_PMA],
}

RECORD_CLASSIFICATION = {
    "product_code": "QAS",
    "device_name": "System, Test, Blood Glucose, Over The Counter",
    "device_class": "2",
    "medical_specialty": "CH",
    "medical_specialty_description": "Clinical Chemistry",
    "regulation_number": "862.1345",
    "definition": "A blood glucose test system for over-the-counter use.",
    "review_panel": "CH",
    "submission_type_id": "2",
    "gmp_exempt_flag": "N",
    "third_party_flag": "Y",
    "unclassified_reason": "",
    "openfda": {
        "registration_number": ["1234567"],
        "fei_number": ["3001234567"],
    },
}

RESPONSE_CLASSIFICATION = {
    "meta": {"results": {"total": 1}},
    "results": [RECORD_CLASSIFICATION],
}

GUIDANCE_RECORD = {
    "registration_number": "9876543",
    "establishment_type": ["Manufacture Medical Device"],
    "proprietary_name": ["CardioMonitor Pro"],
    "products": [
        {"product_code": "DRT", "created_date": "2024-01-15"},
    ],
    "name": "MedTech Corporation",
    "city": "Minneapolis",
    "state": "MN",
    "country_code": "US",
}

RESPONSE_GUIDANCE = {
    "meta": {"results": {"total": 23}},
    "results": [GUIDANCE_RECORD],
}

EMPTY_RESPONSE = {"meta": {"results": {"total": 0}}, "results": []}


def _mock_response(status_code: int, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# Parsing unit tests
# ---------------------------------------------------------------------------

class TestParse510k:
    def test_extracts_all_fields(self):
        result = _parse_510k(RECORD_510K)
        assert result["k_number"] == "K241234"
        assert result["applicant"] == "Medtronic Inc"
        assert result["decision_description"] == "Substantially Equivalent"
        assert result["device_class"] == "2"
        assert result["product_code"] == "DRT"


class TestParsePma:
    def test_extracts_all_fields(self):
        result = _parse_pma(RECORD_PMA)
        assert result["pma_number"] == "P240001"
        assert result["trade_name"] == "FreeStyle Libre 4"
        assert result["decision_code"] == "APPR"
        assert result["device_class"] == "3"


class TestParseClassification:
    def test_extracts_all_fields(self):
        result = _parse_classification(RECORD_CLASSIFICATION)
        assert result["product_code"] == "QAS"
        assert result["device_class"] == "2"
        assert "blood glucose" in result["definition"].lower()
        assert result["third_party_review"] == "Y"


class TestBuildSearchQuery:
    def test_wraps_in_quotes(self):
        assert _build_search_query("pacemaker") == '"pacemaker"'


# ---------------------------------------------------------------------------
# search_510k tests
# ---------------------------------------------------------------------------

class TestSearch510k:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=RESPONSE_510K)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_510k("cardiac monitor")
        assert result["total_count"] == 45
        assert result["returned_count"] == 1
        assert result["results"][0]["k_number"] == "K241234"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_510k("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(500)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_510k("stent")
        assert "error" in result
        assert "500" in result["error"]


# ---------------------------------------------------------------------------
# search_pma tests
# ---------------------------------------------------------------------------

class TestSearchPma:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=RESPONSE_PMA)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_pma("glucose monitor")
        assert result["total_count"] == 18
        assert result["results"][0]["pma_number"] == "P240001"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_pma("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sanitizes_query(self):
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_pma("implant; DROP TABLE devices")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "DROP" not in params.get("search", "")


# ---------------------------------------------------------------------------
# get_device_classification tests
# ---------------------------------------------------------------------------

class TestGetDeviceClassification:
    @pytest.mark.asyncio
    async def test_successful_lookup(self):
        mock_resp = _mock_response(200, json_data=RESPONSE_CLASSIFICATION)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_device_classification("QAS")
        assert result["product_code"] == "QAS"
        assert result["results"][0]["device_class"] == "2"

    @pytest.mark.asyncio
    async def test_uppercase_conversion(self):
        mock_resp = _mock_response(200, json_data=RESPONSE_CLASSIFICATION)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_device_classification("qas")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "QAS" in params.get("search", "")

    @pytest.mark.asyncio
    async def test_empty_product_code(self):
        result = await get_device_classification("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_results(self):
        empty_class_resp = {"meta": {"results": {"total": 0}}, "results": []}
        mock_resp = _mock_response(200, json_data=empty_class_resp)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_device_classification("ZZZ")
        assert "error" in result
        assert "No classification found" in result["error"]


# ---------------------------------------------------------------------------
# search_guidance_documents tests
# ---------------------------------------------------------------------------

class TestSearchGuidanceDocuments:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=RESPONSE_GUIDANCE)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_guidance_documents("cardiac")
        assert result["total_count"] == 23
        assert result["returned_count"] == 1
        assert result["results"][0]["name"] == "MedTech Corporation"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_guidance_documents("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(404)
        with patch("mcp_servers.regulatory.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_guidance_documents("orthopedic")
        assert "error" in result
        assert "404" in result["error"]
