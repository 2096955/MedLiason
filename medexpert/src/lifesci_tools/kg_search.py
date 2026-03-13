"""KG Search — queries the Knowledge Graph and produces RAG citations.

Wraps the Knowledge Graph MCP server functions (query_knowledge_graph,
get_session_graph) and converts Memgraph node results into RAGSource
objects for the frontend citation pipeline.

Citation ID namespace: kg0rN (distinct from s0rN in source_collector).
searchType: "kb_search" (already defined in frontend fe.ts types).
"""

import logging
from datetime import datetime, timezone
from typing import Any

from google.adk.tools import ToolContext
from google.genai import types as adk_types
from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool
from solace_agent_mesh.common.rag_dto import create_rag_search_result, create_rag_source

logger = logging.getLogger(__name__)

_ID_URL_TEMPLATES = {
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/{id}/",
    "nct_id": "https://clinicaltrials.gov/study/{id}",
    "doi": "https://doi.org/{id}",
}

_EXCLUDED_LABELS = {"Session", "Specialist"}


async def _call_kg_tool(tool_name: str, arguments: dict) -> dict:
    """Call a KG MCP server tool via Python import (same-process pattern)."""
    try:
        from mcp_servers.knowledge_graph.server import (
            get_session_graph,
            query_knowledge_graph,
        )

        tool_map = {
            "query_knowledge_graph": query_knowledge_graph,
            "get_session_graph": get_session_graph,
        }

        fn = tool_map.get(tool_name)
        if fn is None:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        if hasattr(fn, "fn"):
            fn = fn.fn
        return await fn(**arguments)

    except ImportError:
        return {
            "success": False,
            "error": "Knowledge graph MCP server not available",
            "error_category": "service_unavailable",
        }
    except Exception as exc:
        logger.error("KG tool call failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "error_category": "api_error",
        }


def _build_url_from_props(props: dict) -> str:
    """Construct a URL from node properties (pmid, nct_id, doi)."""
    for id_type, template in _ID_URL_TEMPLATES.items():
        value = props.get(id_type)
        if value:
            return template.format(id=value)
    return ""


def _node_to_rag_source(node: dict, index: int) -> dict[str, Any]:
    """Convert a KG node dict into a RAG source via create_rag_source."""
    citation_id = f"kg0r{index}"
    labels = node.get("labels", [])
    props = node.get("properties", {})
    name = node.get("name", "")
    description = node.get("description", "")
    is_study = "Study" in labels

    url = _build_url_from_props(props) if is_study else ""
    content_preview = f"{name}: {description[:200]}" if description else name

    evidence_grade = ""
    if is_study:
        evidence_grade = props.get("evidence_grade", "Moderate")

    return create_rag_source(
        citation_id=citation_id,
        file_id=f"kg_node_{index}",
        filename=name,
        title=props.get("title", name),
        source_url=url or None,
        url=url or None,
        content_preview=content_preview,
        relevance_score=1.0,
        source_type="kb_search",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        metadata={
            "title": props.get("title", name),
            "link": url,
            "type": "kb_search",
            "labels": labels,
            "source": "knowledge_graph",
            "node_id": node.get("id", ""),
            "evidence_grade": evidence_grade,
            "favicon": (
                f"https://www.google.com/s2/favicons?domain={url}&sz=32" if url else ""
            ),
        },
    )


