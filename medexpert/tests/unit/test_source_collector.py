"""Unit tests for the source_collector tool."""

import json
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from lifesci_tools.source_collector import (
    SourceCollectorTool,
    cross_reference_findings,
    _extract_inline_refs,
)


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


# ── Inline source reference extraction ───────────────────────


def test_extract_inline_refs_pmid():
    """Extracts [[pmid:XXXX]] references from findings."""
    refs = _extract_inline_refs([
        "Metformin reduces HbA1c by 1.0-1.5% [[pmid:38901234]].",
        "Second finding with another ref [[pmid:99999999]].",
    ])
    assert ("pmid", "38901234") in refs
    assert ("pmid", "99999999") in refs
    assert len(refs) == 2


def test_extract_inline_refs_mixed_types():
    """Extracts mixed reference types: pmid, nct, doi, url."""
    refs = _extract_inline_refs([
        "Finding A [[pmid:12345678]].",
        "Finding B [[nct:NCT06123456]].",
        "Finding C [[doi:10.1038/s41586-023]].",
        "Finding D [[url:https://fda.gov/safety]].",
    ])
    assert ("pmid", "12345678") in refs
    assert ("nct", "NCT06123456") in refs
    assert ("doi", "10.1038/s41586-023") in refs
    assert ("url", "https://fda.gov/safety") in refs
    assert len(refs) == 4


def test_extract_inline_refs_no_refs():
    """Returns empty set when findings have no inline refs."""
    refs = _extract_inline_refs(["Plain finding with no references."])
    assert len(refs) == 0


def test_extract_inline_refs_empty_list():
    """Returns empty set for empty findings list."""
    refs = _extract_inline_refs([])
    assert len(refs) == 0


def test_extract_inline_refs_non_string_items():
    """Gracefully handles non-string items in findings list."""
    refs = _extract_inline_refs([None, 42, "Valid finding [[pmid:111]].", {}])
    assert ("pmid", "111") in refs
    assert len(refs) == 1


def test_extract_inline_refs_multiple_in_one_finding():
    """Multiple refs in a single finding are all extracted."""
    refs = _extract_inline_refs([
        "Drug A [[pmid:111]] interacts with Drug B [[pmid:222]] per trial [[nct:NCT333]]."
    ])
    assert len(refs) == 3


# ── Cross-reference validation ───────────────────────────────


def test_cross_reference_all_matched():
    """No warnings when all inline refs match sources."""
    findings = [
        "Metformin reduces HbA1c [[pmid:38901234]].",
        "Trial showed improvement [[nct:NCT06123456]].",
    ]
    sources = [
        {"title": "A", "snippet": "a", "pmid": "38901234"},
        {"title": "B", "snippet": "b", "nct_id": "NCT06123456"},
    ]
    warnings = cross_reference_findings(findings, sources)
    assert warnings == []


def test_cross_reference_unmatched_pmid():
    """Warning generated for pmid not in sources."""
    findings = ["Finding [[pmid:99999999]]."]
    sources = [{"title": "A", "snippet": "a", "pmid": "11111111"}]
    warnings = cross_reference_findings(findings, sources)
    assert len(warnings) == 1
    assert "[[pmid:99999999]]" in warnings[0]
    assert "no matching source" in warnings[0]


def test_cross_reference_unmatched_nct():
    """Warning generated for nct not in sources."""
    findings = ["Trial finding [[nct:NCT00000001]]."]
    sources = [{"title": "A", "snippet": "a", "pmid": "12345678"}]
    warnings = cross_reference_findings(findings, sources)
    assert len(warnings) == 1
    assert "[[nct:NCT00000001]]" in warnings[0]


def test_cross_reference_partial_match():
    """Only unmatched refs generate warnings."""
    findings = [
        "Matched [[pmid:111]].",
        "Unmatched [[pmid:999]].",
    ]
    sources = [{"title": "A", "snippet": "a", "pmid": "111"}]
    warnings = cross_reference_findings(findings, sources)
    assert len(warnings) == 1
    assert "[[pmid:999]]" in warnings[0]


