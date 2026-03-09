"""Knowledge Graph MCP Server (Memgraph) — Session + Persistent KB.

Provides a knowledge graph query interface backed by Memgraph for
biomedical entity relationships, session graphs, and NLQ-to-Cypher.

Two connection modes for defense-in-depth:
- Read-only driver (MEMGRAPH_RO_*) for all query tools
- Read-write driver (MEMGRAPH_URL) used only by graph_writer DynamicTool

If Memgraph is not available, returns structured error responses with
fallback search queries. NEVER fabricates medical data.

Port: 9011
"""

import logging
import os
import re
import sys

from fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mcp_servers._http import raise_or_return_error
from mcp_servers._security import sanitize_query

log = logging.getLogger(__name__)

mcp = FastMCP("Knowledge Graph")

# Read-only connection for query tools (defense-in-depth)
MEMGRAPH_URL = os.environ.get("MEMGRAPH_URL", os.environ.get("NEO4J_URI", ""))
MEMGRAPH_USER = os.environ.get(
    "MEMGRAPH_RO_USER", os.environ.get("MEMGRAPH_USER", os.environ.get("NEO4J_USER", "memgraph"))
)
MEMGRAPH_PASSWORD = os.environ.get(
    "MEMGRAPH_RO_PASSWORD", os.environ.get("MEMGRAPH_PASSWORD", os.environ.get("NEO4J_PASSWORD", ""))
)

# NLQ model configuration (uses litellm, already a dependency)
NLQ_MODEL = os.environ.get("NLQ_MODEL", "vertex_ai/gemini-2.5-flash")
NLQ_TIMEOUT = 15  # seconds

# Lazy-loaded driver
_driver = None


def _get_driver():
    """Attempt to create or return the Memgraph driver singleton."""
    global _driver
    if _driver is not None:
        return _driver

    if not MEMGRAPH_URL:
        return None

    try:
        from neo4j import GraphDatabase

        _driver = GraphDatabase.driver(
            MEMGRAPH_URL,
            auth=(MEMGRAPH_USER, MEMGRAPH_PASSWORD),
            connection_timeout=5,
            max_transaction_retry_time=5,
        )
        _driver.verify_connectivity()
        log.info("Connected to Memgraph at %s", MEMGRAPH_URL)
        _ensure_indexes()
        return _driver
    except ImportError:
        log.warning("neo4j driver package not installed.")
        return None
    except Exception as exc:
        log.warning("Failed to connect to Memgraph: %s", exc)
        _driver = None
        return None


def _ensure_indexes():
    """Create indexes for common lookups (idempotent)."""
    if _driver is None:
        return
    index_statements = [
        "CREATE INDEX ON :Session(session_id);",
        "CREATE INDEX ON :Disease(name);",
        "CREATE INDEX ON :Drug(name);",
        "CREATE INDEX ON :Gene(name);",
        "CREATE INDEX ON :Study(pmid);",
        "CREATE INDEX ON :Specialist(name);",
    ]
    try:
        with _driver.session() as session:
            for stmt in index_statements:
                try:
                    session.run(stmt)
                except Exception:
                    pass  # Index already exists — Memgraph returns error, safe to ignore
        log.info("Ensured %d indexes on Memgraph", len(index_statements))
    except Exception as exc:
        log.warning("Index creation failed (non-fatal): %s", exc)


def _no_connection_response(query: str = "", entity_types: list[str] | None = None) -> dict:
    """Return a structured degradation response when Memgraph is unavailable."""
    type_hint = f" {' '.join(entity_types)}" if entity_types else ""
    fallback = f"{query}{type_hint} biomedical knowledge graph"
    return {
        "success": False,
        "error": "Knowledge graph not available. Memgraph connection required.",
        "error_category": "service_unavailable",
        "is_retryable": True,
        "server": "knowledge_graph",
        "tool": "query_knowledge_graph",
        "results": [],
        "fallback_search_query": fallback,
    }


def _sanitize_cypher_param(value: str) -> str:
    """Sanitize a value for use as a Cypher parameter.

    We use parameterized queries, but this adds defense-in-depth
    by stripping control characters.
    """
    if not value:
        return ""
    return value.replace("\\", "").replace("`", "").replace("\x00", "")


# ── Cypher validation for NLQ-generated queries ──────────────────────

_ALLOWED_CYPHER_PREFIXES = re.compile(
    r"^\s*(MATCH|OPTIONAL\s+MATCH|RETURN|WITH|UNWIND|CALL)\b",
    re.IGNORECASE,
)
_MUTATION_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|GRANT|REVOKE|LOAD|FOREACH)\b",
    re.IGNORECASE,
)