class KgSearchTool(DynamicTool):
    """Queries the Knowledge Graph and returns results as RAG citations."""

    tool_name = "kg_search"
    tool_description = (
        "Search the MedExpert Knowledge Graph (Memgraph) for entities and "
        "relationships from prior research sessions. Returns RAG-formatted "
        "citations with [[cite:kg0rN]] markers you can use in your answer. "
        "Call at STEP 1 (PLAN) to check what is already known before "
        "delegating to specialists."
    )
    parameters_schema = adk_types.Schema(
        type=adk_types.Type.OBJECT,
        properties={
            "query": adk_types.Schema(
                type=adk_types.Type.STRING,
                description="Search query (entity name, disease, drug, gene, etc.).",
            ),
            "entity_types": adk_types.Schema(
                type=adk_types.Type.ARRAY,
                description="Optional filter by node labels: Disease, Drug, Gene, Study.",
                items=adk_types.Schema(type=adk_types.Type.STRING),
            ),
            "session_id": adk_types.Schema(
                type=adk_types.Type.STRING,
                description="Retrieve a specific prior session's graph.",
            ),
            "limit": adk_types.Schema(
                type=adk_types.Type.INTEGER,
                description="Max results (default 10, max 50).",
            ),
        },
        required=["query"],
    )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: str | None = None,
    ) -> dict[str, Any]:
        log_id = "[kg_search]"
        query = args.get("query", "").strip()

        if not query:
            return {"status": "invalid_query", "rag_metadata": None, "num_sources": 0}

        session_id = args.get("session_id")
        try:
            limit = min(max(int(args.get("limit", 10)), 1), 50)
        except (TypeError, ValueError):
            limit = 10

        try:
            if session_id:
                raw = await _call_kg_tool(
                    "get_session_graph", {"session_id": session_id}
                )
                nodes = raw.get("nodes", []) if raw.get("success") else []
            else:
                entity_types = args.get("entity_types")
                raw = await _call_kg_tool(
                    "query_knowledge_graph",
                    {"query": query, "entity_types": entity_types, "limit": limit},
                )
                nodes = raw.get("results", []) if raw.get("success") else []

            if not raw.get("success"):
                logger.warning(
                    "%s KG query failed: %s", log_id, raw.get("error", "unknown")
                )
                return {
                    "status": "kg_unavailable",
                    "rag_metadata": None,
                    "num_sources": 0,
                }

            content_nodes = [
                n
                for n in nodes
                if not any(lbl in _EXCLUDED_LABELS for lbl in n.get("labels", []))
            ]

            if not content_nodes:
                return {
                    "status": "no_kg_results",
                    "rag_metadata": None,
                    "num_sources": 0,
                }

            rag_sources: list[dict[str, Any]] = []
            valid_ids: list[str] = []
            for i, node in enumerate(content_nodes[:limit]):
                rag_sources.append(_node_to_rag_source(node, i))
                valid_ids.append(f"kg0r{i}")

            rag_metadata = create_rag_search_result(
                query=query,
                search_type="kb_search",
                timestamp=datetime.now(timezone.utc).isoformat(),
                sources=rag_sources,
            )

            lines = [f"=== KNOWLEDGE GRAPH RESULTS ({len(rag_sources)} entities) ==="]
            lines.append(f"Query: {query}")
            lines.append(f"Citation IDs: {', '.join(valid_ids)}")
            lines.append("")
            for i, node in enumerate(content_nodes[:limit]):
                cid = f"kg0r{i}"
                lines.append(f"--- KG ENTITY {i + 1} ---")
                lines.append(f"CITATION ID: [[cite:{cid}]]")
                lines.append(f"TYPE: {', '.join(node.get('labels', []))}")
                lines.append(f"NAME: {node.get('name', 'N/A')}")
                desc = node.get("description", "")
                if desc:
                    lines.append(f"DESCRIPTION: {desc[:300]}")
                url = _build_url_from_props(node.get("properties", {}))
                if url:
                    lines.append(f"URL: {url}")
                lines.append("")
            lines.append("=== END KNOWLEDGE GRAPH RESULTS ===")

            logger.info(
                "%s Found %d KG entities for query: %s",
                log_id,
                len(rag_sources),
                query[:80],
            )

            return {
                "status": "found",
                "formatted_results": "\n".join(lines),
                "rag_metadata": rag_metadata,
                "valid_citation_ids": valid_ids,
                "num_sources": len(rag_sources),
            }

        except Exception as exc:
            logger.error("%s Unexpected error: %s", log_id, exc)
            return {"status": "kg_unavailable", "rag_metadata": None, "num_sources": 0}
