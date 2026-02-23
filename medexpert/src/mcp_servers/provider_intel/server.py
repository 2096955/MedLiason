"""Provider Intelligence MCP Server (CMS Open Payments / Provider Data).

Provides healthcare provider search, quality metrics, and Open Payments
financial transparency data from CMS. Used by the ProviderIntelSpecialist
agent for physician lookup and conflict-of-interest analysis.

CRITICAL SECURITY: All string parameters are sanitized via escape_sql_string()
and numeric parameters via validate_numeric() to prevent injection attacks.

Port: 9010
"""

import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get
from mcp_servers._security import escape_sql_string, validate_numeric, sanitize_query
from lifesci_common.constants import CMS_OPEN_PAYMENTS_URL, CMS_PROVIDER_URL

log = logging.getLogger(__name__)

mcp = FastMCP("Provider Intelligence")

# CMS dataset identifiers
OPEN_PAYMENTS_GENERAL_DATASET = "a02c-2sgh"
PROVIDER_COMPARE_DATASET = "mj5m-pzi6"


@mcp.tool()
async def search_providers(
    name: str | None = None,
    state: str | None = None,
    specialty: str | None = None,
    max_results: int = 10,
) -> dict:
    """Search healthcare providers in the CMS provider directory.

    Queries CMS Provider Compare data for physician information
    including name, specialty, location, and practice details.

    SECURITY: All string inputs are escaped to prevent injection.

    Args:
        name: Provider name (partial match supported).
        state: Two-letter US state code (e.g., "CA", "NY").
        specialty: Medical specialty (e.g., "Cardiology", "Oncology").
        max_results: Maximum number of results (default 10, max 50).

    Returns:
        Dictionary with provider records.
    """
    if not name and not state and not specialty:
        return {"error": "At least one search parameter (name, state, or specialty) is required."}

    conditions = []
    if name:
        safe_name = escape_sql_string(name)
        if not safe_name:
            return {"error": "Invalid name parameter after sanitization."}
        conditions.append(
            f"upper(provider_last_name) like upper('%{safe_name}%')"
        )
    if state:
        safe_state = escape_sql_string(state)
        if not safe_state or len(safe_state) > 2:
            return {"error": "Invalid state parameter. Must be a 2-letter code."}
        conditions.append(f"provider_state = '{safe_state.upper()}'")
    if specialty:
        safe_specialty = escape_sql_string(specialty)
        if not safe_specialty:
            return {"error": "Invalid specialty parameter after sanitization."}
        conditions.append(
            f"upper(primary_specialty) like upper('%{safe_specialty}%')"
        )

    where_clause = " AND ".join(conditions)
    url = f"{CMS_PROVIDER_URL}/{PROVIDER_COMPARE_DATASET}"
    params = {
        "$where": where_clause,
        "$limit": str(min(max_results, 50)),
    }

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            records = resp.json()
            parsed = []
            for rec in records:
                parsed.append({
                    "npi": rec.get("npi", ""),
                    "name": f"{rec.get('provider_first_name', '')} {rec.get('provider_last_name', '')}".strip(),
                    "specialty": rec.get("primary_specialty", ""),
                    "state": rec.get("provider_state", ""),
                    "city": rec.get("provider_city", ""),
                    "zip": rec.get("provider_zip_code", ""),
                    "organization": rec.get("organization_legal_name", ""),
                    "gender": rec.get("provider_gender", ""),
                })
            return {
                "query": {"name": name, "state": state, "specialty": specialty},
                "results": parsed,
                "total_results": len(parsed),
            }
        else:
            return {
                "error": f"CMS Provider API returned status {resp.status_code}",
                "results": [],
            }

    except Exception as exc:
        log.error("Provider search failed: %s", exc)
        return {"error": str(exc), "results": []}


