"""Unit tests for the Knowledge Graph REST API router."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


@pytest.mark.asyncio
@patch("graph_api.router._fetch_edges_between_nodes", new_callable=AsyncMock)
@patch("graph_api.router._call_tool", new_callable=AsyncMock)
async def test_explore_endpoint_fetches_edges(mock_call_tool, mock_fetch_edges):
    """Explore endpoint should fetch real edges between returned nodes."""
    mock_call_tool.return_value = {
        "success": True,
        "results": [
            {"id": "e1", "labels": ["Disease"], "name": "Diabetes", "description": ""},
            {"id": "e2", "labels": ["Drug"], "name": "Metformin", "description": ""},
        ],
        "total_results": 2,
    }
    mock_fetch_edges.return_value = [
        {"id": "e1-FOUND-e2", "source": "e1", "target": "e2", "label": "FOUND"},
    ]

    from graph_api.router import explore_graph

    # Create a mock request-like call (FastAPI injects params)
    response = await explore_graph(labels=None, limit=100)
    data = response.body.decode("utf-8")
    import json

    parsed = json.loads(data)

    assert parsed["success"] is True
    assert len(parsed["nodes"]) == 2
    assert len(parsed["edges"]) == 1
    assert parsed["edges"][0]["label"] == "FOUND"

    # Verify edge fetch was called with node IDs
    mock_fetch_edges.assert_called_once_with(["e1", "e2"])


@pytest.mark.asyncio
@patch("graph_api.router._fetch_edges_between_nodes", new_callable=AsyncMock)
@patch("graph_api.router._call_tool", new_callable=AsyncMock)
async def test_explore_endpoint_no_edges_on_empty_nodes(mock_call_tool, mock_fetch_edges):
    """Explore with zero nodes should not attempt edge fetch."""
    mock_call_tool.return_value = {
        "success": True,
        "results": [],
        "total_results": 0,
    }
    mock_fetch_edges.return_value = []

    from graph_api.router import explore_graph

    response = await explore_graph(labels=None, limit=100)
    import json

    parsed = json.loads(response.body.decode("utf-8"))

    assert parsed["success"] is True
    assert len(parsed["nodes"]) == 0
    assert len(parsed["edges"]) == 0
    # _fetch_edges_between_nodes should still be called with empty list
    mock_fetch_edges.assert_called_once_with([])


@pytest.mark.asyncio
@patch("graph_api.router._fetch_edges_between_nodes", new_callable=AsyncMock)
@patch("graph_api.router._call_tool", new_callable=AsyncMock)
async def test_explore_endpoint_with_label_filter(mock_call_tool, mock_fetch_edges):
    """Explore with label filter passes entity_types to query tool."""
    mock_call_tool.return_value = {
        "success": True,
        "results": [
            {"id": "g1", "labels": ["Gene"], "name": "BRCA1", "description": ""},
        ],
        "total_results": 1,
    }
    mock_fetch_edges.return_value = []

    from graph_api.router import explore_graph

    await explore_graph(labels="Gene,Drug", limit=50)

    # Verify entity_types were passed to query_knowledge_graph
    mock_call_tool.assert_called_once_with(
        "query_knowledge_graph",
        {"query": "*", "entity_types": ["Gene", "Drug"], "limit": 50},
    )


@pytest.mark.asyncio
async def test_fetch_edges_no_driver():
    """_fetch_edges_between_nodes returns [] when driver unavailable."""
    from graph_api.router import _fetch_edges_between_nodes

    with patch(
        "mcp_servers.knowledge_graph.server._get_driver", return_value=None
    ):
        result = await _fetch_edges_between_nodes(["n1", "n2"])
        assert result == []


@pytest.mark.asyncio
async def test_fetch_edges_success():
    """_fetch_edges_between_nodes returns edges from Memgraph."""
    from graph_api.router import _fetch_edges_between_nodes

    mock_record1 = {"src": "n1", "tgt": "n2", "label": "FOUND"}
    mock_record2 = {"src": "n2", "tgt": "n3", "label": "EVIDENCED_BY"}

    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(
        return_value=iter([mock_record1, mock_record2])
    )

    mock_session = MagicMock()
    mock_session.run.return_value = mock_result
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_drv = MagicMock()
    mock_drv.session.return_value = mock_session

    with patch(
        "mcp_servers.knowledge_graph.server._get_driver", return_value=mock_drv
    ):
        result = await _fetch_edges_between_nodes(["n1", "n2", "n3"])

    assert len(result) == 2
    assert result[0]["label"] == "FOUND"
    assert result[1]["label"] == "EVIDENCED_BY"


@pytest.mark.asyncio
async def test_fetch_edges_deduplicates():
    """_fetch_edges_between_nodes deduplicates edges by id."""
    from graph_api.router import _fetch_edges_between_nodes

    # Same edge returned twice
    mock_record = {"src": "n1", "tgt": "n2", "label": "FOUND"}
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(
        return_value=iter([mock_record, mock_record])
    )

    mock_session = MagicMock()
    mock_session.run.return_value = mock_result
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_drv = MagicMock()
    mock_drv.session.return_value = mock_session

    with patch(
        "mcp_servers.knowledge_graph.server._get_driver", return_value=mock_drv
    ):
        result = await _fetch_edges_between_nodes(["n1", "n2"])

    assert len(result) == 1


@pytest.mark.asyncio
async def test_fetch_edges_empty_ids():
    """_fetch_edges_between_nodes returns [] for empty ID list."""
    from graph_api.router import _fetch_edges_between_nodes

    result = await _fetch_edges_between_nodes([])
    assert result == []