def test_cross_reference_empty_findings():
    """No warnings for empty findings list."""
    warnings = cross_reference_findings([], [{"title": "A", "snippet": "a"}])
    assert warnings == []


def test_cross_reference_no_inline_refs():
    """No warnings when findings have no inline refs."""
    warnings = cross_reference_findings(
        ["Plain finding."],
        [{"title": "A", "snippet": "a", "pmid": "111"}],
    )
    assert warnings == []


def test_cross_reference_url_type():
    """URL refs are cross-referenced against source url field."""
    findings = ["See report [[url:https://fda.gov/safety/123]]."]
    sources = [
        {"title": "A", "snippet": "a", "url": "https://fda.gov/safety/123"},
    ]
    warnings = cross_reference_findings(findings, sources)
    assert warnings == []


def test_cross_reference_doi_type():
    """DOI refs are cross-referenced against source doi field."""
    findings = ["Study showed [[doi:10.1038/abc]]."]
    sources = [{"title": "A", "snippet": "a", "doi": "10.1038/abc"}]
    warnings = cross_reference_findings(findings, sources)
    assert warnings == []


# ── Cross-reference in tool execution ────────────────────────


async def test_tool_cross_reference_no_findings(tool, ctx):
    """cross_reference_warnings is empty list when findings not provided."""
    result = await tool._run_async_impl(
        {
            "query": "test",
            "sources": [{"title": "A", "snippet": "a", "pmid": "111"}],
        },
        ctx,
    )
    assert result["cross_reference_warnings"] == []


async def test_tool_cross_reference_matched(tool, ctx):
    """cross_reference_warnings is empty when all refs match."""
    result = await tool._run_async_impl(
        {
            "query": "test",
            "sources": [{"title": "A", "snippet": "a", "pmid": "38901234"}],
            "findings": ["Finding with ref [[pmid:38901234]]."],
        },
        ctx,
    )
    assert result["cross_reference_warnings"] == []


async def test_tool_cross_reference_unmatched(tool, ctx):
    """cross_reference_warnings lists unmatched refs."""
    result = await tool._run_async_impl(
        {
            "query": "test",
            "sources": [{"title": "A", "snippet": "a", "pmid": "111"}],
            "findings": [
                "Good ref [[pmid:111]].",
                "Bad ref [[pmid:999]].",
            ],
        },
        ctx,
    )
    assert len(result["cross_reference_warnings"]) == 1
    assert "[[pmid:999]]" in result["cross_reference_warnings"][0]


# ── Redis raw sources storage ────────────────────────────────


async def test_raw_sources_stored_in_redis(tool, ctx):
    """publish_sources stores raw sources array in Redis for graph_writer."""
    mock_redis_instance = MagicMock()

    # Ensure the state path falls through to ctx.session.id
    ctx.state = None

    sources = [
        {"title": "Study A", "snippet": "Finding.", "pmid": "12345678", "publication_year": "2024"},
        {"title": "Trial B", "snippet": "Result.", "nct_id": "NCT01234567", "publication_year": "2023"},
    ]

    with patch("redis.from_url", return_value=mock_redis_instance):
        result = await tool._run_async_impl(
            {"query": "cancer treatment", "sources": sources},
            ctx,
        )

    assert result["status"] == "published"

    # Collect all r.set() calls
    set_calls = mock_redis_instance.set.call_args_list
    assert len(set_calls) == 2, f"Expected 2 Redis set calls, got {len(set_calls)}"

    # First call: citation_map_json
    cm_key = set_calls[0][0][0]
    assert cm_key == "medexpert:test-session-001:evidence:citation_map_json"

    # Second call: published_sources_raw
    raw_key = set_calls[1][0][0]
    assert raw_key == "medexpert:test-session-001:evidence:published_sources_raw"

    raw_value = json.loads(set_calls[1][0][1].decode("utf-8"))
    assert len(raw_value) == 2
    assert raw_value[0]["pmid"] == "12345678"
    assert raw_value[0]["publication_year"] == "2024"
    assert raw_value[1]["nct_id"] == "NCT01234567"

    # Both use same TTL
    assert set_calls[0][1]["ex"] == 3600
    assert set_calls[1][1]["ex"] == 3600
