"""Medical Imaging / Radiology MCP Server.

Provides tools for searching medical imaging studies, DICOM metadata,
and radiology-related information via OpenFDA device data and
LOINC radiology codes.

API sources:
  - OpenFDA MAUDE (device adverse events with imaging devices)
  - OpenFDA 510(k) (imaging device clearances)
  - LOINC Radiology codes (via FHIR terminology server)

Port: 9014
"""

import json
import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get
from mcp_servers._security import sanitize_query

log = logging.getLogger(__name__)

mcp = FastMCP("Medical Imaging")

OPENFDA_BASE_URL = "https://api.fda.gov"
OPENFDA_DEVICE_510K_URL = f"{OPENFDA_BASE_URL}/device/510k.json"
OPENFDA_DEVICE_EVENT_URL = f"{OPENFDA_BASE_URL}/device/event.json"
OPENFDA_DEVICE_CLASS_URL = f"{OPENFDA_BASE_URL}/device/classification.json"

# Common imaging modalities for structured search
IMAGING_MODALITIES = {
    "ct": "Computed Tomography",
    "mri": "Magnetic Resonance Imaging",
    "xray": "X-Ray Radiography",
    "ultrasound": "Ultrasound",
    "pet": "Positron Emission Tomography",
    "mammography": "Mammography",
    "fluoroscopy": "Fluoroscopy",
    "nuclear": "Nuclear Medicine",
    "dexa": "Dual-Energy X-Ray Absorptiometry",
}


def _parse_imaging_device_clearance(record: dict) -> dict:
    """Extract key fields from a 510(k) imaging device clearance."""
    return {
        "k_number": record.get("k_number", ""),
        "device_name": record.get("device_name", ""),
        "applicant": record.get("applicant", ""),
        "decision_date": record.get("decision_date", ""),
        "decision_description": record.get("decision_description", ""),
        "product_code": record.get("product_code", ""),
        "review_panel": record.get("review_panel", ""),
        "advisory_committee_description": record.get(
            "advisory_committee_description", ""
        ),
        "statement_or_summary": record.get("statement_or_summary", ""),
    }


def _parse_imaging_device_event(event: dict) -> dict:
    """Extract key fields from a device adverse event related to imaging."""
    devices = []
    for device in event.get("device", []):
        devices.append(
            {
                "brand_name": device.get("brand_name", ""),
                "generic_name": device.get("generic_name", ""),
                "manufacturer": device.get("manufacturer_d_name", ""),
                "model_number": device.get("model_number", ""),
            }
        )

    return {
        "report_number": event.get("report_number", ""),
        "event_type": event.get("event_type", ""),
        "date_received": event.get("date_received", ""),
        "devices": devices,
        "mdr_text": [
            {"text_type": t.get("text_type_code", ""), "text": t.get("text", "")}
            for t in event.get("mdr_text", [])[:3]
        ],
    }


def _parse_device_classification(record: dict) -> dict:
    """Extract key fields from a device classification record."""
    return {
        "product_code": record.get("product_code", ""),
        "device_name": record.get("device_name", ""),
        "device_class": record.get("device_class", ""),
        "medical_specialty_description": record.get(
            "medical_specialty_description", ""
        ),
        "regulation_number": record.get("regulation_number", ""),
        "definition": record.get("definition", ""),
    }


@mcp.tool()
async def search_imaging_device_clearances(
    query: str, max_results: int = 10
) -> dict:
    """Search FDA 510(k) clearances for imaging and radiology devices.

    Returns device clearance records including device name, applicant,
    decision date, and product codes for imaging equipment.

    Args:
        query: Search term (e.g., "MRI", "CT scanner", "ultrasound probe").
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dictionary with matched imaging device clearances.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": f'"{safe_query}"',
        "limit": min(max_results, 100),
    }

    response = await resilient_get(OPENFDA_DEVICE_510K_URL, params=params)
    if response.status_code != 200:
        return {
            "error": f"OpenFDA 510(k) API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse 510(k) JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_imaging_device_clearance(r) for r in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_imaging_device_events(
    query: str, max_results: int = 10
) -> dict:
    """Search FDA MAUDE adverse event reports for imaging/radiology devices.

    Returns adverse event reports for imaging equipment including CT, MRI,
    X-ray, ultrasound, and other modalities.

    Args:
        query: Search term (e.g., "CT scanner malfunction", "MRI burn").
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dictionary with matched imaging device adverse events.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": f'"{safe_query}"',
        "limit": min(max_results, 100),
    }

    response = await resilient_get(OPENFDA_DEVICE_EVENT_URL, params=params)
    if response.status_code != 200:
        return {
            "error": f"OpenFDA device events API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse device events JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_imaging_device_event(e) for e in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_imaging_device_classifications(
    query: str, max_results: int = 10
) -> dict:
    """Search FDA device classification database for imaging/radiology devices.

    Returns device classification data including product codes, device class
    (I/II/III), medical specialty, and regulatory requirements.

    Args:
        query: Search term (e.g., "computed tomography", "ultrasonic scanner").
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dictionary with matched device classification records.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": f'"{safe_query}"',
        "limit": min(max_results, 100),
    }

    response = await resilient_get(OPENFDA_DEVICE_CLASS_URL, params=params)
    if response.status_code != 200:
        return {
            "error": f"OpenFDA device classification API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse device classification JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_device_classification(r) for r in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def get_imaging_modality_info(modality: str) -> dict:
    """Get information about an imaging modality and its associated FDA devices.

    Provides a summary of the modality and searches for related device
    classifications in the FDA database.

    Args:
        modality: Imaging modality code or name (e.g., "ct", "mri", "ultrasound").

    Returns:
        Dictionary with modality details and related FDA device classifications.
    """
    modality_lower = modality.strip().lower()
    full_name = IMAGING_MODALITIES.get(modality_lower, modality)

    # Search for device classifications related to this modality
    safe_modality = sanitize_query(full_name)
    if not safe_modality:
        return {"error": "Invalid modality", "modality": modality}

    params = {
        "search": f'"{safe_modality}"',
        "limit": 10,
    }

    classifications = []
    try:
        response = await resilient_get(OPENFDA_DEVICE_CLASS_URL, params=params)
        if response.status_code == 200:
            data = response.json()
            classifications = [
                _parse_device_classification(r) for r in data.get("results", [])
            ]
    except Exception as exc:
        log.error("Failed to fetch modality classifications: %s", exc)

    return {
        "modality_code": modality_lower,
        "modality_name": full_name,
        "known_modality": modality_lower in IMAGING_MODALITIES,
        "fda_classifications": classifications,
        "total_classifications": len(classifications),
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9014)