def _validate_read_only_cypher(cypher: str) -> str | None:
    """Validate that LLM-generated Cypher is read-only.

    Returns the Cypher string if valid, None if rejected.
    """
    if not cypher or not cypher.strip():
        return None
    stripped = cypher.strip().rstrip(";")
    if not _ALLOWED_CYPHER_PREFIXES.match(stripped):
        log.warning("NLQ Cypher rejected (bad prefix): %s", stripped[:100])
        return None
    if _MUTATION_KEYWORDS.search(stripped):
        log.warning("NLQ Cypher rejected (mutation keyword): %s", stripped[:100])
        return None
    return stripped


def _get_graph_schema() -> str:
    """Fetch the Memgraph schema for NLQ system prompt."""
    driver = _get_driver()
    if driver is None:
        return "Schema unavailable."
    try:
        with driver.session() as session:
            # Get node labels
            result = session.run("CALL db.labels() YIELD label RETURN collect(label) AS labels")
            record = result.single()
            labels = record["labels"] if record else []

            # Get relationship types
            result = session.run(
                "CALL db.relationshipTypes() YIELD relationshipType "
                "RETURN collect(relationshipType) AS types"
            )
            record = result.single()
            rel_types = record["types"] if record else []

        return (
            f"Node labels: {', '.join(labels) if labels else 'none yet'}\n"
            f"Relationship types: {', '.join(rel_types) if rel_types else 'none yet'}\n"
            "Common properties: name, description, session_id, pmid, nct_id, created_at"
        )
    except Exception as exc:
        log.warning("Schema fetch failed: %s", exc)
        return "Schema unavailable."


@mcp.tool()
async def query_knowledge_graph(
    query: str,
    entity_types: list[str] | None = None,
    limit: int = 25,
) -> dict:
    """Query the biomedical knowledge graph for entities and relationships.

    Searches the Memgraph knowledge graph for biomedical entities matching
    the query. Use query="*" to browse all entities (no text filter).
    If Memgraph is not available, returns a structured error
    with a fallback search query for alternative data sources.

    Args:
        query: Search query (e.g., "BRCA1 breast cancer"). Use "*" to browse all.
        entity_types: Optional list of entity types to filter (e.g., ["Gene", "Disease", "Drug"]).
        limit: Maximum number of results (default 25, max 500).

    Returns:
        Dictionary with graph query results or degradation response.
    """
    safe_query = sanitize_query(query)
    # Allow "*" as a browse/wildcard — don't reject it
    if not safe_query and query != "*":
        return {"success": False, "error": "Invalid query parameter", "results": []}

    is_browse = safe_query == "*" or not safe_query
    result_limit = min(max(int(limit), 1), 500)

    driver = _get_driver()
    if driver is None:
        return _no_connection_response(safe_query or "*", entity_types)

    try:
        if is_browse and entity_types:
            # Browse with type filter — no text search
            safe_types = [sanitize_query(t, max_len=50) for t in entity_types if t]
            label_filter = " OR ".join(
                [f"any(label IN labels(n) WHERE label = $type_{i})" for i in range(len(safe_types))]
            )
            cypher = f"MATCH (n) WHERE ({label_filter}) RETURN n LIMIT {result_limit}"
            params = {}
            for i, t in enumerate(safe_types):
                params[f"type_{i}"] = _sanitize_cypher_param(t)
        elif is_browse:
            # Browse all — no filter
            cypher = f"MATCH (n) RETURN n LIMIT {result_limit}"
            params = {}
        elif entity_types:
            safe_types = [sanitize_query(t, max_len=50) for t in entity_types if t]
            label_filter = " OR ".join(
                [f"any(label IN labels(n) WHERE label = $type_{i})" for i in range(len(safe_types))]
            )
            cypher = (
                f"MATCH (n) WHERE ({label_filter}) AND "
                f"(n.name CONTAINS $query OR n.description CONTAINS $query) "
                f"RETURN n LIMIT {result_limit}"
            )
            params = {"query": _sanitize_cypher_param(safe_query)}
            for i, t in enumerate(safe_types):
                params[f"type_{i}"] = _sanitize_cypher_param(t)
        else:
            cypher = (
                "MATCH (n) WHERE n.name CONTAINS $query OR "
                "n.description CONTAINS $query "
                f"RETURN n LIMIT {result_limit}"
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
            "success": True,
            "query": safe_query,
            "entity_types": entity_types,
            "results": records,
            "total_results": len(records),
        }

    except Exception as exc:
        log.error("Knowledge graph query failed: %s", exc)
        return raise_or_return_error(exc, "knowledge_graph", "query_knowledge_graph")


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
        return {"success": False, "error": "Invalid entity_id parameter"}

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
            "success": True,
            "entity_id": safe_id,
            "relationships": relationships,
            "total_relationships": len(relationships),
        }

    except Exception as exc:
        log.error("Entity relationship query failed: %s", exc)
        return raise_or_return_error(exc, "knowledge_graph", "get_entity_relationships")


