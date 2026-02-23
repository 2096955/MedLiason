"""Unit tests for the Pharmacovigilance MCP server."""

import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.pharmacovigilance import server as pv_module

search_serious = pv_module.search_serious_adverse_events.fn
search_deaths = pv_module.search_death_reports.fn
get_warnings = pv_module.get_boxed_warnings.fn


# ── Fixtures ──────────────────────────────────────────────────


def _mock_response(status_code, json_data):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


SAMPLE_SERIOUS_EVENT = {
    "meta": {"results": {"total": 1500}},
    "results": [
        {
            "safetyreportid": "SR-001",
            "reporttype": "1",
            "serious": "1",
            "seriousnessdeath": "",
            "seriousnesshospitalization": "1",
            "seriousnesslifethreatening": "",
            "seriousnessdisabling": "",
            "seriousnesscongenitalanomali": "",
            "seriousnessother": "",
            "receivedate": "20240115",
            "patient": {
                "patientonsetage": "65",
                "patientsex": "2",
                "patientweight": "72",
                "drug": [
                    {
                        "medicinalproduct": "METFORMIN",
                        "drugcharacterization": "1",
                        "drugindication": "Diabetes",
                        "actiondrug": "Drug Withdrawn",
                        "drugadministrationroute": "Oral",
                    }
                ],
                "reaction": [
                    {
                        "reactionmeddrapt": "Lactic acidosis",
                        "reactionoutcome": "2",
                        "reactionmeddraversionpt": "26.1",
                    },
                    {
                        "reactionmeddrapt": "Nausea",
                        "reactionoutcome": "1",
                        "reactionmeddraversionpt": "26.1",
                    },
                ],
            },
        },
        {
            "safetyreportid": "SR-002",
            "serious": "1",
            "receivedate": "20240201",
            "patient": {
                "drug": [{"medicinalproduct": "METFORMIN"}],
                "reaction": [
                    {"reactionmeddrapt": "Lactic acidosis", "reactionoutcome": "5"},
                ],
            },
        },
    ],
}

SAMPLE_DEATH_REPORT = {
    "meta": {"results": {"total": 200}},
    "results": [
        {
            "safetyreportid": "DR-001",
            "serious": "1",
            "seriousnessdeath": "1",
            "receivedate": "20240301",
            "patient": {
                "patientonsetage": "78",
                "patientsex": "1",
                "drug": [{"medicinalproduct": "DRUG_X", "drugcharacterization": "1"}],
                "reaction": [
                    {"reactionmeddrapt": "Cardiac arrest", "reactionoutcome": "5"}
                ],
            },
        }
    ],
}

SAMPLE_BOXED_WARNING = {
    "results": [
        {
            "openfda": {
                "brand_name": ["Actos"],
                "generic_name": ["pioglitazone"],
                "manufacturer_name": ["Takeda"],
            },
            "boxed_warning": [
                "WARNING: CONGESTIVE HEART FAILURE. Thiazolidinediones cause fluid retention."
            ],
            "warnings_and_cautions": ["Monitor for signs of heart failure."],
            "adverse_reactions": ["Edema, weight gain, fractures."],
            "drug_interactions": ["CYP2C8 inhibitors may increase levels."],
            "contraindications": ["NYHA Class III or IV heart failure."],
            "pregnancy": ["Category C."],
            "nursing_mothers": [],
            "pediatric_use": ["Safety not established."],
        }
    ],
}


# ── Serious adverse events ────────────────────────────────────


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_search_serious_events_success(mock_get):
    mock_get.return_value = _mock_response(200, SAMPLE_SERIOUS_EVENT)
    result = await search_serious(drug_name="metformin", max_results=5)

    assert result["drug_name"] == "metformin"
    assert result["total_serious_reports"] == 1500
    assert result["returned_count"] == 2

    event = result["results"][0]
    assert event["safety_report_id"] == "SR-001"
    assert event["serious"] == "1"
    assert event["serious_reasons"]["hospitalization"] == "1"
    assert event["drugs"][0]["name"] == "METFORMIN"
    assert event["reactions"][0]["term"] == "Lactic acidosis"

    # Verify signal aggregation
    assert any(r["term"] == "lactic acidosis" for r in result["top_reactions"])
    lactic = next(r for r in result["top_reactions"] if r["term"] == "lactic acidosis")
    assert lactic["count"] == 2


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_search_serious_events_empty_drug(mock_get):
    result = await search_serious(drug_name="")
    assert result["error"] == "Empty drug name after sanitization"
    mock_get.assert_not_called()


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_search_serious_events_api_error(mock_get):
    mock_get.return_value = _mock_response(429, {})
    result = await search_serious(drug_name="aspirin")
    assert "error" in result
    assert result["results"] == []


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_search_serious_events_invalid_json(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = json.JSONDecodeError("", "", 0)
    mock_get.return_value = resp
    result = await search_serious(drug_name="ibuprofen")
    assert "error" in result


# ── Death reports ─────────────────────────────────────────────


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_search_death_reports_success(mock_get):
    mock_get.return_value = _mock_response(200, SAMPLE_DEATH_REPORT)
    result = await search_deaths(drug_name="drug_x", max_results=5)

    assert result["drug_name"] == "drug_x"
    assert result["total_death_reports"] == 200
    assert result["returned_count"] == 1
    event = result["results"][0]
    assert event["safety_report_id"] == "DR-001"
    assert event["reactions"][0]["term"] == "Cardiac arrest"


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_search_death_reports_empty_drug(mock_get):
    result = await search_deaths(drug_name="")
    assert "error" in result
    mock_get.assert_not_called()


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_search_death_reports_api_error(mock_get):
    mock_get.return_value = _mock_response(500, {})
    result = await search_deaths(drug_name="warfarin")
    assert "error" in result


# ── Boxed warnings ────────────────────────────────────────────


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_get_boxed_warnings_success(mock_get):
    mock_get.return_value = _mock_response(200, SAMPLE_BOXED_WARNING)
    result = await get_warnings(drug_name="pioglitazone")

    assert result["drug_name"] == "pioglitazone"
    assert result["has_boxed_warning"] is True
    assert result["returned_count"] == 1

    label = result["results"][0]
    assert "CONGESTIVE HEART FAILURE" in label["boxed_warning"][0]
    assert label["brand_name"] == ["Actos"]
    assert label["contraindications"] == ["NYHA Class III or IV heart failure."]


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_get_boxed_warnings_none_found(mock_get):
    mock_get.return_value = _mock_response(200, {
        "results": [
            {
                "openfda": {"brand_name": ["Tylenol"], "generic_name": ["acetaminophen"]},
                "boxed_warning": [],
                "warnings_and_cautions": ["Hepatotoxicity risk."],
            }
        ]
    })
    result = await get_warnings(drug_name="acetaminophen")

    assert result["has_boxed_warning"] is False


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_get_boxed_warnings_empty_drug(mock_get):
    result = await get_warnings(drug_name="")
    assert "error" in result
    mock_get.assert_not_called()


@patch("mcp_servers.pharmacovigilance.server.resilient_get", new_callable=AsyncMock)
async def test_get_boxed_warnings_api_error(mock_get):
    mock_get.return_value = _mock_response(404, {})
    result = await get_warnings(drug_name="fake_drug")
    assert "error" in result
