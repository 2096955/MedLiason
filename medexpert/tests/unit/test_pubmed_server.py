"""Tests for the PubMed MCP server.

Covers search_pubmed, get_article_abstract, and get_article_metadata
with mocked HTTP responses. Tests XML parsing, error handling, input
sanitization, and NCBI API key handling.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.pubmed import server as pubmed_module
from mcp_servers.pubmed.server import (
    _parse_id_list,
    _parse_abstract,
    _parse_esummary,
)

# FastMCP 3.x: @mcp.tool() returns the original function directly.
# Import tool functions directly from the server module.
search_pubmed = pubmed_module.search_pubmed
get_article_abstract = pubmed_module.get_article_abstract
get_article_metadata = pubmed_module.get_article_metadata


# ---------------------------------------------------------------------------
# Realistic XML fixtures
# ---------------------------------------------------------------------------

ESEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSearchResult>
    <Count>1542</Count>
    <RetMax>3</RetMax>
    <RetStart>0</RetStart>
    <IdList>
        <Id>38901234</Id>
        <Id>38876543</Id>
        <Id>38854321</Id>
    </IdList>
</eSearchResult>"""

ESEARCH_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSearchResult>
    <Count>0</Count>
    <RetMax>0</RetMax>
    <RetStart>0</RetStart>
    <IdList/>
</eSearchResult>"""

EFETCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
    <PubmedArticle>
        <MedlineCitation>
            <PMID Version="1">38901234</PMID>
            <Article>
                <ArticleTitle>Efficacy of CAR-T therapy in relapsed B-cell lymphoma</ArticleTitle>
                <Abstract>
                    <AbstractText Label="BACKGROUND">CAR-T cell therapy has transformed treatment of B-cell malignancies.</AbstractText>
                    <AbstractText Label="METHODS">Multicenter randomized trial of 450 patients.</AbstractText>
                    <AbstractText Label="RESULTS">Complete response rate was 68% at 12 months.</AbstractText>
                    <AbstractText Label="CONCLUSIONS">CAR-T therapy demonstrates durable responses.</AbstractText>
                </Abstract>
                <AuthorList>
                    <Author>
                        <LastName>Chen</LastName>
                        <ForeName>Wei</ForeName>
                    </Author>
                    <Author>
                        <LastName>Patel</LastName>
                        <ForeName>Raj</ForeName>
                    </Author>
                </AuthorList>
                <Journal>
                    <Title>New England Journal of Medicine</Title>
                </Journal>
            </Article>
        </MedlineCitation>
        <PubmedData>
            <History>
                <PubMedPubDate PubStatus="pubmed">
                    <Year>2024</Year>
                    <Month>Jun</Month>
                </PubMedPubDate>
            </History>
        </PubmedData>
    </PubmedArticle>
</PubmedArticleSet>"""

EFETCH_NO_ARTICLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet/>"""

ESUMMARY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSummaryResult>
    <DocSum>
        <Id>38901234</Id>
        <Item Name="Title" Type="String">Efficacy of CAR-T therapy in relapsed B-cell lymphoma</Item>
        <Item Name="Source" Type="String">N Engl J Med</Item>
        <Item Name="PubDate" Type="String">2024 Jun</Item>
        <Item Name="Volume" Type="String">390</Item>
        <Item Name="Issue" Type="String">24</Item>
        <Item Name="Pages" Type="String">2251-2263</Item>
        <Item Name="DOI" Type="String">10.1056/NEJMoa2401234</Item>
        <Item Name="FullJournalName" Type="String">The New England journal of medicine</Item>
        <Item Name="AuthorList" Type="List">
            <Item Name="Author" Type="String">Chen W</Item>
            <Item Name="Author" Type="String">Patel R</Item>
        </Item>
        <Item Name="PubTypeList" Type="List">
            <Item Name="PubType" Type="String">Journal Article</Item>
            <Item Name="PubType" Type="String">Randomized Controlled Trial</Item>
        </Item>
    </DocSum>
</eSummaryResult>"""

