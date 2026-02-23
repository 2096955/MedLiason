"""ClinicalTrials.gov v2 API MCP server.

Provides tools for searching clinical trials by query, condition, or
intervention, and retrieving detailed trial information by NCT ID.

API: ClinicalTrials.gov v2 -- JSON responses, free, no key required.
"""

import json
import os
import sys
import logging

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get
from mcp_servers._security import sanitize_query
from lifesci_common.constants import CLINICALTRIALS_STUDIES_URL

log = logging.getLogger(__name__)

mcp = FastMCP("clinicaltrials")

# Default fields to request from the v2 API
DEFAULT_FIELDS = (
    "NCTId,BriefTitle,OverallStatus,Phase,EnrollmentCount,"
    "StartDate,CompletionDate,Condition,InterventionName,"
    "LeadSponsorName,StudyType"
)

DETAIL_FIELDS = (
    "NCTId,BriefTitle,OfficialTitle,OverallStatus,Phase,"
    "EnrollmentCount,EnrollmentType,StartDate,CompletionDate,"
    "Condition,InterventionName,InterventionType,InterventionDescription,"
    "LeadSponsorName,CollaboratorName,StudyType,BriefSummary,"
    "DetailedDescription,EligibilityCriteria,MinimumAge,MaximumAge,"
    "Sex,PrimaryOutcomeMeasure,SecondaryOutcomeMeasure,"
    "LocationFacility,LocationCity,LocationState,LocationCountry,"
    "DesignAllocation,DesignInterventionModel,DesignPrimaryPurpose,"
    "DesignMasking,ResultsFirstPostDate"
)


def _parse_study(study: dict) -> dict:
    """Extract key fields from a v2 API study object."""
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {})
    conditions = protocol.get("conditionsModule", {})
    interventions = protocol.get("armsInterventionsModule", {})
    eligibility = protocol.get("eligibilityModule", {})
    description = protocol.get("descriptionModule", {})

    # Extract intervention names
    intervention_list = []
    for intv in interventions.get("interventions", []):
        name = intv.get("name", "")
        intv_type = intv.get("type", "")
        if name:
            intervention_list.append({"name": name, "type": intv_type})

    # Extract lead sponsor
    lead_sponsor = sponsor.get("leadSponsor", {})

    return {
        "nct_id": ident.get("nctId", ""),
        "brief_title": ident.get("briefTitle", ""),
        "official_title": ident.get("officialTitle", ""),
        "overall_status": status.get("overallStatus", ""),
        "phase": design.get("phases", []),
        "enrollment": design.get("enrollmentInfo", {}).get("count"),
        "enrollment_type": design.get("enrollmentInfo", {}).get("type", ""),
        "start_date": status.get("startDateStruct", {}).get("date", ""),
        "completion_date": status.get("completionDateStruct", {}).get("date", ""),
        "conditions": conditions.get("conditions", []),
        "interventions": intervention_list,
        "lead_sponsor": lead_sponsor.get("name", ""),
        "study_type": design.get("studyType", ""),
        "brief_summary": description.get("briefSummary", ""),
        "eligibility": {
            "criteria": eligibility.get("eligibilityCriteria", ""),
            "sex": eligibility.get("sex", ""),
            "minimum_age": eligibility.get("minimumAge", ""),
            "maximum_age": eligibility.get("maximumAge", ""),
        },
    }


def _parse_study_list(data: dict) -> list[dict]:
    """Parse a list of studies from the v2 API response."""
    studies = data.get("studies", [])
    return [_parse_study(s) for s in studies]


@mcp.tool()
async def search_trials(query: str, max_results: int = 10) -> dict:
    """Search ClinicalTrials.gov for clinical trials matching a free-text query.

    Returns a list of trials with key metadata (NCT ID, title, status, phase, etc.).
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Empty query after sanitization", "results": []}

    params = {
        "query.term": safe_query,
        "pageSize": min(max_results, 100),
        "format": "json",
    }

    response = await resilient_get(CLINICALTRIALS_STUDIES_URL, params=params)
    if response.status_code != 200:
        return {
            "error": f"ClinicalTrials.gov API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse ClinicalTrials JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = _parse_study_list(data)
    total_count = data.get("totalCount", len(results))

    return {
        "query": safe_query,
        "total_count": total_count,
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def get_trial_details(nct_id: str) -> dict:
    """Retrieve detailed information for a specific clinical trial by NCT ID.

    NCT IDs follow the pattern NCT followed by 8 digits (e.g., NCT04280705).
    """
    if not nct_id or not nct_id.strip().upper().startswith("NCT"):
        return {"error": "Invalid NCT ID: must start with 'NCT'"}

    clean_id = nct_id.strip().upper()
    url = f"{CLINICALTRIALS_STUDIES_URL}/{clean_id}"
    params = {"format": "json"}

    response = await resilient_get(url, params=params)
    if response.status_code == 404:
        return {"error": f"Trial {clean_id} not found"}
    if response.status_code != 200:
        return {"error": f"ClinicalTrials.gov API returned {response.status_code}"}

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse trial details JSON: %s", exc)
        return {"error": "Invalid JSON response from API"}

    return _parse_study(data)


@mcp.tool()
async def search_by_condition(condition: str, max_results: int = 10) -> dict:
    """Search clinical trials by medical condition or disease name.

    Uses the ClinicalTrials.gov condition-specific search field for
    more precise results than free-text search.
    """
    safe_condition = sanitize_query(condition)
    if not safe_condition:
        return {"error": "Empty condition after sanitization", "results": []}

    params = {
        "query.cond": safe_condition,
        "pageSize": min(max_results, 100),
        "format": "json",
    }

    response = await resilient_get(CLINICALTRIALS_STUDIES_URL, params=params)
    if response.status_code != 200:
        return {
            "error": f"ClinicalTrials.gov API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse condition search JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = _parse_study_list(data)
    total_count = data.get("totalCount", len(results))

    return {
        "condition": safe_condition,
        "total_count": total_count,
        "returned_count": len(results),
        "results": results,
    }


@mcp.tool()
async def search_by_intervention(intervention: str, max_results: int = 10) -> dict:
    """Search clinical trials by intervention (drug, device, procedure, etc.).

    Uses the ClinicalTrials.gov intervention-specific search field.
    """
    safe_intervention = sanitize_query(intervention)
    if not safe_intervention:
        return {"error": "Empty intervention after sanitization", "results": []}

    params = {
        "query.intr": safe_intervention,
        "pageSize": min(max_results, 100),
        "format": "json",
    }

    response = await resilient_get(CLINICALTRIALS_STUDIES_URL, params=params)
    if response.status_code != 200:
        return {
            "error": f"ClinicalTrials.gov API returned {response.status_code}",
            "results": [],
        }

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse intervention search JSON: %s", exc)
        return {"error": "Invalid JSON response from API", "results": []}

    results = _parse_study_list(data)
    total_count = data.get("totalCount", len(results))

    return {
        "intervention": safe_intervention,
        "total_count": total_count,
        "returned_count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9002)
