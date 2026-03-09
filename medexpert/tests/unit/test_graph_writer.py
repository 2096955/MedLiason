"""Unit tests for the graph_writer DynamicTool."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from lifesci_tools.graph_writer import (
    GraphWriterTool,
    _build_cross_references,
    _build_entity_studies,
    _build_specialist_entities,
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


class TestReadSessionDataSourcePriority:
    """Tests that _read_session_data prefers published_sources_raw over citation_map_json."""

    @pytest.mark.asyncio
    async def test_prefers_published_sources_raw(self):
        """published_sources_raw (with pmid/nct_id) is used when available."""
        from lifesci_tools.graph_writer import _read_session_data

        raw_sources = json.dumps([
            {"pmid": "12345678", "nct_id": "", "doi": "", "title": "Study A", "publication_year": "2024"},
        ])
        citation_map = json.dumps([
            {"id": "s0r0", "title": "Study A", "snippet": "Finding.", "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/"},
        ])

        async def mock_get(key):
            if key.endswith(":evidence:published_sources_raw"):
                return raw_sources
            if key.endswith(":evidence:citation_map_json"):
                return citation_map
            return None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_client.scan_iter = MagicMock(return_value=AsyncIteratorMock([]))
        mock_client.aclose = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_client):
            data = await _read_session_data("test-session-123")

        sources = data["sources"]
        assert len(sources) == 1
        assert sources[0]["pmid"] == "12345678"
        assert sources[0]["publication_year"] == "2024"

    @pytest.mark.asyncio
    async def test_falls_back_to_citation_map_when_no_raw(self):
        """Falls back to citation_map_json when published_sources_raw is absent."""
        from lifesci_tools.graph_writer import _read_session_data

        citation_map = json.dumps([
            {"id": "s0r0", "title": "Study A", "snippet": "Finding.", "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/"},
        ])

        async def mock_get(key):
            if key.endswith(":evidence:citation_map_json"):
                return citation_map
            return None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_client.scan_iter = MagicMock(return_value=AsyncIteratorMock([]))
        mock_client.aclose = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_client):
            data = await _read_session_data("test-session-456")

        sources = data["sources"]
        assert len(sources) == 1
        assert sources[0]["id"] == "s0r0"  # citation_map format


class AsyncIteratorMock:
    """Helper to mock async iterators (scan_iter)."""

    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


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


# ── DAG edge model tests ─────────────────────────────────────


class TestBuildSessionCypherNewEdgeModel:
    """Tests for the new pipeline DAG edge model (FOUND, EVIDENCED_BY, CITED)."""

    def test_found_edges_when_specialist_entities_provided(self):
        """Specialist-FOUND->Entity edges are generated from specialist_entities map."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="breast cancer treatment",
            domain="oncology",
            specialists_used=["LiteratureSpecialist", "DrugSpecialist"],
            studies=[{"pmid": "12345678", "nct_id": "", "doi": "", "title": "Study", "year": "2024"}],
            diseases=["breast cancer"],
            drugs=["tamoxifen"],
            genes=["BRCA1"],
            specialist_entities={
                "LiteratureSpecialist": {"diseases": ["breast cancer"], "drugs": [], "genes": ["BRCA1"]},
                "DrugSpecialist": {"diseases": [], "drugs": ["tamoxifen"], "genes": []},
            },
        )
        # Check that FOUND edges are present
        found_stmts = [(c, p) for c, p in statements if "FOUND" in c]
        assert len(found_stmts) == 3  # breast cancer, tamoxifen, BRCA1

        # Check that ABOUT edges are NOT present (DAG mode)
        about_stmts = [(c, p) for c, p in statements if "ABOUT" in c]
        assert len(about_stmts) == 0

    def test_evidenced_by_edges_from_entity_studies(self):
        """Entity-EVIDENCED_BY->Study edges are generated from entity_studies map."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="oncology",
            specialists_used=["LiteratureSpecialist"],
            studies=[
                {"pmid": "11111111", "nct_id": "", "doi": "", "title": "Study A", "year": "2024"},
                {"pmid": "22222222", "nct_id": "", "doi": "", "title": "Study B", "year": "2024"},
            ],
            diseases=["breast cancer"],
            drugs=[],
            genes=[],
            specialist_entities={
                "LiteratureSpecialist": {"diseases": ["breast cancer"], "drugs": [], "genes": []},
            },
            entity_studies={
                "breast cancer": ["11111111", "22222222"],
            },
        )
        ev_stmts = [(c, p) for c, p in statements if "EVIDENCED_BY" in c]
        # 1 entity × 2 studies × 3 labels (Disease, Drug, Gene) = 6 statements
        # But only Disease label will match at runtime — all 6 are emitted
        assert len(ev_stmts) == 6

    def test_cited_cross_ref_edges(self):
        """Study-CITED->Study edges are generated from cross_references."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="",
            specialists_used=[],
            studies=[
                {"pmid": "11111111", "nct_id": "", "doi": "", "title": "A", "year": "2024"},
                {"pmid": "22222222", "nct_id": "", "doi": "", "title": "B", "year": "2024"},
            ],
            diseases=[],
            drugs=[],
            genes=[],
            cross_references=[("11111111", "22222222")],
        )
        cited_stmts = [(c, p) for c, p in statements if "[:CITED]" in c]
        assert len(cited_stmts) == 1
        cypher, params = cited_stmts[0]
        assert "s1 <> s2" in cypher  # prevent self-loops
        assert params["src_id"] == "11111111"
        assert params["tgt_id"] == "22222222"

    def test_partial_flag_on_study_nodes(self):
        """Studies with partial=True should have st.partial = true in Cypher."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="",
            specialists_used=[],
            studies=[
                {"pmid": "11111111", "nct_id": "", "doi": "", "title": "Complete", "year": "2024"},
                {"pmid": "22222222", "nct_id": "", "doi": "", "title": "Partial", "year": "2024", "partial": True},
            ],
            diseases=[],
            drugs=[],
            genes=[],
        )
        # Session + 2 studies = 3 statements
        assert len(statements) == 3
        # First study: partial=False
        _, params1 = statements[1]
        assert params1["partial"] is False
        # Second study: partial=True
        _, params2 = statements[2]
        assert params2["partial"] is True

    def test_fallback_about_edges_without_specialist_entities(self):
        """When specialist_entities is None, fallback to legacy Session-ABOUT->Entity."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="oncology",
            specialists_used=["LiteratureSpecialist"],
            studies=[],
            diseases=["breast cancer"],
            drugs=["tamoxifen"],
            genes=["BRCA1"],
            specialist_entities=None,  # explicitly None — fallback mode
        )
        about_stmts = [(c, p) for c, p in statements if "ABOUT" in c]
        assert len(about_stmts) == 3  # disease + drug + gene

        found_stmts = [(c, p) for c, p in statements if "FOUND" in c]
        assert len(found_stmts) == 0  # no FOUND edges in fallback mode

    def test_no_session_cited_study_edges(self):
        """Studies should NOT have Session-CITED->Study edges (old star model)."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="",
            specialists_used=[],
            studies=[{"pmid": "12345678", "nct_id": "", "doi": "", "title": "T", "year": "2024"}],
            diseases=[],
            drugs=[],
            genes=[],
        )
        for cypher, params in statements:
            # No statement should link Session to Study via CITED
            if "CITED" in cypher:
                assert "Session" not in cypher, "Session-CITED->Study edges should not exist"

    def test_nct_and_doi_study_ids_in_evidenced_by(self):
        """entity_studies correctly handles NCT IDs and DOIs."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="",
            specialists_used=["LiteratureSpecialist"],
            studies=[
                {"pmid": "", "nct_id": "NCT01234567", "doi": "", "title": "Trial", "year": "2024"},
                {"pmid": "", "nct_id": "", "doi": "10.1234/test", "title": "Paper", "year": "2024"},
            ],
            diseases=["diabetes"],
            drugs=[],
            genes=[],
            specialist_entities={
                "LiteratureSpecialist": {"diseases": ["diabetes"], "drugs": [], "genes": []},
            },
            entity_studies={
                "diabetes": ["NCT01234567", "10.1234/test"],
            },
        )
        ev_stmts = [(c, p) for c, p in statements if "EVIDENCED_BY" in c]
        # 1 entity × 2 studies × 3 labels = 6
        assert len(ev_stmts) == 6

        # Check that at least one uses nct_id and one uses doi
        nct_ev = [p for c, p in ev_stmts if "nct_id" in c]
        doi_ev = [p for c, p in ev_stmts if "doi" in c]
        assert len(nct_ev) >= 1
        assert len(doi_ev) >= 1

    def test_mixed_cross_references(self):
        """Cross-references between PMID and NCT study IDs."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="",
            specialists_used=[],
            studies=[
                {"pmid": "11111111", "nct_id": "", "doi": "", "title": "A", "year": ""},
                {"pmid": "", "nct_id": "NCT01234567", "doi": "", "title": "B", "year": ""},
            ],
            diseases=[],
            drugs=[],
            genes=[],
            cross_references=[("11111111", "NCT01234567")],
        )
        cited_stmts = [(c, p) for c, p in statements if "[:CITED]" in c]
        assert len(cited_stmts) == 1
        cypher, params = cited_stmts[0]
        assert "pmid" in cypher  # source uses pmid
        assert "nct_id" in cypher  # target uses nct_id

    def test_empty_specialist_entities_treated_as_no_dag(self):
        """An empty dict {} should behave like None (fallback to ABOUT)."""
        statements = build_session_cypher(
            session_id="s1",
            query_text="test",
            domain="",
            specialists_used=[],
            studies=[],
            diseases=["test disease"],
            drugs=[],
            genes=[],
            specialist_entities={},  # empty dict
        )
        about_stmts = [(c, p) for c, p in statements if "ABOUT" in c]
        assert len(about_stmts) == 1  # fallback ABOUT edge


# ── DAG attribution map builder tests ────────────────────────


class TestBuildSpecialistEntities:
    """Tests for the _build_specialist_entities helper."""

    def test_attributes_disease_to_specialist_by_source(self):
        sources = [
            {"agent_name": "LiteratureSpecialist", "title": "Breast cancer review", "snippet": ""},
        ]
        result = _build_specialist_entities(
            sources, ["LiteratureSpecialist"], ["breast cancer"], [], [],
        )
        assert "LiteratureSpecialist" in result
        assert "breast cancer" in result["LiteratureSpecialist"]["diseases"]

    def test_attributes_drug_to_drug_specialist(self):
        sources = [
            {"agent_name": "DrugSpecialist", "title": "Tamoxifen efficacy study", "snippet": ""},
        ]
        result = _build_specialist_entities(
            sources, ["DrugSpecialist"], [], ["Tamoxifen"], [],
        )
        assert "Tamoxifen" in result["DrugSpecialist"]["drugs"]

    def test_unattributed_entities_distributed_to_all_specialists(self):
        """Entities not found in any source text go to ALL specialists."""
        sources = [
            {"agent_name": "LiteratureSpecialist", "title": "Some other topic", "snippet": ""},
        ]
        result = _build_specialist_entities(
            sources, ["LiteratureSpecialist", "DrugSpecialist"],
            ["rare disease"], [], [],
        )
        # "rare disease" appears in no source — distributed to both
        assert "rare disease" in result["LiteratureSpecialist"]["diseases"]
        assert "rare disease" in result["DrugSpecialist"]["diseases"]

    def test_empty_sources_distributes_all(self):
        result = _build_specialist_entities(
            [], ["SpecA", "SpecB"], ["disease X"], ["drug Y"], ["GENE1"],
        )
        for spec in ["SpecA", "SpecB"]:
            assert "disease X" in result[spec]["diseases"]
            assert "drug Y" in result[spec]["drugs"]
            assert "GENE1" in result[spec]["genes"]

    def test_prunes_empty_specialists(self):
        """Specialists with no entities at all are pruned from the map."""
        sources = [
            {"agent_name": "LitSpec", "title": "Diabetes overview", "snippet": ""},
        ]
        result = _build_specialist_entities(
            sources, ["LitSpec", "EmptySpec"], ["diabetes"], [], [],
        )
        # LitSpec gets diabetes via attribution.
        # EmptySpec gets diabetes via fallback? No — diabetes IS attributed to LitSpec,
        # so it's not unattributed. EmptySpec has nothing → pruned.
        assert "LitSpec" in result
        # EmptySpec should be pruned (no entities)
        assert "EmptySpec" not in result

    def test_gene_case_sensitive_match(self):
        """Gene matching is case-sensitive (uppercase in original text)."""
        sources = [
            {"agent_name": "GenomicsSpecialist", "title": "BRCA1 mutations", "snippet": ""},
        ]
        result = _build_specialist_entities(
            sources, ["GenomicsSpecialist"], [], [], ["BRCA1"],
        )
        assert "BRCA1" in result["GenomicsSpecialist"]["genes"]


class TestBuildEntityStudies:
    """Tests for the _build_entity_studies helper."""

    def test_links_disease_to_study_by_title(self):
        sources = [
            {"pmid": "12345678", "title": "Breast cancer treatment outcomes", "snippet": ""},
        ]
        result = _build_entity_studies(sources, ["breast cancer"], [], [])
        assert "breast cancer" in result
        assert "12345678" in result["breast cancer"]

    def test_links_drug_to_study_by_snippet(self):
        sources = [
            {"pmid": "87654321", "title": "Study A", "snippet": "Tamoxifen showed efficacy"},
        ]
        result = _build_entity_studies(sources, [], ["Tamoxifen"], [])
        assert "Tamoxifen" in result
        assert "87654321" in result["Tamoxifen"]

    def test_links_gene_case_sensitive(self):
        sources = [
            {"pmid": "11111111", "title": "BRCA1 and cancer risk", "snippet": ""},
        ]
        result = _build_entity_studies(sources, [], [], ["BRCA1"])
        assert "BRCA1" in result
        assert "11111111" in result["BRCA1"]

    def test_no_links_when_entity_not_in_text(self):
        sources = [
            {"pmid": "12345678", "title": "Unrelated study", "snippet": ""},
        ]
        result = _build_entity_studies(sources, ["breast cancer"], [], [])
        assert "breast cancer" not in result

    def test_deduplicates_study_ids(self):
        sources = [
            {"pmid": "12345678", "title": "Diabetes study", "snippet": "diabetes management"},
        ]
        result = _build_entity_studies(sources, ["diabetes"], [], [])
        assert result["diabetes"].count("12345678") == 1

    def test_handles_nct_and_doi_ids(self):
        sources = [
            {"nct_id": "NCT01234567", "title": "Diabetes trial", "snippet": ""},
            {"doi": "10.1234/test", "title": "Diabetes review", "snippet": ""},
        ]
        result = _build_entity_studies(sources, ["diabetes"], [], [])
        assert "NCT01234567" in result.get("diabetes", [])
        assert "10.1234/test" in result.get("diabetes", [])

    def test_skips_sources_without_primary_id(self):
        sources = [{"title": "No ID source about diabetes", "snippet": ""}]
        result = _build_entity_studies(sources, ["diabetes"], [], [])
        assert len(result) == 0


class TestBuildCrossReferences:
    """Tests for the _build_cross_references helper."""

    def test_finds_co_cited_pmids_in_paragraph(self):
        text = "Studies [[pmid:11111111]] and [[pmid:22222222]] both showed efficacy."
        result = _build_cross_references(text)
        assert len(result) == 1
        assert ("11111111", "22222222") in result or ("22222222", "11111111") in result

    def test_no_cross_refs_for_single_pmid(self):
        text = "Only [[pmid:11111111]] was relevant."
        result = _build_cross_references(text)
        assert len(result) == 0

    def test_multiple_paragraphs_separate(self):
        text = (
            "Paragraph 1: [[pmid:11111111]] and [[pmid:22222222]]\n\n"
            "Paragraph 2: [[pmid:33333333]] and [[pmid:44444444]]"
        )
        result = _build_cross_references(text)
        assert len(result) == 2

    def test_deduplicates_pairs(self):
        text = (
            "[[pmid:11111111]] and [[pmid:22222222]] agree. "
            "Also [[pmid:11111111]] and [[pmid:22222222]] confirmed."
        )
        result = _build_cross_references(text)
        assert len(result) == 1

    def test_empty_text(self):
        assert _build_cross_references("") == []
        assert _build_cross_references("no pmids here") == []

    def test_three_pmids_in_one_paragraph(self):
        text = "[[pmid:11111111]], [[pmid:22222222]], and [[pmid:33333333]] all agree."
        result = _build_cross_references(text)
        # 3 choose 2 = 3 pairs
        assert len(result) == 3
