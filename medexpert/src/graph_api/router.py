"""Knowledge Graph REST API router for MedExpert.

Calls the Knowledge Graph MCP server tools directly via Python imports
(same process during `sam run`). This avoids network overhead and the
non-existent HTTP endpoint issue with FastMCP SSE transport.

Mounted at /api/v1/graph by the gateway's main.py (guarded import).
"""

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


@router.get("/explore")
async def explore_graph(
    labels: str | None = Query(None, description="Comma-separated entity types to browse"),
    limit: int = Query(100, ge=1, le=500),
):
    """Browse the persistent knowledge base with optional type filters.

    Reshapes the MCP result into {nodes, edges} format expected by the
    frontend's useGraphData hook.
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
    for idx, item in enumerate(raw_results):
        if isinstance(item, dict):
            node_id = item.get("id") or item.get("name") or f"node-{idx}"
            node_labels = item.get("labels") or [item.get("type") or item.get("label") or "Entity"]
            nodes.append({
                "id": node_id,
                "name": item.get("name", ""),
                "labels": node_labels if isinstance(node_labels, list) else [node_labels],
                "description": item.get("description", ""),
                "properties": {k: v for k, v in item.items() if k not in _STRIP_KEYS},
            })
    return JSONResponse(content={
        "success": True,
        "nodes": nodes,
        "edges": [],
        "total_results": result.get("total_results", len(nodes)),
    })
