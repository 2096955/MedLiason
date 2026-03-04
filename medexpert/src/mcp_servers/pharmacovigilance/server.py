"""Pharmacovigilance / Drug Safety Signal MCP Server.

Provides tools for searching drug safety signals, risk evaluation and
mitigation strategies (REMS), and post-market safety reports. Aggregates
data from OpenFDA FAERS and drug labeling APIs to provide a pharmacovigilance
perspective distinct from the general drug specialist.

API sources:
  - OpenFDA FAERS (drug adverse events with signal detection focus)
  - OpenFDA drug labeling (boxed warnings, REMS)

Port: 9015
"""

import json
import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get, CircuitOpenError, RetryExhaustedError, structured_error_response
from mcp_servers._security import sanitize_query

log = logging.getLogger(__name__)

mcp = FastMCP("Pharmacovigilance")

OPENFDA_BASE_URL = "https://api.fda.gov"
OPENFDA_DRUG_EVENT_URL = f"{OPENFDA_BASE_URL}/drug/event.json"
OPENFDA_DRUG_LABEL_URL = f"{OPENFDA_BASE_URL}/drug/label.json"


def _parse_safety_signal(event: dict) -> dict:
    """Extract pharmacovigilance-focused fields from a FAERS record."""
    patient = event.get("patient", {})
    drugs = []
    for drug in patient.get("drug", []):
        drugs.append(
            {
                "name": drug.get("medicinalproduct", ""),
                "characterization": drug.get("drugcharacterization", ""),
                "indication": drug.get("drugindication", ""),
                "action_taken": drug.get("actiondrug", ""),
                "route": drug.get("drugadministrationroute", ""),
            }
        )

    reactions = []
    for reaction in patient.get("reaction", []):
        reactions.append(
            {
                "term": reaction.get("reactionmeddrapt", ""),
                "outcome": reaction.get("reactionoutcome", ""),
                "version": reaction.get("reactionmeddraversionpt", ""),
            }
        )

    return {
        "safety_report_id": event.get("safetyreportid", ""),
        "report_type": event.get("reporttype", ""),
        "serious": event.get("serious", ""),
        "serious_reasons": {
            "death": event.get("seriousnessdeath", ""),
            "hospitalization": event.get("seriousnesshospitalization", ""),
            "life_threatening": event.get("seriousnesslifethreatening", ""),
            "disability": event.get("seriousnessdisabling", ""),
            "congenital_anomaly": event.get("seriousnesscongenitalanomali", ""),
            "other": event.get("seriousnessother", ""),
        },
        "receive_date": event.get("receivedate", ""),
        "patient_age": patient.get("patientonsetage", ""),
        "patient_sex": patient.get("patientsex", ""),
        "patient_weight": patient.get("patientweight", ""),
        "drugs": drugs,
        "reactions": reactions,
    }


def _parse_boxed_warning(label: dict) -> dict:
    """Extract boxed warnings and REMS info from a drug label."""
    openfda = label.get("openfda", {})
    return {
        "brand_name": openfda.get("brand_name", []),
        "generic_name": openfda.get("generic_name", []),
        "manufacturer": openfda.get("manufacturer_name", []),
        "boxed_warning": label.get("boxed_warning", []),
        "warnings_and_cautions": label.get("warnings_and_cautions", []),
        "adverse_reactions": label.get("adverse_reactions", []),
        "drug_interactions": label.get("drug_interactions", []),
        "contraindications": label.get("contraindications", []),
        "pregnancy": label.get("pregnancy", []),
        "nursing_mothers": label.get("nursing_mothers", []),
        "pediatric_use": label.get("pediatric_use", []),
    }