ESUMMARY_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSummaryResult/>"""


# ---------------------------------------------------------------------------
# Helper to create a mock httpx.Response
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, text: str = "", json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# XML parsing unit tests
# ---------------------------------------------------------------------------

class TestParseIdList:
    def test_parses_multiple_ids(self):
        ids = _parse_id_list(ESEARCH_XML)
        assert ids == ["38901234", "38876543", "38854321"]

    def test_parses_empty_id_list(self):
        ids = _parse_id_list(ESEARCH_EMPTY_XML)
        assert ids == []


class TestParseAbstract:
    def test_parses_full_article(self):
        result = _parse_abstract(EFETCH_XML)
        assert result["pmid"] == "38901234"
        assert "CAR-T therapy" in result["title"]
        assert "BACKGROUND:" in result["abstract"]
        assert "CONCLUSIONS:" in result["abstract"]
        assert "Chen Wei" in result["authors"]
        assert "Patel Raj" in result["authors"]
        assert result["journal"] == "New England Journal of Medicine"

    def test_handles_no_article(self):
        result = _parse_abstract(EFETCH_NO_ARTICLE_XML)
        assert "error" in result


class TestParseEsummary:
    def test_parses_summary_fields(self):
        result = _parse_esummary(ESUMMARY_XML)
        assert result["pmid"] == "38901234"
        assert result["title"] == "Efficacy of CAR-T therapy in relapsed B-cell lymphoma"
        assert result["source"] == "N Engl J Med"
        assert result["volume"] == "390"
        assert result["doi"] == "10.1056/NEJMoa2401234"
        assert "Chen W" in result["authors"]
        assert "Randomized Controlled Trial" in result["pub_types"]

    def test_handles_empty_summary(self):
        result = _parse_esummary(ESUMMARY_EMPTY_XML)
        assert "error" in result


# ---------------------------------------------------------------------------
# search_pubmed tests
# ---------------------------------------------------------------------------

class TestSearchPubmed:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        mock_resp = _mock_response(200, text=ESEARCH_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_pubmed("CAR-T lymphoma", max_results=3)
        assert result["total_count"] == 1542
        assert result["returned_count"] == 3
        assert result["pmids"] == ["38901234", "38876543", "38854321"]
        assert result["query"] == "CAR-T lymphoma"

    @pytest.mark.asyncio
    async def test_empty_query_after_sanitization(self):
        result = await search_pubmed("", max_results=10)
        assert result["error"] == "Empty query after sanitization"
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_api_error_returns_error_dict(self):
        mock_resp = _mock_response(500)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_pubmed("metformin diabetes")
        assert "error" in result
        assert "500" in result["error"]
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_query_is_sanitized(self):
        mock_resp = _mock_response(200, text=ESEARCH_EMPTY_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_pubmed("aspirin; DROP TABLE studies", max_results=5)
            call_params = mock_get.call_args
            assert "DROP" not in call_params.kwargs.get("params", call_params[1].get("params", {})).get("term", "")

    @pytest.mark.asyncio
    async def test_max_results_capped_at_100(self):
        mock_resp = _mock_response(200, text=ESEARCH_EMPTY_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await search_pubmed("oncology", max_results=500)
            call_args = mock_get.call_args
            params = call_args.kwargs.get("params") or call_args[1].get("params", {})
            assert params["retmax"] == "100"

    @pytest.mark.asyncio
    async def test_no_results_returns_empty_list(self):
        mock_resp = _mock_response(200, text=ESEARCH_EMPTY_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await search_pubmed("zzzznonexistentdrugxyz")
        assert result["total_count"] == 0
        assert result["pmids"] == []


# ---------------------------------------------------------------------------
# get_article_abstract tests
# ---------------------------------------------------------------------------

class TestGetArticleAbstract:
    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        mock_resp = _mock_response(200, text=EFETCH_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_article_abstract("38901234")
        assert result["pmid"] == "38901234"
        assert "CAR-T" in result["title"]
        assert len(result["authors"]) == 2

    @pytest.mark.asyncio
    async def test_invalid_pmid_non_numeric(self):
        result = await get_article_abstract("abc123")
        assert "error" in result
        assert "Invalid PMID" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_pmid_empty(self):
        result = await get_article_abstract("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(404)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_article_abstract("99999999")
        assert "error" in result
        assert "404" in result["error"]

    @pytest.mark.asyncio
    async def test_article_not_found_in_response(self):
        mock_resp = _mock_response(200, text=EFETCH_NO_ARTICLE_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_article_abstract("38901234")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_pmid_whitespace_stripped(self):
        mock_resp = _mock_response(200, text=EFETCH_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await get_article_abstract("  38901234  ")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert params["id"] == "38901234"


# ---------------------------------------------------------------------------
# get_article_metadata tests
# ---------------------------------------------------------------------------

class TestGetArticleMetadata:
    @pytest.mark.asyncio
    async def test_successful_metadata_fetch(self):
        mock_resp = _mock_response(200, text=ESUMMARY_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_article_metadata("38901234")
        assert result["pmid"] == "38901234"
        assert result["doi"] == "10.1056/NEJMoa2401234"
        assert "Chen W" in result["authors"]

    @pytest.mark.asyncio
    async def test_invalid_pmid(self):
        result = await get_article_metadata("not-a-number")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_api_error(self):
        mock_resp = _mock_response(503)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_article_metadata("12345678")
        assert "error" in result
        assert "503" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_summary_response(self):
        mock_resp = _mock_response(200, text=ESUMMARY_EMPTY_XML)
        with patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await get_article_metadata("99999999")
        assert "error" in result


# ---------------------------------------------------------------------------
# API key handling tests
# ---------------------------------------------------------------------------

class TestApiKeyHandling:
    @pytest.mark.asyncio
    async def test_api_key_included_when_set(self):
        mock_resp = _mock_response(200, text=ESEARCH_EMPTY_XML)
        with (
            patch("mcp_servers.pubmed.server.NCBI_API_KEY", "test_key_123"),
            patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get,
        ):
            await search_pubmed("test query")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert params.get("api_key") == "test_key_123"

    @pytest.mark.asyncio
    async def test_api_key_omitted_when_not_set(self):
        mock_resp = _mock_response(200, text=ESEARCH_EMPTY_XML)
        with (
            patch("mcp_servers.pubmed.server.NCBI_API_KEY", None),
            patch("mcp_servers.pubmed.server.resilient_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get,
        ):
            await search_pubmed("test query")
            params = mock_get.call_args.kwargs.get("params") or mock_get.call_args[1].get("params", {})
            assert "api_key" not in params
