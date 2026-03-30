"""Unit tests for the Medical Imaging MCP server."""

import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.imaging import server as imaging_module

search_clearances = imaging_module.search_imaging_device_clearances
search_events = imaging_module.search_imaging_device_events
search_classifications = imaging_module.search_imaging_device_classifications
get_modality = imaging_module.get_imaging_modality_info


# ── Fixtures ──────────────────────────────────────────────────


def _mock_response(status_code, json_data):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


SAMPLE_510K = {
    "meta": {"results": {"total": 42}},
    "results": [
        {
            "k_number": "K223456",
            "device_name": "CT Scanner System",
            "applicant": "Siemens Healthineers",
            "decision_date": "2023-06-15",
            "decision_description": "Substantially Equivalent",
            "product_code": "JAK",
            "review_panel": "Radiology",
            "advisory_committee_description": "Radiology",
            "statement_or_summary": "Summary",
        }
    ],
}

SAMPLE_DEVICE_EVENT = {
    "meta": {"results": {"total": 18}},
    "results": [
        {
            "report_number": "1234567-2023-00001",
            "event_type": "Malfunction",
            "date_received": "20230801",
            "device": [
                {
                    "brand_name": "MAGNETOM Vida",
                    "generic_name": "MRI Scanner",
                    "manufacturer_d_name": "Siemens",
                    "model_number": "VIDA-3T",
                }
            ],
            "mdr_text": [
                {
                    "text_type_code": "Description of Event or Problem",
                    "text": "System shut down unexpectedly during scan.",
                }
            ],
        }
    ],
}

SAMPLE_CLASSIFICATION = {
    "meta": {"results": {"total": 5}},
    "results": [
        {
            "product_code": "JAK",
            "device_name": "System, X-Ray, Tomography, Computed",
            "device_class": "2",
            "medical_specialty_description": "Radiology",
            "regulation_number": "892.1750",
            "definition": "Computed tomography x-ray system.",
        }
    ],
}


# ── 510(k) clearance tests ────────────────────────────────────


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_clearances_success(mock_get):
    mock_get.return_value = _mock_response(200, SAMPLE_510K)
    result = await search_clearances(query="CT scanner", max_results=5)

    assert result["query"] == "CT scanner"
    assert result["total_count"] == 42
    assert result["returned_count"] == 1
    assert result["results"][0]["k_number"] == "K223456"
    assert result["results"][0]["applicant"] == "Siemens Healthineers"


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_clearances_empty_query(mock_get):
    result = await search_clearances(query="", max_results=5)
    assert result["error"] == "Empty query after sanitization"
    mock_get.assert_not_called()


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_clearances_api_error(mock_get):
    mock_get.return_value = _mock_response(500, {})
    result = await search_clearances(query="MRI", max_results=5)
    assert "error" in result
    assert result["results"] == []


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_clearances_invalid_json(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = json.JSONDecodeError("", "", 0)
    mock_get.return_value = resp
    result = await search_clearances(query="ultrasound")
    assert "error" in result


# ── Device event tests ────────────────────────────────────────


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_events_success(mock_get):
    mock_get.return_value = _mock_response(200, SAMPLE_DEVICE_EVENT)
    result = await search_events(query="MRI malfunction", max_results=5)

    assert result["total_count"] == 18
    assert result["returned_count"] == 1
    event = result["results"][0]
    assert event["report_number"] == "1234567-2023-00001"
    assert event["event_type"] == "Malfunction"
    assert event["devices"][0]["brand_name"] == "MAGNETOM Vida"


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_events_empty_query(mock_get):
    result = await search_events(query="")
    assert result["error"] == "Empty query after sanitization"


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_events_api_error(mock_get):
    mock_get.return_value = _mock_response(503, {})
    result = await search_events(query="CT burn")
    assert "error" in result


# ── Device classification tests ───────────────────────────────


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_classifications_success(mock_get):
    mock_get.return_value = _mock_response(200, SAMPLE_CLASSIFICATION)
    result = await search_classifications(query="computed tomography")

    assert result["total_count"] == 5
    assert result["returned_count"] == 1
    cls = result["results"][0]
    assert cls["product_code"] == "JAK"
    assert cls["device_class"] == "2"
    assert "Radiology" in cls["medical_specialty_description"]


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_search_classifications_empty(mock_get):
    result = await search_classifications(query="")
    assert result["error"] == "Empty query after sanitization"


# ── Modality info tests ───────────────────────────────────────


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_get_modality_known(mock_get):
    mock_get.return_value = _mock_response(200, SAMPLE_CLASSIFICATION)
    result = await get_modality(modality="ct")

    assert result["modality_code"] == "ct"
    assert result["modality_name"] == "Computed Tomography"
    assert result["known_modality"] is True
    assert result["total_classifications"] >= 0


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_get_modality_unknown(mock_get):
    mock_get.return_value = _mock_response(200, {"results": []})
    result = await get_modality(modality="holography")

    assert result["modality_code"] == "holography"
    assert result["known_modality"] is False


@patch("mcp_servers.imaging.server.resilient_get", new_callable=AsyncMock)
async def test_get_modality_api_failure(mock_get):
    mock_get.side_effect = Exception("Connection refused")
    result = await get_modality(modality="mri")

    assert result["modality_code"] == "mri"
    assert result["known_modality"] is True
    assert result["fda_classifications"] == []
