"""Genomic Variant MCP Server (ClinVar / dbSNP).

Provides genomic variant search and annotation via NCBI ClinVar
(E-utilities: esearch + esummary) and dbSNP APIs. Used by the
GenomicsSpecialist agent to look up pathogenicity, gene-disease
associations, and variant details.

Port: 9007
"""

import logging
import os
import sys
import xml.etree.ElementTree as ET

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import resilient_get
from mcp_servers._security import sanitize_query
from lifesci_common.constants import CLINVAR_API_URL, DBSNP_API_URL

log = logging.getLogger(__name__)

mcp = FastMCP("Genomic Variants")


def _parse_clinvar_esummary(xml_text: str) -> list[dict]:
    """Parse ClinVar eSummary XML into structured variant records."""
    results = []
    try:
        root = ET.fromstring(xml_text)
        for docsum in root.findall(".//DocumentSummary"):
            variant_id = docsum.get("uid", "")
            title = docsum.findtext("title", "")
            clinical_significance = docsum.findtext(
                "clinical_significance/description", ""
            )
            gene_name = docsum.findtext("genes/gene/symbol", "")
            condition = docsum.findtext(
                "trait_set/trait/trait_name", ""
            )
            variation_type = docsum.findtext("variation_type", "")
            accession = docsum.findtext("accession", "")

            results.append({
                "variant_id": variant_id,
                "accession": accession,
                "title": title,
                "gene": gene_name,
                "clinical_significance": clinical_significance,
                "condition": condition,
                "variation_type": variation_type,
            })
    except ET.ParseError as exc:
        log.error("Failed to parse ClinVar XML: %s", exc)

    return results


def _parse_esearch_ids(xml_text: str) -> list[str]:
    """Extract ID list from an NCBI eSearch XML response."""
    ids = []
    try:
        root = ET.fromstring(xml_text)
        for id_elem in root.findall(".//Id"):
            if id_elem.text:
                ids.append(id_elem.text)
    except ET.ParseError as exc:
        log.error("Failed to parse eSearch XML: %s", exc)
    return ids


@mcp.tool()
async def search_variants(
    gene_or_variant: str,
    max_results: int = 10,
) -> dict:
    """Search ClinVar for genetic variants by gene name or variant identifier.

    Performs a two-step search: eSearch to get variant IDs, then eSummary
    to retrieve detailed annotations including clinical significance,
    associated conditions, and gene information.

    Args:
        gene_or_variant: Gene symbol (e.g., "BRCA1") or variant ID (e.g., "rs80357906").
        max_results: Maximum number of variants to return (default 10).

    Returns:
        Dictionary with variant records and metadata.
    """
    safe_query = sanitize_query(gene_or_variant)
    if not safe_query:
        return {"error": "Invalid search query", "results": []}

    # Step 1: eSearch to get ClinVar IDs
    esearch_url = f"{CLINVAR_API_URL}/esearch.fcgi"
    esearch_params = {
        "db": "clinvar",
        "term": safe_query,
        "retmax": str(min(max_results, 50)),
        "retmode": "xml",
    }

    try:
        search_resp = await resilient_get(esearch_url, params=esearch_params)
        if search_resp.status_code != 200:
            return {
                "error": f"ClinVar eSearch returned status {search_resp.status_code}",
                "results": [],
            }

        ids = _parse_esearch_ids(search_resp.text)
        if not ids:
            return {
                "query": gene_or_variant,
                "results": [],
                "total_results": 0,
                "message": "No variants found matching the query.",
            }

        # Step 2: eSummary to get variant details
        esummary_url = f"{CLINVAR_API_URL}/esummary.fcgi"
        esummary_params = {
            "db": "clinvar",
            "id": ",".join(ids),
            "retmode": "xml",
        }

        summary_resp = await resilient_get(esummary_url, params=esummary_params)
        if summary_resp.status_code != 200:
            return {
                "error": f"ClinVar eSummary returned status {summary_resp.status_code}",
                "results": [],
            }

        variants = _parse_clinvar_esummary(summary_resp.text)
        return {
            "query": gene_or_variant,
            "results": variants,
            "total_results": len(variants),
        }

    except Exception as exc:
        log.error("ClinVar search failed: %s", exc)
        return {"error": str(exc), "results": []}


@mcp.tool()
async def get_variant_details(variant_id: str) -> dict:
    """Get detailed information for a specific ClinVar variant.

    Retrieves the full eSummary record for a single ClinVar variant ID.

    Args:
        variant_id: ClinVar variant ID (numeric, e.g., "37079").

    Returns:
        Dictionary with detailed variant information.
    """
    safe_id = sanitize_query(variant_id, max_len=20)
    if not safe_id or not safe_id.strip().isdigit():
        return {"error": "Invalid variant ID. Must be a numeric ClinVar ID."}

    esummary_url = f"{CLINVAR_API_URL}/esummary.fcgi"
    params = {
        "db": "clinvar",
        "id": safe_id.strip(),
        "retmode": "xml",
    }

    try:
        resp = await resilient_get(esummary_url, params=params)
        if resp.status_code != 200:
            return {"error": f"ClinVar API returned status {resp.status_code}"}

        variants = _parse_clinvar_esummary(resp.text)
        if not variants:
            return {
                "variant_id": variant_id,
                "error": "Variant not found in ClinVar.",
            }

        return {
            "variant_id": variant_id,
            "details": variants[0],
        }

    except Exception as exc:
        log.error("ClinVar variant lookup failed: %s", exc)
        return {"error": str(exc)}


@mcp.tool()
async def search_gene_info(gene_name: str) -> dict:
    """Search for gene information and associated variants via dbSNP.

    Queries the NCBI dbSNP Variation API for SNPs associated with
    a given gene. Returns variant rsIDs, positions, and allele info.

    Args:
        gene_name: Gene symbol (e.g., "BRCA1", "TP53", "EGFR").

    Returns:
        Dictionary with gene-associated variant data.
    """
    safe_gene = sanitize_query(gene_name, max_len=50)
    if not safe_gene:
        return {"error": "Invalid gene name", "results": []}

    # dbSNP refsnp search by gene name
    url = f"{DBSNP_API_URL}/refsnp"
    params = {
        "gene_name": safe_gene,
        "page_size": "10",
    }

    try:
        resp = await resilient_get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            # dbSNP returns different formats; handle both list and dict
            if isinstance(data, list):
                results = []
                for snp in data:
                    results.append({
                        "rsid": snp.get("refsnp_id", ""),
                        "gene": safe_gene,
                        "variant_type": snp.get("primary_snapshot_data", {}).get(
                            "variant_type", ""
                        ),
                        "chromosome": snp.get("primary_snapshot_data", {}).get(
                            "placement_with_allele", [{}]
                        )[0].get("seq_id", "") if snp.get("primary_snapshot_data", {}).get("placement_with_allele") else "",
                    })
                return {
                    "gene": safe_gene,
                    "results": results,
                    "total_results": len(results),
                }
            elif isinstance(data, dict):
                return {
                    "gene": safe_gene,
                    "results": [data],
                    "total_results": 1,
                }
            else:
                return {
                    "gene": safe_gene,
                    "results": [],
                    "total_results": 0,
                }
        elif resp.status_code == 404:
            return {
                "gene": safe_gene,
                "results": [],
                "total_results": 0,
                "message": f"No variants found for gene {safe_gene}.",
            }
        else:
            return {
                "error": f"dbSNP API returned status {resp.status_code}",
                "results": [],
            }

    except Exception as exc:
        log.error("dbSNP gene search failed: %s", exc)
        return {"error": str(exc), "results": []}


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9007)
