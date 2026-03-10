"""Knowledge Graph REST API router for MedExpert.

Calls the Knowledge Graph MCP server tools directly via Python imports
(same process during `sam run`). This avoids network overhead and the
non-existent HTTP endpoint issue with FastMCP SSE transport.

Mounted at /api/v1/graph by the gateway's main.py (guarded import).
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/graph", tags=["Knowledge Graph"])

# NLQ rate limiter: 5 requests per session per minute
_NLQ_RATE_LIMIT = 5
_NLQ_WINDOW = 60  # seconds
_nlq_buckets: dict[str, list[float]] = {}


def _get_session_id(request: Request) -> str:
    """Extract session ID from request for rate limiting."""
    return request.headers.get("x-session-id", request.client.host if request.client else "unknown")


def _check_nlq_rate_limit(session_id: str) -> bool:
    """Return True if within rate limit, False if exceeded."""
    now = time.time()
    bucket = _nlq_buckets.get(session_id, [])
    # Prune expired entries
    bucket = [t for t in bucket if now - t < _NLQ_WINDOW]
    if not bucket:
        # Clean up empty buckets to prevent memory leak
        _nlq_buckets.pop(session_id, None)
    if len(bucket) >= _NLQ_RATE_LIMIT:
        _nlq_buckets[session_id] = bucket
        return False
    bucket.append(now)
    _nlq_buckets[session_id] = bucket
    return True


async def _call_tool(tool_name: str, arguments: dict) -> dict:
    """Call a Knowledge Graph MCP tool directly via Python import.

    Since the MCP server runs in the same process during `sam run`,
    we import and call the tool functions directly. This avoids the
    network overhead and FastMCP SSE transport compatibility issues.
    """
    try:
        from mcp_servers.knowledge_graph.server import (
            get_entity_relationships,
            get_graph_stats,
            get_session_graph,
            natural_language_query,
            query_knowledge_graph,
        )

        tool_map = {
            "query_knowledge_graph": query_knowledge_graph,
            "get_entity_relationships": get_entity_relationships,
            "get_session_graph": get_session_graph,
            "get_graph_stats": get_graph_stats,
            "natural_language_query": natural_language_query,
        }

        fn = tool_map.get(tool_name)
        if fn is None:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
                "error_category": "api_error",
                "is_retryable": False,
            }

        # FastMCP @mcp.tool() wraps functions in FunctionTool objects.
        # Unwrap to get the underlying async callable.
        if hasattr(fn, "fn"):
            fn = fn.fn
        return await fn(**arguments)

    except ImportError:
        return {
            "success": False,
            "error": "Knowledge graph MCP server not available",
            "error_category": "service_unavailable",
            "is_retryable": True,
        }
    except Exception as exc:
        log.error("Knowledge graph tool call failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "error_category": "api_error",
            "is_retryable": False,
        }


@router.get("/session/{session_id}")
async def get_session_graph_endpoint(session_id: str):
    """Get the knowledge graph for a specific research session."""
    result = await _call_tool("get_session_graph", {"session_id": session_id})
    status = 200 if result.get("success", False) else 503
    return JSONResponse(content=result, status_code=status)


@router.get("/entity/{entity_id}")
async def get_entity(entity_id: str):
    """Get entity details and relationships."""
    result = await _call_tool("get_entity_relationships", {"entity_id": entity_id})
    status = 200 if result.get("success", False) else 503
    return JSONResponse(content=result, status_code=status)


@router.get("/search")
async def search_graph(
    q: str = Query(..., min_length=1, max_length=500),
    labels: str | None = Query(None, description="Comma-separated entity types"),
):
    """Search the knowledge graph for entities."""
    entity_types = [t.strip() for t in labels.split(",")] if labels else None
    result = await _call_tool(
        "query_knowledge_graph",
        {"query": q, "entity_types": entity_types},
    )
    status = 200 if result.get("success", False) else 503
    return JSONResponse(content=result, status_code=status)


@router.post("/nlq")
async def natural_language_query_endpoint(request: Request):
    """Query the knowledge graph using natural language.

    Rate limited to 5 requests per session per minute.
    """
    session_id = _get_session_id(request)
    if not _check_nlq_rate_limit(session_id):
        return JSONResponse(
            content={
                "success": False,
                "error": "Rate limit exceeded. Max 5 NLQ queries per minute.",
                "error_category": "rate_limited",
                "is_retryable": True,
            },
            status_code=429,
        )

    body = await request.json()
    question = body.get("question", "")
    if not question:
        return JSONResponse(
            content={"success": False, "error": "question is required"},
            status_code=400,
        )

    result = await _call_tool("natural_language_query", {"question": question})
    status = 200 if result.get("success", False) else 503
    return JSONResponse(content=result, status_code=status)


@router.get("/stats")
async def get_graph_stats_endpoint():
    """Get summary statistics for the knowledge graph."""
    result = await _call_tool("get_graph_stats", {})
    status = 200 if result.get("success", False) else 503
    return JSONResponse(content=result, status_code=status)


async def _fetch_edges_between_nodes(
    node_ids: list[str],
    id_to_element_id: dict[int, str] | None = None,
) -> list[dict]:
    """Fetch all edges between a set of nodes via a single Cypher pass.

    Uses Memgraph's ``id()`` function (returns integer internal IDs) because
    Memgraph does NOT support Neo4j 5.x's ``elementId()`` Cypher function.
    The neo4j Python driver's ``node.element_id`` property works (it is a
    client-side convenience), but the Cypher function does not exist in
    Memgraph, causing silent query failures.

    Args:
        node_ids: Element-ID strings from node.element_id (used as frontend
            node ``id`` fields).  Converted to integers for the Cypher query
            via ``id_to_element_id`` or by parsing the strings directly.
        id_to_element_id: Optional mapping from Memgraph internal int IDs to
            element_id strings.  When provided (from the explore endpoint),
            this avoids lossy int↔string round-trips.  When absent, the
            element_id strings are parsed as integers (works for Memgraph
            where element_id == str(internal_id)).
    """
    if not node_ids and not id_to_element_id:
        return []
    try:
        from mcp_servers.knowledge_graph.server import _get_driver

        driver = _get_driver()
        if driver is None:
            return []

        # Build the int→element_id mapping if not provided.
        if id_to_element_id is None:
            id_to_element_id = {}
            for eid in node_ids:
                try:
                    id_to_element_id[int(eid)] = eid
                except (ValueError, TypeError):
                    pass  # Skip non-numeric element IDs

        int_ids = list(id_to_element_id.keys())
        if not int_ids:
            return []

        cypher = (
            "MATCH (a)-[r]->(b) "
            "WHERE id(a) IN $ids AND id(b) IN $ids "
            "RETURN id(a) AS src, id(b) AS tgt, type(r) AS label "
            "LIMIT 500"
        )

        # neo4j Python driver is synchronous — run in thread to avoid
        # blocking the async event loop and stalling other requests.
        def _blocking_query():
            edges = []
            seen = set()
            with driver.session() as session:
                result = session.run(cypher, {"ids": int_ids})
                for record in result:
                    src_eid = id_to_element_id.get(record["src"], str(record["src"]))
                    tgt_eid = id_to_element_id.get(record["tgt"], str(record["tgt"]))
                    edge_id = f"{src_eid}-{record['label']}-{tgt_eid}"
                    if edge_id not in seen:
                        seen.add(edge_id)
                        edges.append({
                            "id": edge_id,
                            "source": src_eid,
                            "target": tgt_eid,
                            "label": record["label"],
                        })
            return edges

        return await asyncio.to_thread(_blocking_query)
    except Exception as exc:
        log.warning("Edge fetch failed (non-fatal): %s", exc)
        return []


@router.get("/explore")
async def explore_graph(
    labels: str | None = Query(None, description="Comma-separated entity types to browse"),
    limit: int = Query(100, ge=1, le=500),
):
    """Browse the persistent knowledge base with optional type filters.

    Reshapes the MCP result into {nodes, edges} format expected by the
    frontend's useGraphData hook. Fetches real edges between returned nodes.
    """
    entity_types = [t.strip() for t in labels.split(",")] if labels else None
    result = await _call_tool(
        "query_knowledge_graph",
        {"query": "*", "entity_types": entity_types, "limit": limit},
    )
    if not result.get("success", False):
        return JSONResponse(content=result, status_code=503)

    # Reshape: convert flat results list into {nodes, edges} for frontend
    # Must match the GraphNode interface: {id, name, labels, description, properties}
    raw_results = result.get("results", [])
    _STRIP_KEYS = {"id", "labels", "name", "description", "type", "label", "properties"}
    nodes = []
    node_ids = []
    # Mapping from Memgraph internal int id → element_id string for edge query.
    id_to_element_id: dict[int, str] = {}
    for idx, item in enumerate(raw_results):
        if isinstance(item, dict):
            node_id = item.get("id") or item.get("name") or f"node-{idx}"
            node_labels = item.get("labels") or [item.get("type") or item.get("label") or "Entity"]
            if not isinstance(node_labels, list):
                node_labels = [node_labels]

            # Derive a display name — Study nodes may lack a `name` property
            # but have `pmid`, `nct_id`, or `title` in their properties.
            display_name = item.get("name", "")
            props = item.get("properties", {})
            if not display_name and isinstance(props, dict):
                display_name = (
                    props.get("pmid", "")
                    or props.get("nct_id", "")
                    or props.get("title", "")
                )
            if not display_name:
                # Fallback: check top-level keys for Study-style properties
                display_name = (
                    item.get("pmid", "")
                    or item.get("nct_id", "")
                    or item.get("title", "")
                    or ""
                )

            extra_props = {k: v for k, v in item.items() if k not in _STRIP_KEYS}
            # Merge nested `properties` dict so the frontend can access
            # Study-specific fields (pmid, nct_id, title) consistently.
            if isinstance(props, dict):
                for pk, pv in props.items():
                    if pk not in extra_props:
                        extra_props[pk] = pv

            nodes.append({
                "id": node_id,
                "name": display_name,
                "labels": node_labels,
                "description": item.get("description", ""),
                "properties": extra_props,
            })
            node_ids.append(node_id)

            # Build int→element_id mapping for the Cypher id() function
            try:
                id_to_element_id[int(node_id)] = node_id
            except (ValueError, TypeError):
                pass  # Non-numeric element_id — edge query will skip

    # Fetch real edges between the returned nodes
    edges = await _fetch_edges_between_nodes(node_ids, id_to_element_id)

    return JSONResponse(content={
        "success": True,
        "nodes": nodes,
        "edges": edges,
        "total_results": result.get("total_results", len(nodes)),
    })
