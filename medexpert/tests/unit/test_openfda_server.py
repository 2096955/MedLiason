"""Tests for the OpenFDA MCP server.

Covers search_drug_events, search_drug_labels, search_drug_recalls, and
search_device_events with mocked HTTP responses. Tests JSON parsing, error
handling, and input sanitization.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.openfda import server as openfda_module
from mcp_servers.openfda.server import (
    _parse_drug_event,
    _parse_drug_label,
    _parse_recall,
    _parse_device_event,
    _build_search_query,
)

# FastMCP 2.x wraps @mcp.tool() decorated functions in FunctionTool objects.
# Access the underlying async function via the .fn attribute.
search_drug_events = openfda_module.search_drug_events.fn
search_drug_labels = openfda_module.search_drug_labels.fn
search_drug_recalls = openfda_module.search_drug_recalls.fn
search_device_events = openfda_module.search_device_events.fn


# ---------------------------------------------------------------------------
# Realistic JSON fixtures
# ---------------------------------------------------------------------------

DRUG_EVENT_RECORD = {
    "safetyreportid": "10456789",
    "receivedate": "20240315",
    "serious": "1",
    "sender": {"senderorganization": "FDA-CDER"},
    "patient": {
        "patientonsetage": "62",
        "patientsex": "2",
        "drug": [
            {
                "medicinalproduct": "METFORMIN HCL",
                "drugindication": "Type 2 Diabetes Mellitus",
                "drugcharacterization": "1",
                "drugadministrationroute": "048",
                "drugdosagetext": "500 mg twice daily",
            },
        ],
        "reaction": [
            {"reactionmeddrapt": "Lactic acidosis", "reactionoutcome": "1"},
            {"reactionmeddrapt": "Nausea", "reactionoutcome": "6"},
        ],
    },
}

DRUG_EVENT_RESPONSE = {
    "meta": {"results": {"total": 3421}},
    "results": [DRUG_EVENT_RECORD],
}

DRUG_LABEL_RECORD = {
    "id": "spl-abc123",
    "openfda": {
        "brand_name": ["METFORMIN HYDROCHLORIDE"],
        "generic_name": ["METFORMIN HYDROCHLORIDE"],
        "manufacturer_name": ["Teva Pharmaceuticals"],
        "route": ["ORAL"],
        "product_type": ["HUMAN PRESCRIPTION DRUG"],
    },
    "indications_and_usage": ["Treatment of type 2 diabetes mellitus."],
    "warnings": ["Lactic acidosis risk with renal impairment."],
    "adverse_reactions": ["Diarrhea, nausea, vomiting, flatulence."],
    "dosage_and_administration": ["500 mg orally twice daily."],
    "contraindications": ["Renal impairment (eGFR < 30)."],
    "drug_interactions": ["Cimetidine may increase metformin levels."],
}

DRUG_LABEL_RESPONSE = {
    "meta": {"results": {"total": 89}},
    "results": [DRUG_LABEL_RECORD],
}

RECALL_RECORD = {
    "recall_number": "D-2024-1234",
    "event_id": "90123",
    "status": "Ongoing",
    "classification": "Class II",
    "reason_for_recall": "Impurity levels exceeding acceptable limits",
    "product_description": "Metformin HCl tablets, 500mg",
    "recalling_firm": "Generic Pharma Inc",
    "city": "Princeton",
    "state": "NJ",
    "country": "US",
    "recall_initiation_date": "20240201",
    "voluntary_mandated": "Voluntary: Firm Initiated",
    "openfda": {"brand_name": ["METFORMIN HCL"]},
}

RECALL_RESPONSE = {
    "meta": {"results": {"total": 12}},
    "results": [RECALL_RECORD],
}

DEVICE_EVENT_RECORD = {
    "report_number": "MW5012345-2024-00001",
    "event_type": "Malfunction",
    "date_received": "20240510",
    "adverse_event_flag": "Y",
    "product_problem_flag": "Y",
    "device": [
        {
            "brand_name": "CardioMonitor Pro",
            "generic_name": "Cardiac Monitor",
            "manufacturer_d_name": "MedTech Corp",
            "model_number": "CM-3000",
            "catalog_number": "CM3K-01",
            "device_report_product_code": "DRT",
        },
    ],
    "mdr_text": [
        {
            "text_type_code": "Description of Event",
            "text": "Device displayed erratic heart rhythm readings.",
        },
    ],
}

DEVICE_EVENT_RESPONSE = {
    "meta": {"results": {"total": 567}},
    "results": [DEVICE_EVENT_RECORD],
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

class TestParseDrugEvent:
    def test_extracts_all_fields(self):
        result = _parse_drug_event(DRUG_EVENT_RECORD)
        assert result["safety_report_id"] == "10456789"
        assert result["serious"] == "1"
        assert result["patient_age"] == "62"
        assert len(result["drugs"]) == 1
        assert result["drugs"][0]["name"] == "METFORMIN HCL"
        assert len(result["reactions"]) == 2
        assert result["reactions"][0]["term"] == "Lactic acidosis"


class TestParseDrugLabel:
    def test_extracts_all_fields(self):
        result = _parse_drug_label(DRUG_LABEL_RECORD)
        assert result["brand_name"] == ["METFORMIN HYDROCHLORIDE"]
        assert "Teva" in result["manufacturer"][0]
        assert "type 2 diabetes" in result["indications_and_usage"][0].lower()


class TestParseRecall:
    def test_extracts_all_fields(self):
        result = _parse_recall(RECALL_RECORD)
        assert result["recall_number"] == "D-2024-1234"
        assert result["classification"] == "Class II"
        assert "Impurity" in result["reason_for_recall"]
        assert result["brand_name"] == ["METFORMIN HCL"]


class TestParseDeviceEvent:
    def test_extracts_all_fields(self):
        result = _parse_device_event(DEVICE_EVENT_RECORD)
        assert result["event_type"] == "Malfunction"
        assert len(result["devices"]) == 1
        assert result["devices"][0]["brand_name"] == "CardioMonitor Pro"
        assert "erratic" in result["mdr_text"][0]["text"]


class TestBuildSearchQuery:
    def test_wraps_query_in_quotes(self):
        assert _build_search_query("metformin") == '"metformin"'


# ---------------------------------------------------------------------------
# search_drug_events tests
# ---------------------------------------------------------------------------

class TestSearchDrugEvents:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=DRUG_EVENT_RESPONSE)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_drug_events("metformin")
        assert result["total_count"] == 3421
        assert result["returned_count"] == 1
        assert result["results"][0]["safety_report_id"] == "10456789"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_drug_events("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(429)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_drug_events("aspirin")
        assert "error" in result


# ---------------------------------------------------------------------------
# search_drug_labels tests
# ---------------------------------------------------------------------------

class TestSearchDrugLabels:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=DRUG_LABEL_RESPONSE)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_drug_labels("metformin")
        assert result["total_count"] == 89
        assert result["results"][0]["brand_name"] == ["METFORMIN HYDROCHLORIDE"]

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_drug_labels("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(500)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_drug_labels("ibuprofen")
        assert "error" in result


# ---------------------------------------------------------------------------
# search_drug_recalls tests
# ---------------------------------------------------------------------------

class TestSearchDrugRecalls:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=RECALL_RESPONSE)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_drug_recalls("metformin")
        assert result["total_count"] == 12
        assert result["results"][0]["classification"] == "Class II"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_drug_recalls("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sanitization_strips_injection(self):
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_drug_recalls("valsartan; DROP TABLE recalls")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "DROP" not in params.get("search", "")


# ---------------------------------------------------------------------------
# search_device_events tests
# ---------------------------------------------------------------------------

class TestSearchDeviceEvents:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, json_data=DEVICE_EVENT_RESPONSE)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_device_events("cardiac monitor")
        assert result["total_count"] == 567
        assert result["results"][0]["event_type"] == "Malfunction"

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await search_device_events("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(403)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_device_events("pacemaker")
        assert "error" in result
        assert "403" in result["error"]

    @pytest.mark.asyncio
    async def test_max_results_capped(self):
        mock_resp = _mock_response(200, json_data=EMPTY_RESPONSE)
        with patch("mcp_servers.openfda.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_device_events("ventilator", max_results=500)
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert params["limit"] == 100
