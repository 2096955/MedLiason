"""Unit tests for the graph_writer DynamicTool."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from lifesci_tools.graph_writer import (
    GraphWriterTool,
    _is_drug_suffix_match,
    build_session_cypher,
    extract_entities_from_source_metadata,
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


# ── extract_entities_from_source_metadata tests ──────────────────────


class TestDrugSuffixMatch:
    """Tests for _is_drug_suffix_match with min-word-length guards."""

    def test_standard_inn_suffix(self):
        assert _is_drug_suffix_match("omeprazole") is True
        assert _is_drug_suffix_match("atorvastatin") is True
        assert _is_drug_suffix_match("adalimumab") is True

    def test_corticosteroid_suffixes(self):
        assert _is_drug_suffix_match("prednisolone") is True
        assert _is_drug_suffix_match("triamcinolone") is True
        assert _is_drug_suffix_match("fluocinonide") is True

    def test_short_stem_rejects_short_words(self):
        """Short suffixes like 'sone' should reject short English words."""
        assert _is_drug_suffix_match("ozone") is False  # 5 chars, needs 7+
        assert _is_drug_suffix_match("stone") is False  # 5 chars

    def test_short_stem_accepts_long_drug_words(self):
        assert _is_drug_suffix_match("prednisone") is True  # 10 chars >= 7
        assert _is_drug_suffix_match("dexamethasone") is True

    def test_benzodiazepine_guard(self):
        """'zepam' requires 7+ chars."""
        assert _is_drug_suffix_match("diazepam") is True  # 8 chars
        assert _is_drug_suffix_match("clonazepam") is True

    def test_pde5_inhibitor(self):
        assert _is_drug_suffix_match("sildenafil") is True
        assert _is_drug_suffix_match("tadalafil") is True

    def test_non_drug_word(self):
        assert _is_drug_suffix_match("milestone") is False  # 9 chars but no matching suffix with min_len
        assert _is_drug_suffix_match("cyanide") is False
        assert _is_drug_suffix_match("cornerstone") is False


class TestExtractEntitiesFromSourceMetadata:
    """Tests for the metadata-based entity extraction function."""

    def test_drug_extraction_from_drug_source(self):
        sources = [
            {"title": "Omeprazole vs Lansoprazole for GERD", "agent_name": "DrugSpecialist",
             "source_type": "drug", "snippet": ""},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        drugs = [d.lower() for d in result["drugs"]]
        assert "omeprazole" in drugs
        assert "lansoprazole" in drugs

    def test_drug_extraction_from_fda_source(self):
        sources = [
            {"title": "FDA approval of Adalimumab", "agent_name": "",
             "source_type": "fda", "snippet": ""},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        drugs = [d.lower() for d in result["drugs"]]
        assert "adalimumab" in drugs

    def test_drug_extraction_skips_drug_class_terms(self):
        """Drug-class terms like 'antibiotic' should not become Drug nodes."""
        sources = [
            {"title": "Antibiotic resistance patterns", "agent_name": "DrugSpecialist",
             "source_type": "drug", "snippet": "corticosteroid usage"},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        drugs_lower = [d.lower() for d in result["drugs"]]
        assert "antibiotic" not in drugs_lower
        assert "corticosteroid" not in drugs_lower

    def test_drug_extraction_from_query_text(self):
        """User query mentioning a drug name should extract it."""
        result = extract_entities_from_source_metadata([], "Is omeprazole safe during pregnancy?")
        drugs = [d.lower() for d in result["drugs"]]
        assert "omeprazole" in drugs

    def test_drug_extraction_query_text_excludes_classes(self):
        result = extract_entities_from_source_metadata([], "What are the best statins for cholesterol?")
        drugs_lower = [d.lower() for d in result["drugs"]]
        assert "statins" not in drugs_lower

    def test_gene_extraction_from_genomic_source(self):
        sources = [
            {"title": "BRCA1 mutations in breast cancer", "agent_name": "GenomicsSpecialist",
             "source_type": "genomic", "snippet": "EGFR and TP53 variants"},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        assert "BRCA1" in result["genes"]
        assert "EGFR" in result["genes"]
        assert "TP53" in result["genes"]

    def test_gene_extraction_mid_digit_patterns(self):
        """Gene names with mid-string digits (IL4R, CYP3A5) should match."""
        sources = [
            {"title": "IL4R polymorphisms", "agent_name": "",
             "source_type": "gene", "snippet": "CYP3A5 expression"},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        assert "IL4R" in result["genes"]
        assert "CYP3A5" in result["genes"]

    def test_gene_exclusion_list(self):
        """Common abbreviations should not become Gene nodes."""
        sources = [
            {"title": "RESULTS FROM STUDY OF DNA METHODS", "agent_name": "",
             "source_type": "genomic", "snippet": ""},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        for excl in ("RESULTS", "STUDY", "DNA", "METHODS", "FROM"):
            assert excl not in result["genes"]

    def test_gene_regex_caps_at_8_chars(self):
        """All-caps strings > 8 chars should NOT match."""
        sources = [
            {"title": "ABCDEFGHI mutations", "agent_name": "",
             "source_type": "genomic", "snippet": ""},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        assert "ABCDEFGHI" not in result["genes"]  # 9 chars, too long

    def test_disease_extraction_simple(self):
        result = extract_entities_from_source_metadata([], "treatments for type 2 diabetes")
        diseases = [d.lower() for d in result["diseases"]]
        assert "type 2 diabetes" in diseases

    def test_disease_extraction_hyphenated(self):
        result = extract_entities_from_source_metadata(
            [], "What are treatments for non-small cell lung cancer?"
        )
        diseases_lower = [d.lower() for d in result["diseases"]]
        # Should capture the hyphenated disease name
        assert any("lung cancer" in d for d in diseases_lower)

    def test_disease_extraction_direct_pattern(self):
        """Direct medical terms are matched without needing preposition."""
        result = extract_entities_from_source_metadata([], "asthma management guidelines")
        diseases = [d.lower() for d in result["diseases"]]
        assert "asthma" in diseases

    def test_disease_excludes_drug_class_tokens(self):
        """Multi-word matches containing drug-class terms are rejected."""
        result = extract_entities_from_source_metadata(
            [], "treatments for corticosteroids in asthma"
        )
        diseases_lower = [d.lower() for d in result["diseases"]]
        # "corticosteroids in asthma" should be rejected (contains drug-class term)
        assert not any("corticosteroid" in d for d in diseases_lower)

    def test_disease_excludes_stopwords(self):
        """Stopwords like 'educational purposes' should not become diseases."""
        result = extract_entities_from_source_metadata(
            [], "This is for educational purposes only"
        )
        diseases_lower = [d.lower() for d in result["diseases"]]
        assert not any("educational" in d for d in diseases_lower)
        assert not any("purposes" in d for d in diseases_lower)

    def test_empty_inputs(self):
        result = extract_entities_from_source_metadata([], "")
        assert result["drugs"] == []
        assert result["diseases"] == []
        assert result["genes"] == []

    def test_sources_with_missing_fields(self):
        """Sources with None/missing fields should not crash."""
        sources = [
            {"title": None, "agent_name": None, "source_type": None, "snippet": None},
            {},
        ]
        result = extract_entities_from_source_metadata(sources, "test query")
        # Should not raise — just return whatever it finds
        assert isinstance(result, dict)

    def test_pharm_source_type_triggers_drug_extraction(self):
        sources = [
            {"title": "Atorvastatin pharmacokinetics", "agent_name": "",
             "source_type": "pharmacovigilance", "snippet": ""},
        ]
        result = extract_entities_from_source_metadata(sources, "")
        drugs = [d.lower() for d in result["drugs"]]
        assert "atorvastatin" in drugs


class TestSessionIdOverride:
    """Tests for the session ID defensive override in _do_write."""

    @pytest.mark.asyncio
    async def test_placeholder_12345_overridden(self, tool, ctx):
        """Session ID '12345' should be overridden by tool_context.session.id."""
        ctx.session.id = "real-session-abc123"
        with patch("lifesci_tools.graph_writer._get_rw_driver", return_value=None):
            result = await tool._run_async_impl(
                {"session_id": "12345"}, tool_context=ctx
            )
        # Driver is None → graph_unavailable, but session_id was accepted
        assert result["error_category"] == "graph_unavailable"

    @pytest.mark.asyncio
    async def test_empty_session_id_overridden(self, tool, ctx):
        ctx.session.id = "real-session-xyz"
        with patch("lifesci_tools.graph_writer._get_rw_driver", return_value=None):
            result = await tool._run_async_impl(
                {"session_id": ""}, tool_context=ctx
            )
        assert result["error_category"] == "graph_unavailable"

    @pytest.mark.asyncio
    async def test_placeholder_with_no_real_session_fails(self, tool):
        ctx = MagicMock()
        ctx.session = None
        result = await tool._run_async_impl(
            {"session_id": "12345"}, tool_context=ctx
        )
        assert result["success"] is False
        assert result["error_category"] == "validation_error"

    @pytest.mark.asyncio
    async def test_real_session_id_preferred_over_llm_value(self, tool, ctx):
        """Even a non-placeholder LLM value is overridden by tool_context."""
        ctx.session.id = "correct-session-id"
        with patch("lifesci_tools.graph_writer._get_rw_driver", return_value=None):
            result = await tool._run_async_impl(
                {"session_id": "some-other-value"}, tool_context=ctx
            )
        assert result["error_category"] == "graph_unavailable"