@mcp.tool()
async def get_provider_quality(provider_id: str) -> dict:
    """Get quality metrics for a specific healthcare provider.

    Retrieves quality scores, patient experience ratings, and
    performance metrics from CMS Provider Compare.

    Args:
        provider_id: National Provider Identifier (NPI) — 10-digit number.

    Returns:
        Dictionary with quality metrics.
    """
    safe_id = sanitize_query(provider_id, max_len=10)
    if not safe_id or not safe_id.strip().isdigit() or len(safe_id.strip()) != 10:
        return {"error": "Invalid provider_id. Must be a 10-digit NPI number."}

    url = f"{CMS_PROVIDER_URL}/{PROVIDER_COMPARE_DATASET}"
    params = {
        "$where": f"npi = '{safe_id.strip()}'",
        "$limit": "1",
    }

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            records = resp.json()
            if not records:
                return {
                    "provider_id": provider_id,
                    "error": "Provider not found.",
                }

            rec = records[0]
            return {
                "provider_id": provider_id,
                "npi": rec.get("npi", ""),
                "name": f"{rec.get('provider_first_name', '')} {rec.get('provider_last_name', '')}".strip(),
                "specialty": rec.get("primary_specialty", ""),
                "quality_score": rec.get("quality_score", ""),
                "patient_experience": rec.get("patient_experience_score", ""),
                "hospital_affiliation": rec.get("hospital_affiliation", ""),
                "source": "CMS Provider Compare",
            }
        else:
            return {
                "error": f"CMS API returned status {resp.status_code}",
            }

    except Exception as exc:
        log.error("Provider quality lookup failed: %s", exc)
        return {"error": str(exc)}


@mcp.tool()
async def search_open_payments(
    physician_name: str | None = None,
    company: str | None = None,
    min_amount: float | None = None,
    max_results: int = 10,
) -> dict:
    """Search CMS Open Payments for industry payments to physicians.

    Queries the Open Payments database for financial transactions
    between pharmaceutical/device companies and healthcare providers.
    Useful for conflict-of-interest analysis.

    SECURITY: All string inputs escaped via escape_sql_string().
    Numeric inputs validated via validate_numeric().

    Args:
        physician_name: Physician last name (partial match).
        company: Company/manufacturer name (partial match).
        min_amount: Minimum payment amount in USD.
        max_results: Maximum number of results (default 10, max 50).

    Returns:
        Dictionary with payment records.
    """
    if not physician_name and not company:
        return {"error": "At least one of physician_name or company is required."}

    conditions = []
    if physician_name:
        safe_name = escape_sql_string(physician_name)
        if not safe_name:
            return {"error": "Invalid physician_name after sanitization."}
        conditions.append(
            f"upper(covered_recipient_last_name) like upper('%{safe_name}%')"
        )
    if company:
        safe_company = escape_sql_string(company)
        if not safe_company:
            return {"error": "Invalid company name after sanitization."}
        conditions.append(
            f"upper(applicable_manufacturer_or_applicable_gpo_making_payment_name) "
            f"like upper('%{safe_company}%')"
        )
    if min_amount is not None:
        validated_amount = validate_numeric(str(min_amount))
        if validated_amount is None:
            return {"error": "Invalid min_amount. Must be a valid number."}
        conditions.append(
            f"total_amount_of_payment_usdollars >= {validated_amount}"
        )

    where_clause = " AND ".join(conditions)
    url = f"{CMS_OPEN_PAYMENTS_URL}/{OPEN_PAYMENTS_GENERAL_DATASET}"
    params = {
        "$where": where_clause,
        "$limit": str(min(max_results, 50)),
        "$order": "total_amount_of_payment_usdollars DESC",
    }

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            records = resp.json()
            parsed = []
            for rec in records:
                parsed.append({
                    "physician": (
                        f"{rec.get('covered_recipient_first_name', '')} "
                        f"{rec.get('covered_recipient_last_name', '')}"
                    ).strip(),
                    "physician_specialty": rec.get(
                        "covered_recipient_specialty_1", ""
                    ),
                    "company": rec.get(
                        "applicable_manufacturer_or_applicable_gpo_making_payment_name",
                        "",
                    ),
                    "amount_usd": rec.get(
                        "total_amount_of_payment_usdollars", ""
                    ),
                    "payment_nature": rec.get(
                        "nature_of_payment_or_transfer_of_value", ""
                    ),
                    "date": rec.get("date_of_payment", ""),
                    "form_of_payment": rec.get(
                        "form_of_payment_or_transfer_of_value", ""
                    ),
                    "product": rec.get(
                        "name_of_drug_or_biological_or_device_or_medical_supply_1",
                        "",
                    ),
                })
            return {
                "query": {
                    "physician_name": physician_name,
                    "company": company,
                    "min_amount": min_amount,
                },
                "results": parsed,
                "total_results": len(parsed),
                "source": "CMS Open Payments",
            }
        else:
            return {
                "error": f"CMS Open Payments API returned status {resp.status_code}",
                "results": [],
            }

    except Exception as exc:
        log.error("Open Payments search failed: %s", exc)
        return {"error": str(exc), "results": []}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9010)