@mcp.tool()
async def search_serious_adverse_events(
    drug_name: str, max_results: int = 10
) -> dict:
    """Search for serious adverse events associated with a drug in FAERS.

    Focuses on serious reports (deaths, hospitalizations, life-threatening events)
    for pharmacovigilance signal detection.

    Args:
        drug_name: Drug name to search (generic or brand).
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dictionary with serious adverse event reports and signal summary.
    """
    safe_drug = sanitize_query(drug_name)
    if not safe_drug:
        return {"error": "Empty drug name after sanitization", "results": []}

    params = {
        "search": f'patient.drug.medicinalproduct:"{safe_drug}"+AND+serious:1',
        "limit": min(max_results, 100),
        "sort": "receivedate:desc",
    }

    try:
        response = await resilient_get(OPENFDA_DRUG_EVENT_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "pharmacovigilance", "search_serious_adverse_events"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA FAERS API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse FAERS JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_safety_signal(e) for e in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    # Aggregate reaction terms for signal detection
    reaction_counts: dict[str, int] = {}
    for result in results:
        for reaction in result.get("reactions", []):
            term = reaction.get("term", "").lower()
            if term:
                reaction_counts[term] = reaction_counts.get(term, 0) + 1

    top_reactions = sorted(reaction_counts.items(), key=lambda x: -x[1])[:20]

    return {
        "success": True,
        "drug_name": safe_drug,
        "total_serious_reports": meta.get("total", 0),
        "returned_count": len(results),
        "top_reactions": [{"term": t, "count": c} for t, c in top_reactions],
        "results": results,
    }


@mcp.tool()
async def search_death_reports(drug_name: str, max_results: int = 10) -> dict:
    """Search for FAERS reports where patient death was reported.

    Specifically filters for adverse event reports with seriousnessdeath=1.

    Args:
        drug_name: Drug name to search (generic or brand).
        max_results: Maximum number of results to return (default 10).

    Returns:
        Dictionary with death-associated adverse event reports.
    """
    safe_drug = sanitize_query(drug_name)
    if not safe_drug:
        return {"error": "Empty drug name after sanitization", "results": []}

    params = {
        "search": (
            f'patient.drug.medicinalproduct:"{safe_drug}"'
            "+AND+seriousnessdeath:1"
        ),
        "limit": min(max_results, 100),
        "sort": "receivedate:desc",
    }

    try:
        response = await resilient_get(OPENFDA_DRUG_EVENT_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "pharmacovigilance", "search_death_reports"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA FAERS API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse FAERS death reports JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_safety_signal(e) for e in data.get("results", [])]
    meta = data.get("meta", {}).get("results", {})

    return {
        "success": True,
        "drug_name": safe_drug,
        "total_death_reports": meta.get("total", 0),
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def get_boxed_warnings(drug_name: str) -> dict:
    """Retrieve FDA boxed warnings (black box warnings) for a drug.

    Boxed warnings are the FDA's most serious safety warnings and indicate
    significant risks of serious or life-threatening adverse effects.

    Args:
        drug_name: Drug name to search (generic or brand).

    Returns:
        Dictionary with boxed warnings, contraindications, and safety info.
    """
    safe_drug = sanitize_query(drug_name)
    if not safe_drug:
        return {"error": "Empty drug name after sanitization", "results": []}

    params = {
        "search": f'openfda.generic_name:"{safe_drug}"+openfda.brand_name:"{safe_drug}"',
        "limit": 5,
    }

    try:
        response = await resilient_get(OPENFDA_DRUG_LABEL_URL, params=params)
    except (CircuitOpenError, RetryExhaustedError) as exc:
        return {**structured_error_response(exc, "pharmacovigilance", "get_boxed_warnings"), "results": []}

    if response.status_code != 200:
        return {
            "error": f"OpenFDA drug label API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse drug label JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = [_parse_boxed_warning(l) for l in data.get("results", [])]

    has_boxed_warning = any(
        bool(r.get("boxed_warning")) for r in results
    )

    return {
        "success": True,
        "drug_name": safe_drug,
        "has_boxed_warning": has_boxed_warning,
        "returned_count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9015)
