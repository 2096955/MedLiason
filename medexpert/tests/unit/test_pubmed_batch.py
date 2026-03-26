"""Tests for the PubMed batch fetch MCP tool (get_articles_batch).

Covers multi-PMID retrieval, clamping to 20, empty input handling,
graceful degradation on NCBI failure, and partial results when some
PMIDs are missing from the response.
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.pubmed import server as pubmed_module
from mcp_servers.pubmed.server import _parse_batch_xml

# Unwrap the FastMCP FunctionTool wrapper to get the underlying async callable
get_articles_batch = pubmed_module.get_articles_batch.fn


# ---------------------------------------------------------------------------
# Realistic multi-article XML fixture
# ---------------------------------------------------------------------------

EFETCH_BATCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
    <PubmedArticle>
        <MedlineCitation>
            <PMID Version="1">38901234</PMID>
            <Article>
                <ArticleTitle>Efficacy of CAR-T therapy in relapsed B-cell lymphoma</ArticleTitle>
                <Abstract>
                    <AbstractText Label="BACKGROUND">CAR-T cell therapy has transformed treatment.</AbstractText>
                    <AbstractText Label="RESULTS">Complete response rate was 68%.</AbstractText>
                </Abstract>
                <AuthorList>
                    <Author>
                        <LastName>Chen</LastName>
                        <ForeName>Wei</ForeName>
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
    <PubmedArticle>
        <MedlineCitation>
            <PMID Version="1">38876543</PMID>
            <Article>
                <ArticleTitle>SGLT2 Inhibitors and Renal Outcomes in Diabetes</ArticleTitle>
                <Abstract>
                    <AbstractText>SGLT2 inhibitors reduced renal events by 30%.</AbstractText>
                </Abstract>
                <AuthorList>
                    <Author>
                        <LastName>Patel</LastName>
                        <ForeName>Raj</ForeName>
                    </Author>
                </AuthorList>
                <Journal>
                    <Title>The Lancet</Title>
                </Journal>
            </Article>
        </MedlineCitation>
        <PubmedData>
            <History>
                <PubMedPubDate PubStatus="pubmed">
                    <Year>2024</Year>
                    <Month>May</Month>
                </PubMedPubDate>
            </History>
        </PubmedData>
    </PubmedArticle>
    <PubmedArticle>
        <MedlineCitation>
            <PMID Version="1">38854321</PMID>
            <Article>
                <ArticleTitle>Immune Checkpoint Therapy in Melanoma</ArticleTitle>
                <Abstract>
                    <AbstractText Label="METHODS">Phase III trial of 800 patients.</AbstractText>
                    <AbstractText Label="CONCLUSIONS">5-year survival improved to 52%.</AbstractText>
                </Abstract>
                <AuthorList>
                    <Author>
                        <LastName>Garcia</LastName>
                        <ForeName>Maria</ForeName>
                    </Author>
                </AuthorList>
                <Journal>
                    <Title>Journal of Clinical Oncology</Title>
                </Journal>
            </Article>
        </MedlineCitation>
        <PubmedData>
            <History>
                <PubMedPubDate PubStatus="pubmed">
                    <Year>2024</Year>
                    <Month>Apr</Month>
                </PubMedPubDate>
            </History>
        </PubmedData>
    </PubmedArticle>
</PubmedArticleSet>"""

# Only first article present (simulates missing PMIDs)
EFETCH_PARTIAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
    <PubmedArticle>
        <MedlineCitation>
            <PMID Version="1">38901234</PMID>
            <Article>
                <ArticleTitle>Efficacy of CAR-T therapy in relapsed B-cell lymphoma</ArticleTitle>
                <Abstract>
                    <AbstractText>CAR-T cell therapy has transformed treatment.</AbstractText>
                </Abstract>
                <AuthorList>
                    <Author>
                        <LastName>Chen</LastName>
                        <ForeName>Wei</ForeName>
                    </Author>
                </AuthorList>
                <Journal>
                    <Title>New England Journal of Medicine</Title>
                </Journal>
            </Article>
        </MedlineCitation>
    </PubmedArticle>
</PubmedArticleSet>"""

# Single-article response
EFETCH_SINGLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
    <PubmedArticle>
        <MedlineCitation>
            <PMID Version="1">12345678</PMID>
            <Article>
                <ArticleTitle>Metformin and Cardiovascular Outcomes</ArticleTitle>
                <Abstract>
                    <AbstractText>Metformin reduces HbA1c by 1.0-1.5%.</AbstractText>
                </Abstract>
                <AuthorList>
                    <Author>
                        <LastName>Smith</LastName>
                        <ForeName>Jane</ForeName>
                    </Author>
                </AuthorList>
                <Journal>
                    <Title>Diabetes Care</Title>
                </Journal>
            </Article>
        </MedlineCitation>
    </PubmedArticle>
</PubmedArticleSet>"""


