"""Unit tests for the graph_writer DynamicTool."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from lifesci_tools.graph_writer import (
    GraphWriterTool,
    build_session_cypher,
    extract_entities_from_sources,
    extract_inline_entities,
    extract_nct_ids,
    extract_pmids,
)


@pytest.fixture
def tool():
    return GraphWriterTool()


@pytest.fixture
def ctx():
    c = MagicMock()
    c.session = MagicMock()
    c.session.id = "test-session-001"
    return c


# ── Entity extraction tests ──────────────────────────────────


def test_extract_pmids_basic():
    text = "See PMID:12345678 and PMID: 87654321 for details."
    result = extract_pmids(text)
    assert "12345678" in result
    assert "87654321" in result


def test_extract_pmids_deduplicates():
    text = "PMID:12345678 is important. Also see PMID:12345678 again."
    result = extract_pmids(text)
    assert len(result) == 1


def test_extract_pmids_empty():
    assert extract_pmids("no pmids here") == []


def test_extract_nct_ids():
    text = "Trial NCT01234567 and NCT98765432 are relevant."
    result = extract_nct_ids(text)
    assert "NCT01234567" in result
    assert "NCT98765432" in result


def test_extract_nct_ids_case_insensitive():
    result = extract_nct_ids("trial nct01234567")
    assert "NCT01234567" in result


def test_extract_inline_diseases():
    text = "Found [[disease:breast cancer]] and [[disease:diabetes]]."
    result = extract_inline_entities(text)
    assert "breast cancer" in result["diseases"]
    assert "diabetes" in result["diseases"]


def test_extract_inline_drugs():
    text = "Use [[drug:metformin]] for treatment."
    result = extract_inline_entities(text)
    assert "metformin" in result["drugs"]


def test_extract_inline_genes():
    text = "BRCA1: [[gene:BRCA1]] mutation."
    result = extract_inline_entities(text)
    assert "BRCA1" in result["genes"]


def test_extract_inline_empty():
    result = extract_inline_entities("no tagged entities")
    assert result["diseases"] == []
    assert result["drugs"] == []
    assert result["genes"] == []


# ── Source extraction tests ──────────────────────────────────


def test_extract_entities_from_sources():
    sources = [
        {"pmid": "12345678", "title": "Study A", "publication_year": "2024"},
        {"nct_id": "NCT01234567", "title": "Trial B"},
        {"doi": "10.1234/test", "title": "Paper C", "year": "2023"},
    ]
    result = extract_entities_from_sources(sources)
    assert len(result["studies"]) == 3
    assert result["studies"][0]["pmid"] == "12345678"
    assert result["studies"][1]["nct_id"] == "NCT01234567"
    assert result["studies"][2]["doi"] == "10.1234/test"


def test_extract_entities_deduplicates():
    sources = [
        {"pmid": "12345678", "title": "Study A"},
        {"pmid": "12345678", "title": "Study A duplicate"},
    ]
    result = extract_entities_from_sources(sources)
    assert len(result["studies"]) == 1


def test_extract_entities_skips_empty():
    sources = [{"title": "No ID"}]
    result = extract_entities_from_sources(sources)
    assert len(result["studies"]) == 0


# ── Cypher generation tests ──────────────────────────────────


def test_build_session_cypher_basic():
    statements = build_session_cypher(
        session_id="s1",
        query_text="breast cancer treatment",
        domain="oncology",
        specialists_used=["LiteratureSpecialist"],
        studies=[{"pmid": "12345678", "nct_id": "", "doi": "", "title": "Study", "year": "2024"}],
        diseases=["breast cancer"],
        drugs=["tamoxifen"],
        genes=["BRCA1"],
    )
    # Session + 1 specialist + 1 study + 1 disease + 1 drug + 1 gene = 6 statements
    assert len(statements) == 6
    # All should be (cypher, params) tuples
    for cypher, params in statements:
        assert isinstance(cypher, str)
        assert isinstance(params, dict)
        assert "MERGE" in cypher


def test_build_session_cypher_idempotent():
    """MERGE statements should use ON CREATE SET for idempotency."""
    statements = build_session_cypher(
        session_id="s1",
        query_text="test",
        domain="general",
        specialists_used=[],
        studies=[],
        diseases=[],
        drugs=[],
        genes=[],
    )
    # Just the session node
    assert len(statements) == 1
    cypher, params = statements[0]
    assert "MERGE" in cypher
    assert "ON CREATE SET" in cypher


def test_build_session_cypher_truncates_long_values():
    statements = build_session_cypher(
        session_id="s1",
        query_text="x" * 2000,
        domain="oncology",
        specialists_used=[],
        studies=[],
        diseases=["a" * 300],
        drugs=[],
        genes=[],
    )
    # Session + 1 disease = 2 statements
    assert len(statements) == 2
    _, params = statements[0]
    assert len(params["query"]) == 1000  # truncated
    _, params = statements[1]
    assert len(params["name"]) == 200  # truncated


# ── Tool execution tests ─────────────────────────────────────


@patch("lifesci_tools.graph_writer._get_rw_driver")
@patch("lifesci_tools.graph_writer._read_session_data")
async def test_tool_returns_structured_error_when_memgraph_down(mock_redis, mock_driver, tool, ctx):
    """graph_writer never raises — returns structured error."""
    mock_driver.return_value = None
    mock_redis.return_value = {"specialists_used": [], "query_text": "", "domain": "", "sources": [], "findings_text": ""}

    result = await tool._run_async_impl(
        {"session_id": "test-session", "query_text": "test"},
        ctx,
    )
    assert result["success"] is False
    assert result["error_category"] == "graph_unavailable"
    assert result["nodes_created"] == 0


@patch("lifesci_tools.graph_writer._get_rw_driver")
@patch("lifesci_tools.graph_writer._read_session_data")
async def test_tool_succeeds_with_mock_driver(mock_redis, mock_driver, tool, ctx):
    """graph_writer writes nodes when Memgraph is available."""
    # Mock the driver and session
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_summary = MagicMock()
    mock_summary.counters.nodes_created = 3
    mock_summary.counters.relationships_created = 2
    mock_result.consume.return_value = mock_summary
    mock_session.run.return_value = mock_result
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    mock_drv = MagicMock()
    mock_drv.session.return_value = mock_session
    mock_driver.return_value = mock_drv

    mock_redis.return_value = {
        "specialists_used": ["LiteratureSpecialist"],
        "query_text": "breast cancer",
        "domain": "oncology",
        "sources": [{"pmid": "12345678", "title": "Study A"}],
        "findings_text": "BRCA1 [[disease:breast cancer]]",
    }

    result = await tool._run_async_impl(
        {"session_id": "test-session"},
        ctx,
    )
    assert result["success"] is True
    assert result["nodes_created"] > 0 or result["total_statements"] > 0


async def test_tool_requires_session_id(tool, ctx):
    """graph_writer fails gracefully without session_id."""
    ctx.session.id = ""
    result = await tool._run_async_impl({"session_id": ""}, ctx)
    assert result["success"] is False
    assert "session_id" in result["error"]


@patch("lifesci_tools.graph_writer._get_rw_driver")
@patch("lifesci_tools.graph_writer._read_session_data")
async def test_tool_catches_exceptions(mock_redis, mock_driver, tool, ctx):
    """graph_writer catches unexpected errors."""
    mock_driver.side_effect = RuntimeError("Boom")
    mock_redis.return_value = {"specialists_used": [], "query_text": "", "domain": "", "sources": [], "findings_text": ""}

    result = await tool._run_async_impl(
        {"session_id": "test-session", "query_text": "test"},
        ctx,
    )
    assert result["success"] is False
    assert result["is_retryable"] is True


# ── Redis key format tests (F2 fix) ─────────────────────────


async def test_read_session_data_uses_medexpert_prefix():
    """Redis keys must use medexpert:{session_id}:{namespace}:{key} format."""
    from lifesci_tools.graph_writer import _read_session_data

    # The function should use the medexpert: prefix
    # We verify by checking the source code references the correct prefix
    import inspect
    source = inspect.getsource(_read_session_data)
    assert "medexpert:" in source, "Redis keys must use medexpert: prefix to match memory_plane"
    assert 'f"medexpert:{session_id}' in source or "f\"{pfx}" in source or 'f"{pfx}:' in source


# ── Study MERGE key tests (F16 fix) ─────────────────────────


def test_study_merge_uses_pmid_when_available():
    """Study with PMID uses pmid as MERGE key."""
    statements = build_session_cypher(
        session_id="s1", query_text="test", domain="",
        specialists_used=[], diseases=[], drugs=[], genes=[],
        studies=[{"pmid": "12345678", "nct_id": "NCT01234567", "doi": "", "title": "T", "year": "2024"}],
    )
    # Session + 1 study = 2 statements
    cypher, params = statements[1]
    assert "{pmid:" in cypher
    assert params["primary_id"] == "12345678"


def test_study_merge_uses_nct_when_no_pmid():
    """Study without PMID uses nct_id as MERGE key."""
    statements = build_session_cypher(
        session_id="s1", query_text="test", domain="",
        specialists_used=[], diseases=[], drugs=[], genes=[],
        studies=[{"pmid": "", "nct_id": "NCT01234567", "doi": "", "title": "T", "year": "2024"}],
    )
    cypher, params = statements[1]
    assert "{nct_id:" in cypher
    assert params["primary_id"] == "NCT01234567"


def test_study_merge_uses_doi_when_no_pmid_or_nct():
    """Study with only DOI uses doi as MERGE key."""
    statements = build_session_cypher(
        session_id="s1", query_text="test", domain="",
        specialists_used=[], diseases=[], drugs=[], genes=[],
        studies=[{"pmid": "", "nct_id": "", "doi": "10.1234/test", "title": "T", "year": ""}],
    )
    cypher, params = statements[1]
    assert "{doi:" in cypher
    assert params["primary_id"] == "10.1234/test"
