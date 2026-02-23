"""Knowledge Graph MCP Server (Neo4j) — Honest Degradation.

Provides a knowledge graph query interface backed by Neo4j for
biomedical entity relationships. If Neo4j is not available, returns
structured error responses with fallback search queries.

NEVER fabricates medical data. Empty results are always preferred
over fake data.

Port: 9011
"""

import logging
import os
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._security import sanitize_query

log = logging.getLogger(__name__)

mcp = FastMCP("Knowledge Graph")

NEO4J_URI = os.environ.get("NEO4J_URI", "")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

# Lazy-loaded driver
_driver = None


def _get_driver():
    """Attempt to create or return the Neo4j driver singleton."""
    global _driver
    if _driver is not None:
        return _driver

    if not NEO4J_URI:
        return None

    try:
        from neo4j import GraphDatabase

        _driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
        )
        # Verify connectivity
        _driver.verify_connectivity()
        log.info("Connected to Neo4j at %s", NEO4J_URI)
        return _driver
    except ImportError:
        log.warning("neo4j driver package not installed.")
        return None
    except Exception as exc:
        log.warning("Failed to connect to Neo4j: %s", exc)
        _driver = None
        return None


def _no_connection_response(query: str, entity_types: list[str] | None = None) -> dict:
    """Return a structured degradation response when Neo4j is unavailable."""
    type_hint = f" {' '.join(entity_types)}" if entity_types else ""
    fallback = f"{query}{type_hint} biomedical knowledge graph"
    return {
        "status": "no_connection",
        "results": [],
        "message": "Knowledge graph not available. Neo4j connection required.",
        "fallback_search_query": fallback,
    }


def _sanitize_cypher_param(value: str) -> str:
    """Sanitize a value for use as a Cypher parameter.

    We use parameterized queries, but this adds defense-in-depth
    by stripping control characters.
    """
    if not value:
        return ""
    # Remove Cypher-dangerous characters as a defense-in-depth measure
    # (parameterized queries handle the real protection)
    return value.replace("\\", "").replace("`", "").replace("\x00", "")


@mcp.tool()
async def query_knowledge_graph(
    query: str,
    entity_types: list[str] | None = None,
) -> dict:
    """Query the biomedical knowledge graph for entities and relationships.

    Searches the Neo4j knowledge graph for biomedical entities matching
    the query. If Neo4j is not available, returns a structured error
    with a fallback search query for alternative data sources.

    Args:
        query: Natural language or entity search query (e.g., "BRCA1 breast cancer").
        entity_types: Optional list of entity types to filter (e.g., ["Gene", "Disease", "Drug"]).

    Returns:
        Dictionary with graph query results or degradation response.
    """
    safe_query = sanitize_query(query)
    if not safe_query:
        return {"error": "Invalid query parameter", "results": []}

    driver = _get_driver()
    if driver is None:
        return _no_connection_response(safe_query, entity_types)

    try:
        # Build Cypher query with parameterized inputs
        if entity_types:
            safe_types = [sanitize_query(t, max_len=50) for t in entity_types if t]
            label_filter = " OR ".join(
                [f"any(label IN labels(n) WHERE label = $type_{i})" for i in range(len(safe_types))]
            )
            cypher = (
                f"MATCH (n) WHERE ({label_filter}) AND "
                f"(n.name CONTAINS $query OR n.description CONTAINS $query) "
                f"RETURN n LIMIT 25"
            )
            params = {"query": _sanitize_cypher_param(safe_query)}
            for i, t in enumerate(safe_types):
                params[f"type_{i}"] = _sanitize_cypher_param(t)
        else:
            cypher = (
                "MATCH (n) WHERE n.name CONTAINS $query OR "
                "n.description CONTAINS $query "
                "RETURN n LIMIT 25"
            )
            params = {"query": _sanitize_cypher_param(safe_query)}

        with driver.session() as session:
            result = session.run(cypher, params)
            records = []
            for record in result:
                node = record["n"]
                records.append({
                    "id": node.element_id,
                    "labels": list(node.labels),
                    "name": node.get("name", ""),
                    "description": node.get("description", ""),
                    "properties": dict(node),
                })

        return {
            "query": safe_query,
            "entity_types": entity_types,
            "results": records,
            "total_results": len(records),
        }

    except Exception as exc:
        log.error("Knowledge graph query failed: %s", exc)
        return _no_connection_response(safe_query, entity_types)


@mcp.tool()
async def get_entity_relationships(entity_id: str) -> dict:
    """Get relationships for a specific entity in the knowledge graph.

    Retrieves all incoming and outgoing relationships for a given
    entity node, including relationship types and connected entities.

    Args:
        entity_id: Entity identifier (node element ID or name).

    Returns:
        Dictionary with relationship data or degradation response.
    """
    safe_id = sanitize_query(entity_id, max_len=200)
    if not safe_id:
        return {"error": "Invalid entity_id parameter"}

    driver = _get_driver()
    if driver is None:
        return _no_connection_response(safe_id)

    try:
        cypher = (
            "MATCH (n)-[r]-(m) "
            "WHERE n.name = $entity_id OR elementId(n) = $entity_id "
            "RETURN n, type(r) AS rel_type, r, m LIMIT 50"
        )
        params = {"entity_id": _sanitize_cypher_param(safe_id)}

        with driver.session() as session:
            result = session.run(cypher, params)
            relationships = []
            for record in result:
                source = record["n"]
                target = record["m"]
                relationships.append({
                    "source": {
                        "id": source.element_id,
                        "name": source.get("name", ""),
                        "labels": list(source.labels),
                    },
                    "relationship": record["rel_type"],
                    "target": {
                        "id": target.element_id,
                        "name": target.get("name", ""),
                        "labels": list(target.labels),
                    },
                })

        return {
            "entity_id": safe_id,
            "relationships": relationships,
            "total_relationships": len(relationships),
        }

    except Exception as exc:
        log.error("Entity relationship query failed: %s", exc)
        return _no_connection_response(safe_id)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9011)