# ---------------------------------------------------------------------------
# Helper to create a mock httpx.Response
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# get_articles_batch tests
# ---------------------------------------------------------------------------

class TestGetArticlesBatch:

    @pytest.mark.asyncio
    async def test_batch_fetch_multiple_pmids(self):
        """Fetching 3 PMIDs returns 3 articles with correct metadata."""
        mock_resp = _mock_response(200, text=EFETCH_BATCH_XML)
        with patch(
            "mcp_servers.pubmed.server.resilient_get",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await get_articles_batch("38901234,38876543,38854321")

        assert result["success"] is True
        assert result["count"] == 3
        assert len(result["articles"]) == 3
        assert result["missing_pmids"] == []

        # Verify each article was parsed
        pmids = [a["pmid"] for a in result["articles"]]
        assert "38901234" in pmids
        assert "38876543" in pmids
        assert "38854321" in pmids

        # Spot-check first article
        art0 = next(a for a in result["articles"] if a["pmid"] == "38901234")
        assert "CAR-T" in art0["title"]
        assert "Chen Wei" in art0["authors"]

    @pytest.mark.asyncio
    async def test_batch_fetch_single_pmid(self):
        """Batch fetch works correctly with a single PMID."""
        mock_resp = _mock_response(200, text=EFETCH_SINGLE_XML)
        with patch(
            "mcp_servers.pubmed.server.resilient_get",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await get_articles_batch("12345678")

        assert result["success"] is True
        assert result["count"] == 1
        assert len(result["articles"]) == 1
        assert result["articles"][0]["pmid"] == "12345678"
        assert "Metformin" in result["articles"][0]["title"]

    @pytest.mark.asyncio
    async def test_batch_fetch_clamps_to_20(self):
        """Requesting >20 PMIDs clamps to the first 20."""
        # Generate 25 fake PMIDs
        pmids_25 = [str(10000000 + i) for i in range(25)]
        pmids_str = ",".join(pmids_25)

        mock_resp = _mock_response(200, text=EFETCH_SINGLE_XML)
        with patch(
            "mcp_servers.pubmed.server.resilient_get",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ) as mock_get:
            await get_articles_batch(pmids_str)

            # Verify the actual HTTP call used only the first 20 PMIDs
            call_args = mock_get.call_args
            params = call_args.kwargs.get("params") or call_args[1].get("params", {})
            sent_ids = params["id"].split(",")
            assert len(sent_ids) == 20

    @pytest.mark.asyncio
    async def test_batch_fetch_empty_string(self):
        """Empty PMID string returns an error dict."""
        result = await get_articles_batch("")
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_batch_fetch_graceful_degradation(self):
        """NCBI failure returns a structured error response."""
        from mcp_servers._http import RetryExhaustedError
        from fastmcp.exceptions import ToolError

        exc = RetryExhaustedError(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            last_status=503,
            attempts=4,
        )
        with patch(
            "mcp_servers.pubmed.server.resilient_get",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            # RetryExhaustedError with 503 is a fatal category (service_unavailable),
            # so raise_or_return_error raises ToolError
            with pytest.raises(ToolError) as exc_info:
                await get_articles_batch("38901234,38876543")

            error_msg = str(exc_info.value)
            assert "pubmed" in error_msg
            assert "service_unavailable" in error_msg

    @pytest.mark.asyncio
    async def test_batch_fetch_partial_results(self):
        """When some PMIDs are missing from the response, return partial
        results and list the missing PMIDs."""
        mock_resp = _mock_response(200, text=EFETCH_PARTIAL_XML)
        with patch(
            "mcp_servers.pubmed.server.resilient_get",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await get_articles_batch("38901234,99999999,88888888")

        assert result["success"] is True
        assert result["count"] == 1
        assert len(result["articles"]) == 1
        assert result["articles"][0]["pmid"] == "38901234"
        # The missing PMIDs should be reported
        assert "99999999" in result["missing_pmids"]
        assert "88888888" in result["missing_pmids"]


# ---------------------------------------------------------------------------
# _parse_batch_xml unit tests
# ---------------------------------------------------------------------------

class TestParseBatchXml:

    def test_parses_multiple_articles(self):
        articles = _parse_batch_xml(
            EFETCH_BATCH_XML,
            ["38901234", "38876543", "38854321"],
        )
        assert len(articles) == 3
        pmids = {a["pmid"] for a in articles}
        assert pmids == {"38901234", "38876543", "38854321"}

    def test_returns_empty_for_empty_xml(self):
        empty_xml = '<?xml version="1.0"?><PubmedArticleSet/>'
        articles = _parse_batch_xml(empty_xml, ["12345678"])
        assert articles == []

    def test_handles_malformed_xml(self):
        """Malformed XML returns empty list instead of raising."""
        articles = _parse_batch_xml("<not valid xml", ["12345678"])
        assert articles == []
