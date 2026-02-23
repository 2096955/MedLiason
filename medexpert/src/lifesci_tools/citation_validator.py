"""Citation Validator — PMID/DOI resolution via PubMed and CrossRef.

Validates that cited identifiers (PMIDs and DOIs) actually exist and
retrieves their metadata. Uses real free APIs: PubMed E-utilities
esummary and CrossRef works API.
"""

import json
import logging
import xml.etree.ElementTree as ET
from typing import Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool

import os
import sys

# Ensure sibling packages are importable when running standalone
_parent = os.path.join(os.path.dirname(__file__), "..")
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from mcp_servers._http import resilient_get  # noqa: E402
from lifesci_common.constants import CROSSREF_WORKS_URL, PUBMED_ESUMMARY_URL  # noqa: E402

log = logging.getLogger(__name__)


async def _validate_pmid(pmid: str) -> dict:
    """Validate a PubMed ID via esummary API."""
    try:
        response = await resilient_get(
            PUBMED_ESUMMARY_URL,
            params={"db": "pubmed", "id": pmid, "retmode": "json"},
        )
        if response.status_code != 200:
            return {"identifier": pmid, "valid": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        result = data.get("result", {})
        article = result.get(pmid, {})

        if "error" in article:
            return {"identifier": pmid, "valid": False, "error": article["error"]}

        title = article.get("title", "")
        if not title:
            return {"identifier": pmid, "valid": False, "error": "No title found"}

        authors = []
        for author in article.get("authors", []):
            authors.append(author.get("name", ""))

        return {
            "identifier": pmid,
            "valid": True,
            "metadata": {
                "title": title,
                "authors": authors[:5],
                "journal": article.get("fulljournalname", article.get("source", "")),
                "year": article.get("pubdate", "")[:4],
                "doi": article.get("elocationid", ""),
            },
        }
    except Exception as exc:
        return {"identifier": pmid, "valid": False, "error": str(exc)}


async def _validate_doi(doi: str) -> dict:
    """Validate a DOI via CrossRef API."""
    try:
        url = f"{CROSSREF_WORKS_URL}/{doi}"
        response = await resilient_get(
            url,
            headers={"Accept": "application/json"},
        )
        if response.status_code == 404:
            return {"identifier": doi, "valid": False, "error": "DOI not found"}
        if response.status_code != 200:
            return {"identifier": doi, "valid": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        message = data.get("message", {})

        title_list = message.get("title", [])
        title = title_list[0] if title_list else ""

        authors = []
        for author in message.get("author", [])[:5]:
            name = f"{author.get('given', '')} {author.get('family', '')}".strip()
            if name:
                authors.append(name)

        container = message.get("container-title", [])
        journal = container[0] if container else ""

        published = message.get("published-print", message.get("published-online", {}))
        date_parts = published.get("date-parts", [[]])
        year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""

        return {
            "identifier": doi,
            "valid": True,
            "metadata": {
                "title": title,
                "authors": authors,
                "journal": journal,
                "year": year,
            },
        }
    except Exception as exc:
        return {"identifier": doi, "valid": False, "error": str(exc)}


class CitationValidatorTool(DynamicTool):
    """Validates PMIDs and DOIs against PubMed and CrossRef APIs."""

    @property
    def tool_name(self) -> str:
        return "citation_validator"

    @property
    def tool_description(self) -> str:
        return (
            "Validates citation identifiers (PMIDs and DOIs) by checking them against "
            "PubMed E-utilities and CrossRef APIs. Returns metadata (title, authors, "
            "journal, year) for valid citations, or an error for invalid ones."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "citations_json": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description=(
                        "JSON string of citations array. Each item: "
                        '{type: "pmid"|"doi", identifier: str}'
                    ),
                ),
            },
            required=["citations_json"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> dict:
        try:
            citations = json.loads(args.get("citations_json", "[]"))
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in citations_json"}

        if not isinstance(citations, list):
            return {"error": "citations_json must be a JSON array"}

        results = []
        for citation in citations:
            ctype = citation.get("type", "").lower()
            identifier = citation.get("identifier", "").strip()

            if not identifier:
                results.append({"identifier": "", "valid": False, "error": "Empty identifier"})
                continue

            if ctype == "pmid":
                result = await _validate_pmid(identifier)
            elif ctype == "doi":
                result = await _validate_doi(identifier)
            else:
                result = {
                    "identifier": identifier,
                    "valid": False,
                    "error": f"Unknown type '{ctype}'. Use 'pmid' or 'doi'.",
                }

            results.append(result)

        valid_count = sum(1 for r in results if r.get("valid"))
        return {
            "results": results,
            "total": len(results),
            "valid_count": valid_count,
            "invalid_count": len(results) - valid_count,
        }
