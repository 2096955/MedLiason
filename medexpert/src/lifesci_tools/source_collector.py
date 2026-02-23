"""Source Collector — publishes MCP-sourced references as RAG citations.

Specialist agents return text with identifiers (PMIDs, NCT IDs, DOIs, etc.)
but no rag_metadata reaches the SSE stream because they run via fire-and-forget
A2A. This tool lets the orchestrator push those references into the citation
pipeline so the frontend Sources panel displays them.

URL construction rules:
  PMID        → https://pubmed.ncbi.nlm.nih.gov/{pmid}/
  NCT ID      → https://clinicaltrials.gov/study/{nct_id}
  DOI         → https://doi.org/{doi}
  explicit URL → used as-is
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool
from solace_agent_mesh.common.rag_dto import create_rag_source, create_rag_search_result

logger = logging.getLogger(__name__)

# ── URL construction helpers ──────────────────────────────────────────

_ID_URL_TEMPLATES = {
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/{id}/",
    "nct_id": "https://clinicaltrials.gov/study/{id}",
    "doi": "https://doi.org/{id}",
}


def _build_url(source: Dict[str, Any]) -> str:
    """Construct a clickable URL from an identifier or explicit url field."""
    if source.get("url"):
        return source["url"]
    for id_type, template in _ID_URL_TEMPLATES.items():
        value = source.get(id_type)
        if value:
            return template.format(id=value)
    return ""


def _source_label(source: Dict[str, Any]) -> str:
    """Human-readable label for the favicon domain extraction."""
    url = _build_url(source)
    if not url:
        return source.get("title", "Unknown source")
    try:
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return url


class SourceCollectorTool(DynamicTool):
    """Publishes structured references as RAG citations visible in the UI."""

    @property
    def tool_name(self) -> str:
        return "publish_sources"

    @property
    def tool_description(self) -> str:
        return (
            "Publish references from specialist agent responses so they appear "
            "in the Sources sidebar. Call this AFTER collecting specialist results. "
            "Provide a list of sources — each with a title, snippet, and at least "
            "one identifier (pmid, nct_id, doi) or a url. Returns citation IDs "
            "you MUST use in your answer with [[cite:...]] markers."
        )

    @property
    def parameters_schema(self) -> adk_types.Schema:
        return adk_types.Schema(
            type=adk_types.Type.OBJECT,
            properties={
                "query": adk_types.Schema(
                    type=adk_types.Type.STRING,
                    description="The original user question (used as the search title).",
                ),
                "sources": adk_types.Schema(
                    type=adk_types.Type.ARRAY,
                    description="List of sources to publish.",
                    items=adk_types.Schema(
                        type=adk_types.Type.OBJECT,
                        properties={
                            "title": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Source title (article name, trial title, etc.).",
                            ),
                            "snippet": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Brief description or key finding.",
                            ),
                            "source_type": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description=(
                                    "Type of source: pubmed, clinical_trial, fda, "
                                    "regulatory, cdc, genomic, environmental, provider, web"
                                ),
                            ),
                            "pmid": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="PubMed ID (e.g. '38901234').",
                            ),
                            "nct_id": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="ClinicalTrials.gov NCT ID (e.g. 'NCT06123456').",
                            ),
                            "doi": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Digital Object Identifier.",
                            ),
                            "url": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Explicit URL (used if no identifier provided).",
                            ),
                            "agent_name": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Name of the agent that provided this source.",
                            ),
                            "mcp_server": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="Name of the MCP server used to retrieve this source.",
                            ),
                            "api_endpoint": adk_types.Schema(
                                type=adk_types.Type.STRING,
                                description="API endpoint that returned this data.",
                            ),
                        },
                        required=["title", "snippet"],
                    ),
                ),
            },
            required=["query", "sources"],
        )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> Dict[str, Any]:
        log_id = "[publish_sources]"
        query = args.get("query", "")
        sources = args.get("sources") or []

        if not sources:
            logger.warning("%s Called with empty sources list", log_id)
            return {"status": "no_sources", "valid_citation_ids": []}

        # Build RAG sources and citation IDs
        rag_sources = []
        valid_citation_ids = []
        now = datetime.now(timezone.utc).isoformat()

        for i, src in enumerate(sources):
            citation_id = f"s0r{i}"
            valid_citation_ids.append(citation_id)
            url = _build_url(src)
            domain = _source_label(src)

            rag_source = create_rag_source(
                citation_id=citation_id,
                file_id=f"mcp_source_{i}",
                filename=domain,
                title=src.get("title", ""),
                source_url=url,
                url=url,
                content_preview=src.get("snippet", "")[:200],
                relevance_score=1.0,
                source_type=src.get("source_type", "reference"),
                retrieved_at=now,
                metadata={
                    "title": src.get("title", ""),
                    "link": url,
                    "type": src.get("source_type", "reference"),
                    "favicon": (
                        f"https://www.google.com/s2/favicons?domain={url}&sz=32"
                        if url
                        else ""
                    ),
                    "agent_name": src.get("agent_name", ""),
                    "mcp_server": src.get("mcp_server", ""),
                    "api_endpoint": src.get("api_endpoint", ""),
                },
            )
            rag_sources.append(rag_source)

        rag_metadata = create_rag_search_result(
            query=query,
            search_type="web_search",
            timestamp=now,
            sources=rag_sources,
        )

        logger.info(
            "%s Published %d sources for query: %s",
            log_id,
            len(rag_sources),
            query[:80],
        )

        # Build formatted results for the LLM
        lines = [f"=== PUBLISHED SOURCES ({len(rag_sources)} references) ==="]
        lines.append(f"Query: {query}")
        lines.append(f"Valid citation IDs: {', '.join(valid_citation_ids)}")
        lines.append("")
        for i, src in enumerate(sources):
            cid = f"s0r{i}"
            lines.append(f"--- SOURCE {i + 1} ---")
            lines.append(f"CITATION ID: [[cite:{cid}]]")
            lines.append(f"TITLE: {src.get('title', 'N/A')}")
            lines.append(f"URL: {_build_url(src)}")
            if src.get("agent_name"):
                lines.append(f"AGENT: {src.get('agent_name')}")
            if src.get("mcp_server"):
                lines.append(f"MCP SERVER: {src.get('mcp_server')}")
            lines.append(f"CONTENT: {src.get('snippet', 'N/A')}")
            lines.append(f"USE [[cite:{cid}]] to cite facts from THIS source only")
            lines.append("")
        lines.append("=== END PUBLISHED SOURCES ===")

        return {
            "status": "published",
            "formatted_results": "\n".join(lines),
            "rag_metadata": rag_metadata,
            "valid_citation_ids": valid_citation_ids,
            "num_sources": len(rag_sources),
        }
