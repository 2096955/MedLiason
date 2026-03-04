"""OpenFDA API MCP server.

Provides tools for searching FDA drug adverse events, drug labels,
drug recalls/enforcement actions, and medical device adverse events.

API: OpenFDA -- JSON responses, free, 40 requests/minute.
"""

import json
import os
import sys
import logging

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get, CircuitOpenError, RetryExhaustedError, structured_error_response
from mcp_servers._security import sanitize_query
from lifesci_common.constants import (
    OPENFDA_DRUG_EVENT_URL,
    OPENFDA_DRUG_LABEL_URL,
    OPENFDA_DRUG_RECALL_URL,
    OPENFDA_DEVICE_EVENT_URL,
)

log = logging.getLogger(__name__)

mcp = FastMCP("openfda")


def _parse_drug_event(event: dict) -> dict:
    """Extract key fields from a drug adverse event record."""
    patient = event.get("patient", {})
    drugs = []
    for drug in patient.get("drug", []):
        drugs.append({
            "name": drug.get("medicinalproduct", ""),
            "indication": drug.get("drugindication", ""),
            "characterization": drug.get("drugcharacterization", ""),
            "route": drug.get("drugadministrationroute", ""),
            "dosage": drug.get("drugdosagetext", ""),
        })

    reactions = []
    for reaction in patient.get("reaction", []):
        reactions.append({
            "term": reaction.get("reactionmeddrapt", ""),
            "outcome": reaction.get("reactionoutcome", ""),
        })

    return {
        "safety_report_id": event.get("safetyreportid", ""),
        "receive_date": event.get("receivedate", ""),
        "serious": event.get("serious", ""),
        "sender_organization": event.get("sender", {}).get("senderorganization", ""),
        "patient_age": patient.get("patientonsetage", ""),
        "patient_sex": patient.get("patientsex", ""),
        "drugs": drugs,
        "reactions": reactions,
    }


def _parse_drug_label(label: dict) -> dict:
    """Extract key fields from a drug label record."""
    openfda = label.get("openfda", {})
    return {
        "spl_id": label.get("id", ""),
        "brand_name": openfda.get("brand_name", []),
        "generic_name": openfda.get("generic_name", []),
        "manufacturer": openfda.get("manufacturer_name", []),
        "route": openfda.get("route", []),
        "product_type": openfda.get("product_type", []),
        "indications_and_usage": label.get("indications_and_usage", []),
        "warnings": label.get("warnings", []),
        "adverse_reactions": label.get("adverse_reactions", []),
        "dosage_and_administration": label.get("dosage_and_administration", []),
        "contraindications": label.get("contraindications", []),
        "drug_interactions": label.get("drug_interactions", []),
    }


def _parse_recall(recall: dict) -> dict:
    """Extract key fields from a drug recall/enforcement record."""
    openfda = recall.get("openfda", {})
    return {
        "recall_number": recall.get("recall_number", ""),
        "event_id": recall.get("event_id", ""),
        "status": recall.get("status", ""),
        "classification": recall.get("classification", ""),
        "reason_for_recall": recall.get("reason_for_recall", ""),
        "product_description": recall.get("product_description", ""),
        "recalling_firm": recall.get("recalling_firm", ""),
        "city": recall.get("city", ""),
        "state": recall.get("state", ""),
        "country": recall.get("country", ""),
        "recall_initiation_date": recall.get("recall_initiation_date", ""),
        "voluntary_mandated": recall.get("voluntary_mandated", ""),
        "brand_name": openfda.get("brand_name", []),
    }


def _parse_device_event(event: dict) -> dict:
    """Extract key fields from a device adverse event record."""
    devices = []
    for device in event.get("device", []):
        devices.append({
            "brand_name": device.get("brand_name", ""),
            "generic_name": device.get("generic_name", ""),
            "manufacturer": device.get("manufacturer_d_name", ""),
            "model_number": device.get("model_number", ""),
            "catalog_number": device.get("catalog_number", ""),
            "device_class": device.get("device_report_product_code", ""),
        })

    return {
        "report_number": event.get("report_number", ""),
        "event_type": event.get("event_type", ""),
        "date_received": event.get("date_received", ""),
        "adverse_event_flag": event.get("adverse_event_flag", ""),
        "product_problem_flag": event.get("product_problem_flag", ""),
        "devices": devices,
        "mdr_text": [
            {
                "text_type": t.get("text_type_code", ""),
                "text": t.get("text", ""),
            }
            for t in event.get("mdr_text", [])
        ],
    }


def _build_search_query(query: str) -> str:
    """Build an OpenFDA search query string from a sanitized query."""
    # OpenFDA uses Lucene-like query syntax; wrap in quotes for phrase search
    return f'"{query}"'


@mcp.tool()
async def search_drug_events(query: str, max_results: int = 10) -> dict:
    """Search FDA drug adverse event reports (FAERS) by drug name, reaction, or keyword.

    Returns adverse event reports including patient demographics, drug info,
    and reported reactions.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": _build_search_query(safe_query),
        "limit": min(max_results, 100),
    }

    try:
        response = await resilient_get(OPENFDA_DRUG_EVENT_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "openfda", "search_drug_events"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA drug events API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse drug events JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_drug_event(e) for e in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_drug_labels(query: str, max_results: int = 10) -> dict:
    """Search FDA drug labeling (prescribing information, package inserts).

    Returns structured drug label data including indications, warnings,
    adverse reactions, dosage, and contraindications.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": _build_search_query(safe_query),
        "limit": min(max_results, 100),
    }

    try:
        response = await resilient_get(OPENFDA_DRUG_LABEL_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "openfda", "search_drug_labels"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA drug labels API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse drug labels JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_drug_label(l) for l in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_drug_recalls(query: str, max_results: int = 5) -> dict:
    """Search FDA drug recall and enforcement actions.

    Returns recall notices including classification, reason, and recalling firm.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": _build_search_query(safe_query),
        "limit": min(max_results, 100),
    }

    try:
        response = await resilient_get(OPENFDA_DRUG_RECALL_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "openfda", "search_drug_recalls"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA drug recalls API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse drug recalls JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_recall(r) for r in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_device_events(query: str, max_results: int = 10) -> dict:
    """Search FDA medical device adverse event reports (MAUDE).

    Returns device event reports including device information, event type,
    and narrative descriptions.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": _build_search_query(safe_query),
        "limit": min(max_results, 100),
    }

    try:
        response = await resilient_get(OPENFDA_DEVICE_EVENT_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "openfda", "search_device_events"), "results": []}

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

    results = [_parse_device_event(e) for e in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9003)
