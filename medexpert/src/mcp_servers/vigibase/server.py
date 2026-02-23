"""VigiBase Adverse Reactions MCP Server — Honest Degradation.

VigiBase (WHO Global ICSR Database) requires authenticated browser
sessions via the WHO UMC portal. This server always returns structured
degradation responses pointing users to alternative data sources.

Port: 9013
"""

import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._security import sanitize_query

log = logging.getLogger(__name__)

mcp = FastMCP("VigiBase Adverse Reactions")


def _browser_automation_response(
    drug_name: str,
    tool_name: str,
) -> dict:
    """Return the standard VigiBase degradation response."""
    safe_drug = sanitize_query(drug_name, max_len=100)
    return {
        "status": "browser_automation_required",
        "reason": (
            "VigiBase requires authenticated browser session via WHO login. "
            "Access the VigiBase portal at https://www.vigiaccess.org/ or "
            "request institutional access via WHO UMC."
        ),
        "tool": tool_name,
        "drug_name": safe_drug,
        "fallback_search_query": (
            f"{safe_drug} adverse reactions vigibase site:who.int"
        ),
        "alternative_sources": [
            "OpenFDA FAERS (FDA Adverse Event Reporting System)",
            "PubMed case reports",
            "EMA EudraVigilance",
            "MedWatch Safety Reports",
        ],
    }


@mcp.tool()
async def search_adverse_reactions(
    drug_name: str,
    max_results: int = 10,
) -> dict:
    """Search for adverse drug reactions in VigiBase.

    NOTE: VigiBase requires an authenticated browser session via
    WHO login. This tool returns a structured response directing
    users to alternative data sources (OpenFDA FAERS, PubMed).

    Args:
        drug_name: Drug name to search (generic or brand name).
        max_results: Maximum number of results (unused — always returns degradation response).

    Returns:
        Dictionary with browser_automation_required status and alternatives.
    """
    return _browser_automation_response(drug_name, "search_adverse_reactions")


@mcp.tool()
async def get_signal_data(drug_name: str) -> dict:
    """Get safety signal data for a drug from VigiBase.

    NOTE: VigiBase requires an authenticated browser session via
    WHO login. This tool returns a structured response directing
    users to alternative data sources.

    Args:
        drug_name: Drug name to search for safety signals.

    Returns:
        Dictionary with browser_automation_required status and alternatives.
    """
    return _browser_automation_response(drug_name, "get_signal_data")


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9013)
