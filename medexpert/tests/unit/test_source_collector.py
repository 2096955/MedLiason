"""Unit tests for the source_collector tool."""

import sys
import os
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from lifesci_tools.source_collector import SourceCollectorTool


@pytest.fixture
def tool():
    return SourceCollectorTool()


@pytest.fixture
def ctx():
    c = MagicMock()
    c.session = MagicMock()
    c.session.id = "test-session-001"
    return c


# ── URL construction ──────────────────────────────────────────


async def test_pmid_url(tool, ctx):
    """PMID generates correct PubMed URL."""
    result = await tool._run_async_impl(
        {
            "query": "diabetes treatment",
            "sources": [
                {"title": "Study A", "snippet": "Metformin works.", "pmid": "12345678"}
            ],
        },
        ctx,
    )
    assert result["status"] == "published"
    assert "pubmed.ncbi.nlm.nih.gov/12345678" in result["formatted_results"]


async def test_nct_url(tool, ctx):
    """NCT ID generates correct ClinicalTrials.gov URL."""
    result = await tool._run_async_impl(
        {
            "query": "cancer trial",
            "sources": [
                {"title": "Trial B", "snippet": "Phase 3.", "nct_id": "NCT06000001"}
            ],
        },
        ctx,
    )
    assert "clinicaltrials.gov/study/NCT06000001" in result["formatted_results"]


async def test_doi_url(tool, ctx):
    """DOI generates correct doi.org URL."""
    result = await tool._run_async_impl(
        {
            "query": "genetics",
            "sources": [
                {"title": "Paper C", "snippet": "BRCA1 variants.", "doi": "10.1038/s41586-023-06100-0"}
            ],
        },
        ctx,
    )
    assert "doi.org/10.1038/s41586-023-06100-0" in result["formatted_results"]


async def test_explicit_url(tool, ctx):
    """Explicit URL is used as-is."""
    result = await tool._run_async_impl(
        {
            "query": "fda warning",
            "sources": [
                {"title": "FDA Alert", "snippet": "Safety alert.", "url": "https://fda.gov/safety/123"}
            ],
        },
        ctx,
    )
    assert "fda.gov/safety/123" in result["formatted_results"]


# ── Citation ID generation ────────────────────────────────────


async def test_citation_ids(tool, ctx):
    """Citation IDs are generated in s0rN format."""
    result = await tool._run_async_impl(
        {
            "query": "test",
            "sources": [
                {"title": "A", "snippet": "a"},
                {"title": "B", "snippet": "b"},
                {"title": "C", "snippet": "c"},
            ],
        },
        ctx,
    )
    assert result["valid_citation_ids"] == ["s0r0", "s0r1", "s0r2"]
    assert result["num_sources"] == 3


# ── Edge cases ────────────────────────────────────────────────


async def test_empty_sources(tool, ctx):
    """Empty sources list returns no_sources status."""
    result = await tool._run_async_impl(
        {"query": "test", "sources": []}, ctx
    )
    assert result["status"] == "no_sources"


async def test_missing_sources_key(tool, ctx):
    """Missing sources key handled gracefully."""
    result = await tool._run_async_impl(
        {"query": "test"}, ctx
    )
    assert result["status"] == "no_sources"


async def test_source_without_identifier(tool, ctx):
    """Source with no identifier gets empty URL."""
    result = await tool._run_async_impl(
        {
            "query": "test",
            "sources": [{"title": "Orphan", "snippet": "No ID."}],
        },
        ctx,
    )
    assert result["status"] == "published"
    assert result["num_sources"] == 1


# ── RAG metadata ──────────────────────────────────────────────


async def test_rag_metadata_structure(tool, ctx):
    """RAG metadata has required structure for frontend display."""
    result = await tool._run_async_impl(
        {
            "query": "lung cancer",
            "sources": [
                {"title": "Study", "snippet": "Findings.", "pmid": "99999"}
            ],
        },
        ctx,
    )
    rag = result["rag_metadata"]
    assert rag["query"] == "lung cancer"
    # model_dump(by_alias=True) converts to camelCase
    assert rag.get("searchType") == "web_search" or rag.get("search_type") == "web_search"
    assert "timestamp" in rag
    assert len(rag["sources"]) == 1


# ── provenance metadata ──────────────────────────────────────


async def test_provenance_metadata(tool, ctx):
    """Provenance fields appear in metadata and formatted results."""
    result = await tool._run_async_impl(
        {
            "query": "drug interactions",
            "sources": [
                {
                    "title": "Study A",
                    "snippet": "Finding.",
                    "pmid": "12345678",
                    "agent_name": "DrugSpecialist",
                    "mcp_server": "openfda",
                    "api_endpoint": "/drug/event.json",
                }
            ],
        },
        ctx,
    )
    assert result["status"] == "published"
    assert "AGENT: DrugSpecialist" in result["formatted_results"]
    assert "MCP SERVER: openfda" in result["formatted_results"]
    rag = result["rag_metadata"]
    source_meta = rag["sources"][0]["metadata"]
    assert source_meta["agent_name"] == "DrugSpecialist"
    assert source_meta["mcp_server"] == "openfda"
    assert source_meta["api_endpoint"] == "/drug/event.json"