@mcp.tool()
async def get_session_graph(session_id: str) -> dict:
    """Get the complete knowledge graph for a research session.

    Returns all nodes and edges created during a specific research
    session, including specialists used, sources cited, and entities
    discovered.

    Args:
        session_id: The research session identifier.

    Returns:
        Dictionary with nodes and edges arrays for graph visualization.
    """
    safe_id = sanitize_query(session_id, max_len=200)
    if not safe_id:
        return {"success": False, "error": "Invalid session_id parameter"}

    driver = _get_driver()
    if driver is None:
        return _no_connection_response(safe_id)

    try:
        # Pipeline DAG traversal split into separate hops to avoid
        # cartesian product explosion (6 specialists × 10 entities × 3
        # studies × 2 citations = 360 rows from a single chained query).
        params = {"sid": _sanitize_cypher_param(safe_id)}

        nodes = {}
        edges = {}

        def _add_node(n, fallback_name=""):
            if n and n.element_id not in nodes:
                nodes[n.element_id] = {
                    "id": n.element_id,
                    "labels": list(n.labels),
                    "name": n.get("name", fallback_name),
                    "properties": dict(n),
                }

        def _add_edge(src, rel, tgt):
            if src and rel and tgt:
                edge_id = f"{src.element_id}-{rel.type}-{tgt.element_id}"
                if edge_id not in edges:
                    edges[edge_id] = {
                        "id": edge_id,
                        "source": src.element_id,
                        "target": tgt.element_id,
                        "label": rel.type,
                    }

        with driver.session() as session:
            # Hop 1: Session -> Specialists (QUERIED)
            r1 = session.run(
                "MATCH (s:Session {session_id: $sid})-[r:QUERIED]->(sp:Specialist) "
                "RETURN DISTINCT s, r, sp LIMIT 50",
                params,
            )
            for rec in r1:
                _add_node(rec["s"], safe_id)
                _add_node(rec["sp"])
                _add_edge(rec["s"], rec["r"], rec["sp"])

            # Hop 2: Specialists -> Entities (FOUND)
            r2 = session.run(
                "MATCH (s:Session {session_id: $sid})-[:QUERIED]->(sp:Specialist)-[r:FOUND]->(e) "
                "RETURN DISTINCT sp, r, e LIMIT 200",
                params,
            )
            for rec in r2:
                _add_node(rec["sp"])
                _add_node(rec["e"])
                _add_edge(rec["sp"], rec["r"], rec["e"])

            # Hop 3: Entities -> Studies (EVIDENCED_BY)
            r3 = session.run(
                "MATCH (s:Session {session_id: $sid})-[:QUERIED]->(:Specialist)-[:FOUND]->(e)-[r:EVIDENCED_BY]->(st:Study) "
                "RETURN DISTINCT e, r, st LIMIT 300",
                params,
            )
            for rec in r3:
                _add_node(rec["e"])
                _add_node(rec["st"])
                _add_edge(rec["e"], rec["r"], rec["st"])

            # Hop 4: Study -> Study (CITED cross-references)
            r4 = session.run(
                "MATCH (s:Session {session_id: $sid})-[:QUERIED]->(:Specialist)-[:FOUND]->()-[:EVIDENCED_BY]->(st:Study)-[r:CITED]->(st2:Study) "
                "RETURN DISTINCT st, r, st2 LIMIT 200",
                params,
            )
            for rec in r4:
                _add_node(rec["st"])
                _add_node(rec["st2"])
                _add_edge(rec["st"], rec["r"], rec["st2"])

        return {
            "success": True,
            "session_id": safe_id,
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        }

    except Exception as exc:
        log.error("Session graph query failed: %s", exc)
        return raise_or_return_error(exc, "knowledge_graph", "get_session_graph")


