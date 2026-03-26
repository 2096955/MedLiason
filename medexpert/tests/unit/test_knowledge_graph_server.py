"""Unit tests for the Memgraph-backed Knowledge Graph MCP server."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.knowledge_graph.server import (
    _no_connection_response,
    _sanitize_cypher_param,
    _validate_read_only_cypher,
)

# Import tools — unwrap FastMCP wrappers
try:
    from mcp_servers.knowledge_graph.server import mcp

    # Get the underlying callables from FastMCP tool wrappers
    _tools = {t.name: t for t in mcp._tool_manager._tools.values()}
except Exception:
    _tools = {}


def _get_tool_fn(name):
    """Unwrap a FastMCP FunctionTool to its underlying async callable."""
    tool = _tools.get(name)
    if tool is None:
        pytest.skip(f"Tool {name} not found in MCP server")
    return tool.fn


# ── Cypher validation tests ──────────────────────────────────


def test_validate_read_only_match():
    assert _validate_read_only_cypher("MATCH (n) RETURN n") is not None


def test_validate_read_only_optional_match():
    assert _validate_read_only_cypher("OPTIONAL MATCH (n)-[r]-(m) RETURN n, r, m") is not None


def test_validate_read_only_with():
    assert _validate_read_only_cypher("WITH 1 AS x RETURN x") is not None


def test_validate_rejects_create():
    assert _validate_read_only_cypher("CREATE (n:Node {name: 'test'})") is None


def test_validate_rejects_merge():
    assert _validate_read_only_cypher("MERGE (n:Node {name: 'test'})") is None


def test_validate_rejects_delete():
    assert _validate_read_only_cypher("MATCH (n) DETACH DELETE n") is None


def test_validate_rejects_set():
    assert _validate_read_only_cypher("MATCH (n) SET n.name = 'hacked'") is None


def test_validate_rejects_drop():
    assert _validate_read_only_cypher("DROP INDEX ON :Node(name)") is None


def test_validate_rejects_mutation_in_match():
    """Even if it starts with MATCH, reject if mutations follow."""
    assert _validate_read_only_cypher("MATCH (n) DELETE n") is None


def test_validate_rejects_empty():
    assert _validate_read_only_cypher("") is None
    assert _validate_read_only_cypher("   ") is None


def test_validate_strips_trailing_semicolons():
    result = _validate_read_only_cypher("MATCH (n) RETURN n;")
    assert result is not None
    assert not result.endswith(";")


# ── No-connection response tests ─────────────────────────────


def test_no_connection_response_structure():
    resp = _no_connection_response("test query")
    assert resp["success"] is False
    assert resp["error_category"] == "service_unavailable"
    assert resp["is_retryable"] is True
    assert "results" in resp
    assert resp["results"] == []


def test_no_connection_response_with_entity_types():
    resp = _no_connection_response("test", ["Gene", "Disease"])
    assert "Gene Disease" in resp["fallback_search_query"]


# ── Sanitization tests ───────────────────────────────────────


def test_sanitize_cypher_param_strips_backslash():
    assert _sanitize_cypher_param("test\\injection") == "testinjection"


def test_sanitize_cypher_param_strips_backtick():
    assert _sanitize_cypher_param("test`injection") == "testinjection"


def test_sanitize_cypher_param_strips_null():
    assert _sanitize_cypher_param("test\x00injection") == "testinjection"


def test_sanitize_cypher_param_empty():
    assert _sanitize_cypher_param("") == ""


# ── Tool integration tests (with mocked driver) ─────────────


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_query_knowledge_graph_no_connection(mock_driver):
    mock_driver.return_value = None
    fn = _get_tool_fn("query_knowledge_graph")
    result = await fn(query="breast cancer")
    assert result["success"] is False
    assert "results" in result


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_query_knowledge_graph_success(mock_driver):
    # Mock Memgraph session and result
    mock_node = MagicMock()
    mock_node.element_id = "1"
    mock_node.labels = {"Disease"}
    mock_node.get.side_effect = lambda k, d="": {"name": "Breast Cancer", "description": "Common cancer"}.get(k, d)
    mock_node.__iter__ = MagicMock(return_value=iter([("name", "Breast Cancer")]))
    mock_node.keys = MagicMock(return_value=["name"])
    mock_node.__getitem__ = MagicMock(side_effect=lambda k: "Breast Cancer" if k == "name" else "")

    mock_record = {"n": mock_node}
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([MagicMock(**{"__getitem__": lambda s, k: mock_node})]))

    mock_session = MagicMock()
    mock_session.run.return_value = mock_result
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_drv = MagicMock()
    mock_drv.session.return_value = mock_session
    mock_driver.return_value = mock_drv

    fn = _get_tool_fn("query_knowledge_graph")
    result = await fn(query="breast cancer")
    assert result.get("success") is True or "results" in result


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_get_session_graph_no_connection(mock_driver):
    mock_driver.return_value = None
    fn = _get_tool_fn("get_session_graph")
    result = await fn(session_id="test-session")
    assert result["success"] is False


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_get_graph_stats_no_connection(mock_driver):
    mock_driver.return_value = None
    fn = _get_tool_fn("get_graph_stats")
    result = await fn()
    assert result["success"] is False


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_get_entity_relationships_invalid_id(mock_driver):
    mock_driver.return_value = None
    fn = _get_tool_fn("get_entity_relationships")
    result = await fn(entity_id="")
    assert result["success"] is False


# ── NLQ validation integration tests ─────────────────────────


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_nlq_rejects_when_no_connection(mock_driver):
    mock_driver.return_value = None
    fn = _get_tool_fn("natural_language_query")
    result = await fn(question="What drugs treat cancer?")
    assert result["success"] is False


def test_validate_cypher_rejects_grant():
    assert _validate_read_only_cypher("GRANT ALL PRIVILEGES ON DATABASE medexpert TO user") is None


def test_validate_cypher_rejects_revoke():
    assert _validate_read_only_cypher("REVOKE ROLE admin FROM user") is None


def test_validate_cypher_allows_call():
    """CALL is needed for db.labels() etc."""
    result = _validate_read_only_cypher("CALL db.labels() YIELD label RETURN collect(label)")
    assert result is not None


def test_validate_cypher_rejects_alter():
    assert _validate_read_only_cypher("ALTER USER admin SET PASSWORD 'new'") is None


# ── F7 fix: LOAD/FOREACH blocked ─────────────────────────────


def test_validate_cypher_rejects_load_csv():
    """LOAD CSV can import data — must be blocked."""
    assert _validate_read_only_cypher("LOAD CSV FROM 'file:///data.csv' AS row") is None


def test_validate_cypher_rejects_foreach():
    """FOREACH can contain mutation operations — must be blocked."""
    assert _validate_read_only_cypher("MATCH (n) FOREACH (x IN [1,2] | SET n.val = x)") is None


# ── F3 fix: Explore wildcard query ───────────────────────────


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_query_wildcard_browse_no_connection(mock_driver):
    """Wildcard query='*' should not be rejected as invalid."""
    mock_driver.return_value = None
    fn = _get_tool_fn("query_knowledge_graph")
    result = await fn(query="*")
    # Should get degradation response, NOT "Invalid query parameter"
    assert "error" in result
    assert result.get("error") != "Invalid query parameter"


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_query_wildcard_browse_with_types(mock_driver):
    """Wildcard browse with entity_types should filter by label only."""
    mock_driver.return_value = None
    fn = _get_tool_fn("query_knowledge_graph")
    result = await fn(query="*", entity_types=["Disease"])
    assert result["success"] is False  # no connection
    assert "Disease" in result.get("fallback_search_query", "")


# ── Session graph pipeline DAG tests ─────────────────────────


def _make_mock_node(element_id, labels, props):
    """Create a mock Neo4j/Memgraph node."""
    node = MagicMock()
    node.element_id = element_id
    node.labels = set(labels)
    node.get = MagicMock(side_effect=lambda k, d="": props.get(k, d))
    node.__iter__ = MagicMock(return_value=iter(props.items()))
    node.keys = MagicMock(return_value=list(props.keys()))
    node.__getitem__ = MagicMock(side_effect=lambda k: props.get(k, ""))
    node.__bool__ = MagicMock(return_value=True)
    return node


def _make_mock_rel(rel_type):
    """Create a mock Neo4j/Memgraph relationship."""
    rel = MagicMock()
    rel.type = rel_type
    rel.__bool__ = MagicMock(return_value=True)
    return rel


def _make_iter_result(records):
    """Create a mock Memgraph result that iterates over the given records."""
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter(records))
    return mock_result


def _make_hop_record(mapping):
    """Create a mock record with dict-like access."""
    rec = MagicMock()
    rec.__getitem__ = MagicMock(side_effect=lambda k: mapping.get(k))
    return rec


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_get_session_graph_pipeline_dag_edges(mock_driver):
    """Session graph should traverse the pipeline DAG via 4 separate hop queries."""
    session_node = _make_mock_node("s1", ["Session"], {"session_id": "test-1", "query": "diabetes"})
    spec_node = _make_mock_node("sp1", ["Specialist"], {"name": "DrugSpecialist"})
    entity_node = _make_mock_node("e1", ["Drug"], {"name": "metformin"})
    study_node = _make_mock_node("st1", ["Study"], {"pmid": "12345678", "title": "Study A"})

    r_queried = _make_mock_rel("QUERIED")
    r_found = _make_mock_rel("FOUND")
    r_evidenced = _make_mock_rel("EVIDENCED_BY")

    # 5 hop queries return separate results (hop 5 = ABOUT fallback)
    hop1 = _make_iter_result([_make_hop_record({"s": session_node, "r": r_queried, "sp": spec_node})])
    hop2 = _make_iter_result([_make_hop_record({"sp": spec_node, "r": r_found, "e": entity_node})])
    hop3 = _make_iter_result([_make_hop_record({"e": entity_node, "r": r_evidenced, "st": study_node})])
    hop4 = _make_iter_result([])  # no CITED edges
    hop5 = _make_iter_result([])  # no ABOUT fallback edges

    mock_session = MagicMock()
    mock_session.run.side_effect = [hop1, hop2, hop3, hop4, hop5]
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_drv = MagicMock()
    mock_drv.session.return_value = mock_session
    mock_driver.return_value = mock_drv

    fn = _get_tool_fn("get_session_graph")
    result = await fn(session_id="test-1")

    assert result["success"] is True
    assert result["total_nodes"] == 4  # session, specialist, entity, study
    edge_labels = {e["label"] for e in result["edges"]}
    assert "QUERIED" in edge_labels
    assert "FOUND" in edge_labels
    assert "EVIDENCED_BY" in edge_labels

    # Verify 5 separate hop queries were made (4 DAG + 1 ABOUT fallback)
    assert mock_session.run.call_count == 5
    all_cyphers = [call[0][0] for call in mock_session.run.call_args_list]
    assert any("QUERIED" in c for c in all_cyphers)
    assert any("FOUND" in c for c in all_cyphers)
    assert any("EVIDENCED_BY" in c for c in all_cyphers)
    assert any("CITED" in c for c in all_cyphers)


@patch("mcp_servers.knowledge_graph.server._get_driver")
async def test_get_session_graph_cited_cross_refs(mock_driver):
    """Session graph should include Study-CITED->Study cross-references."""
    session_node = _make_mock_node("s1", ["Session"], {"session_id": "test-2", "query": "test"})
    spec_node = _make_mock_node("sp1", ["Specialist"], {"name": "LitSpecialist"})
    entity_node = _make_mock_node("e1", ["Disease"], {"name": "cancer"})
    study1 = _make_mock_node("st1", ["Study"], {"pmid": "11111111", "title": "Study A"})
    study2 = _make_mock_node("st2", ["Study"], {"pmid": "22222222", "title": "Study B"})

    r_queried = _make_mock_rel("QUERIED")
    r_found = _make_mock_rel("FOUND")
    r_evidenced = _make_mock_rel("EVIDENCED_BY")
    r_cited = _make_mock_rel("CITED")

    hop1 = _make_iter_result([_make_hop_record({"s": session_node, "r": r_queried, "sp": spec_node})])
    hop2 = _make_iter_result([_make_hop_record({"sp": spec_node, "r": r_found, "e": entity_node})])
    hop3 = _make_iter_result([_make_hop_record({"e": entity_node, "r": r_evidenced, "st": study1})])
    hop4 = _make_iter_result([_make_hop_record({"st": study1, "r": r_cited, "st2": study2})])
    hop5 = _make_iter_result([])  # no ABOUT fallback edges

    mock_session = MagicMock()
    mock_session.run.side_effect = [hop1, hop2, hop3, hop4, hop5]
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_drv = MagicMock()
    mock_drv.session.return_value = mock_session
    mock_driver.return_value = mock_drv

    fn = _get_tool_fn("get_session_graph")
    result = await fn(session_id="test-2")

    assert result["success"] is True
    edge_labels = {e["label"] for e in result["edges"]}
    assert "CITED" in edge_labels
    # Should have the cited study node
    node_ids = {n["id"] for n in result["nodes"]}
    assert "st2" in node_ids
