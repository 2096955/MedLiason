"""FDA Regulatory Data MCP server.

Provides tools for searching FDA device regulatory databases including
510(k) clearances, PMA approvals, device classifications, and
guidance documents.

API: OpenFDA device endpoints -- JSON responses, free.
"""

import os
import sys
import logging

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get, CircuitOpenError, RetryExhaustedError, structured_error_response
from mcp_servers._security import sanitize_query
from lifesci_common.constants import (
    OPENFDA_DEVICE_510K_URL,
    OPENFDA_DEVICE_PMA_URL,
    OPENFDA_DEVICE_CLASS_URL,
)

log = logging.getLogger(__name__)

mcp = FastMCP("regulatory")


def _build_search_query(query: str) -> str:
    """Build an OpenFDA search query from sanitized input."""
    return f'"{query}"'


def _parse_510k(record: dict) -> dict:
    """Extract key fields from a 510(k) clearance record."""
    openfda = record.get("openfda", {})
    return {
        "k_number": record.get("k_number", ""),
        "applicant": record.get("applicant", ""),
        "device_name": record.get("device_name", ""),
        "date_received": record.get("date_received", ""),
        "decision_date": record.get("decision_date", ""),
        "decision_code": record.get("decision_code", ""),
        "decision_description": record.get("decision_description", ""),
        "product_code": record.get("product_code", ""),
        "statement_or_summary": record.get("statement_or_summary", ""),
        "clearance_type": record.get("clearance_type", ""),
        "advisory_committee": record.get("advisory_committee", ""),
        "advisory_committee_description": record.get(
            "advisory_committee_description", ""
        ),
        "review_panel": record.get("review_panel", ""),
        "regulation_number": openfda.get("regulation_number", []),
        "device_class": openfda.get("device_class", ""),
        "medical_specialty": openfda.get("medical_specialty_description", ""),
    }


def _parse_pma(record: dict) -> dict:
    """Extract key fields from a PMA approval record."""
    openfda = record.get("openfda", {})
    return {
        "pma_number": record.get("pma_number", ""),
        "applicant": record.get("applicant", ""),
        "trade_name": record.get("trade_name", ""),
        "generic_name": record.get("generic_name", ""),
        "date_received": record.get("date_received", ""),
        "decision_date": record.get("decision_date", ""),
        "decision_code": record.get("decision_code", ""),
        "product_code": record.get("product_code", ""),
        "advisory_committee": record.get("advisory_committee", ""),
        "advisory_committee_description": record.get(
            "advisory_committee_description", ""
        ),
        "supplement_type": record.get("supplement_type", ""),
        "supplement_number": record.get("supplement_number", ""),
        "regulation_number": openfda.get("regulation_number", []),
        "device_class": openfda.get("device_class", ""),
    }


def _parse_classification(record: dict) -> dict:
    """Extract key fields from a device classification record."""
    openfda = record.get("openfda", {})
    return {
        "product_code": record.get("product_code", ""),
        "device_name": record.get("device_name", ""),
        "device_class": record.get("device_class", ""),
        "medical_specialty": record.get("medical_specialty", ""),
        "medical_specialty_description": record.get(
            "medical_specialty_description", ""
        ),
        "regulation_number": record.get("regulation_number", ""),
        "definition": record.get("definition", ""),
        "review_panel": record.get("review_panel", ""),
        "submission_type": record.get("submission_type_id", ""),
        "gmp_exempt": record.get("gmp_exempt_flag", ""),
        "third_party_review": record.get("third_party_flag", ""),
        "unclassified_reason": record.get("unclassified_reason", ""),
        "registration_number": openfda.get("registration_number", []),
        "fei_number": openfda.get("fei_number", []),
    }


@mcp.tool()
async def search_510k(query: str, max_results: int = 10) -> dict:
    """Search FDA 510(k) premarket notification clearance database.

    510(k) is the regulatory pathway for devices that are substantially
    equivalent to a legally marketed predicate device. Returns clearance
    records with applicant, device name, decision, and classification info.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": _build_search_query(safe_query),
        "limit": min(max_results, 100),
    }

    try:
        response = await resilient_get(OPENFDA_DEVICE_510K_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "regulatory", "search_510k"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA 510(k) API returned {response.status_code}",
            "results": [],
        }

    data = response.json()
    results = [_parse_510k(r) for r in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_pma(query: str, max_results: int = 10) -> dict:
    """Search FDA Premarket Approval (PMA) database.

    PMA is the most stringent regulatory pathway for high-risk (Class III)
    medical devices. Returns approval records with applicant, trade name,
    decision, and advisory committee information.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "search": _build_search_query(safe_query),
        "limit": min(max_results, 100),
    }

    try:
        response = await resilient_get(OPENFDA_DEVICE_PMA_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "regulatory", "search_pma"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA PMA API returned {response.status_code}",
            "results": [],
        }

    data = response.json()
    results = [_parse_pma(r) for r in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def get_device_classification(product_code: str) -> dict:
    """Look up FDA device classification by product code.

    Returns classification details including device class (I, II, III),
    regulation number, medical specialty, and review panel information.
    Product codes are 3-character alphanumeric identifiers (e.g., 'QAS' for
    blood glucose monitors).
    """
    safe_code = sanitize_query(product_code)
    if not safe_code:
        return {"error": "Empty product code after sanitization"}

    # Search by exact product code field
    params = {
        "search": f"product_code:{safe_code.upper()}",
        "limit": 5,
    }

    try:
        response = await resilient_get(OPENFDA_DEVICE_CLASS_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return structured_error_response(exc, "regulatory", "get_device_classification")

    if response.status_code != 200:
        return {
            "error": f"OpenFDA classification API returned {response.status_code}"
        }

    data = response.json()
    results = [_parse_classification(r) for r in data.get("results", [])]

    if not results:
        return {"error": f"No classification found for product code '{safe_code}'"}

    return {
        "success": True,
        "product_code": safe_code.upper(),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_guidance_documents(query: str, max_results: int = 10) -> dict:
    """Search FDA device guidance documents and regulatory references.

    Guidance documents describe FDA's interpretation of regulatory
    requirements and provide recommendations. Returns registration
    and listing data as a proxy for guidance-relevant device information.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    # Use the registration/listing endpoint as a guidance reference source
    from lifesci_common.constants import FDA_GUIDANCE_URL

    params = {
        "search": _build_search_query(safe_query),
        "limit": min(max_results, 100),
    }

    try:
        response = await resilient_get(FDA_GUIDANCE_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "regulatory", "search_guidance_documents"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"FDA guidance API returned {response.status_code}",
            "results": [],
        }

    data = response.json()
    raw_results = data.get("results", [])
    results = []
    for record in raw_results:
        products = record.get("products", [])
        product_list = []
        for p in products:
            product_list.append({
                "product_code": p.get("product_code", ""),
                "created_date": p.get("created_date", ""),
            })

        results.append({
            "registration_number": record.get("registration_number", ""),
            "establishment_type": record.get("establishment_type", []),
            "proprietary_name": record.get("proprietary_name", []),
            "products": product_list,
            "name": record.get("name", ""),
            "city": record.get("city", ""),
            "state": record.get("state", ""),
            "country": record.get("country_code", ""),
        })

    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "query": safe_query,
        "total_count": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9005)