@mcp.tool()
async def get_graph_stats() -> dict:
    """Get summary statistics for the knowledge graph.

    Returns node counts by label and edge counts by type.
    Useful for monitoring graph growth and health checks.

    Returns:
        Dictionary with node_counts and edge_counts.
    """
    driver = _get_driver()
    if driver is None:
        return _no_connection_response()

    try:
        with driver.session() as session:
            # Node counts by label
            result = session.run(
                "MATCH (n) RETURN labels(n) AS labels, count(n) AS cnt"
            )
            node_counts = {}
            total_nodes = 0
            for record in result:
                for label in record["labels"]:
                    node_counts[label] = node_counts.get(label, 0) + record["cnt"]
                total_nodes += record["cnt"]

            # Edge counts by type
            result = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS cnt"
            )
            edge_counts = {}
            total_edges = 0
            for record in result:
                edge_counts[record["rel_type"]] = record["cnt"]
                total_edges += record["cnt"]

        return {
            "success": True,
            "node_counts": node_counts,
            "edge_counts": edge_counts,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
        }

    except Exception as exc:
        log.error("Graph stats query failed: %s", exc)
        return raise_or_return_error(exc, "knowledge_graph", "get_graph_stats")


@mcp.tool()
async def natural_language_query(question: str) -> dict:
    """Query the knowledge graph using natural language.

    Translates a natural language question into a Cypher query using an LLM,
    executes it against Memgraph (read-only), and returns the results.

    The generated Cypher is validated to be read-only before execution.
    Mutation queries (CREATE, DELETE, etc.) are rejected.

    Args:
        question: Natural language question (e.g., "What drugs treat breast cancer?").

    Returns:
        Dictionary with the answer, generated Cypher, and raw results.
    """
    safe_question = sanitize_query(question, max_len=500)
    if not safe_question:
        return {"success": False, "error": "Invalid question parameter"}

    driver = _get_driver()
    if driver is None:
        return _no_connection_response(safe_question)

    try:
        import litellm

        schema = _get_graph_schema()

        system_prompt = (
            "You are a Cypher query generator for a Memgraph biomedical knowledge graph.\n"
            "Generate ONLY a single read-only Cypher MATCH query. No mutations.\n"
            "Do NOT use CREATE, MERGE, SET, DELETE, DROP, or any write operations.\n"
            "Return ONLY the Cypher query, no explanation.\n\n"
            f"Graph schema:\n{schema}\n\n"
            "Always add LIMIT 25 to prevent large result sets.\n"
            "Use parameterized queries where possible."
        )

        # Vertex AI kwargs for ADC authentication
        vertex_kwargs = {}
        vertex_project = os.environ.get("VERTEX_PROJECT", os.environ.get("VERTEXAI_PROJECT", ""))
        vertex_location = os.environ.get("VERTEX_LOCATION", os.environ.get("VERTEXAI_LOCATION", "us-central1"))
        if vertex_project:
            vertex_kwargs["vertex_project"] = vertex_project
        if vertex_location:
            vertex_kwargs["vertex_location"] = vertex_location

        response = await litellm.acompletion(
            model=NLQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": safe_question},
            ],
            temperature=0.0,
            max_tokens=500,
            timeout=NLQ_TIMEOUT,
            **vertex_kwargs,
        )

        generated_cypher = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if generated_cypher.startswith("```"):
            lines = generated_cypher.split("\n")
            generated_cypher = "\n".join(
                line for line in lines if not line.startswith("```")
            ).strip()

        validated_cypher = _validate_read_only_cypher(generated_cypher)
        if validated_cypher is None:
            return {
                "success": False,
                "error": "Generated query was rejected (not read-only).",
                "error_category": "validation_error",
                "is_retryable": False,
                "server": "knowledge_graph",
                "tool": "natural_language_query",
                "generated_cypher": generated_cypher,
            }

        # Execute the validated query with a timeout
        with driver.session() as session:
            result = session.run(validated_cypher)
            records = []
            for record in result:
                row = {}
                for key in record.keys():
                    val = record[key]
                    if hasattr(val, "element_id"):
                        row[key] = {
                            "id": val.element_id,
                            "labels": list(val.labels) if hasattr(val, "labels") else [],
                            "name": val.get("name", ""),
                            "properties": dict(val),
                        }
                    else:
                        row[key] = val
                records.append(row)

        return {
            "success": True,
            "question": safe_question,
            "generated_cypher": validated_cypher,
            "results": records,
            "total_results": len(records),
        }

    except ImportError:
        return {
            "success": False,
            "error": "litellm not installed — NLQ feature unavailable",
            "error_category": "service_unavailable",
            "is_retryable": False,
            "server": "knowledge_graph",
            "tool": "natural_language_query",
        }
    except Exception as exc:
        log.error("NLQ query failed: %s", exc)
        return raise_or_return_error(exc, "knowledge_graph", "natural_language_query")


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9011)
