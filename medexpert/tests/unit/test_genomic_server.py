"""Tests for Genomic Variant MCP Server (ClinVar / dbSNP).

Covers search_variants, get_variant_details, and search_gene_info tools
with mocked HTTP responses, XML parsing, error handling, and validation.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import sys
import os

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)
from mcp_servers.genomic import server as genomic_module
from mcp_servers.genomic.server import (
    _parse_clinvar_esummary,
    _parse_esearch_ids,
)

# FastMCP @mcp.tool() wraps functions in FunctionTool objects.
# Access the underlying async function via the .fn attribute.
_search_variants = genomic_module.search_variants.fn
_get_variant_details = genomic_module.get_variant_details.fn
_search_gene_info = genomic_module.search_gene_info.fn


# ---------------------------------------------------------------------------
# Sample XML responses
# ---------------------------------------------------------------------------

ESEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSearchResult>
    <Count>2</Count>
    <RetMax>2</RetMax>
    <IdList>
        <Id>37079</Id>
        <Id>52100</Id>
    </IdList>
</eSearchResult>
"""

ESEARCH_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<eSearchResult>
    <Count>0</Count>
    <RetMax>0</RetMax>
    <IdList/>
</eSearchResult>
"""

ESUMMARY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DocumentSummarySet>
    <DocumentSummary uid="37079">
        <title>NM_007294.4(BRCA1):c.5266dupC (p.Gln1756Profs*74)</title>
        <accession>VCV000037079</accession>
        <clinical_significance>
            <description>Pathogenic</description>
        </clinical_significance>
        <genes>
            <gene>
                <symbol>BRCA1</symbol>
            </gene>
        </genes>
        <trait_set>
            <trait>
                <trait_name>Hereditary breast and ovarian cancer syndrome</trait_name>
            </trait>
        </trait_set>
        <variation_type>single nucleotide variant</variation_type>
    </DocumentSummary>
</DocumentSummarySet>
"""

DBSNP_RESPONSE = [
    {
        "refsnp_id": "rs80357906",
        "primary_snapshot_data": {
            "variant_type": "snv",
            "placement_with_allele": [
                {"seq_id": "NC_000017.11"}
            ],
        },
    },
    {
        "refsnp_id": "rs1799950",
        "primary_snapshot_data": {
            "variant_type": "snv",
            "placement_with_allele": [
                {"seq_id": "NC_000017.11"}
            ],
        },
    },
]


def _mock_response(status_code: int, text: str = "", json_data=None) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# Tests: XML parsers
# ---------------------------------------------------------------------------


class TestXmlParsers:
    def test_parse_esearch_ids_success(self):
        ids = _parse_esearch_ids(ESEARCH_XML)
        assert ids == ["37079", "52100"]

    def test_parse_esearch_ids_empty(self):
        ids = _parse_esearch_ids(ESEARCH_EMPTY_XML)
        assert ids == []

    def test_parse_esearch_ids_malformed_xml(self):
        ids = _parse_esearch_ids("<not>valid</xml>")
        # Should not raise; returns what it can find
        assert isinstance(ids, list)

    def test_parse_clinvar_esummary_success(self):
        variants = _parse_clinvar_esummary(ESUMMARY_XML)
        assert len(variants) == 1
        v = variants[0]
        assert v["variant_id"] == "37079"
        assert v["accession"] == "VCV000037079"
        assert v["gene"] == "BRCA1"
        assert v["clinical_significance"] == "Pathogenic"
        assert "Hereditary breast" in v["condition"]

    def test_parse_clinvar_esummary_empty(self):
        xml = '<?xml version="1.0"?><DocumentSummarySet></DocumentSummarySet>'
        variants = _parse_clinvar_esummary(xml)
        assert variants == []

    def test_parse_clinvar_esummary_malformed(self):
        variants = _parse_clinvar_esummary("not xml at all")
        assert variants == []


# ---------------------------------------------------------------------------
# Tests: search_variants
# ---------------------------------------------------------------------------


class TestSearchVariants:
    @pytest.mark.asyncio
    async def test_success_two_step_search(self):
        search_resp = _mock_response(200, text=ESEARCH_XML)
        summary_resp = _mock_response(200, text=ESUMMARY_XML)

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            side_effect=[search_resp, summary_resp],
        ):
            result = await _search_variants("BRCA1")

        assert result["query"] == "BRCA1"
        assert result["total_results"] == 1
        assert result["results"][0]["gene"] == "BRCA1"

    @pytest.mark.asyncio
    async def test_no_variants_found(self):
        search_resp = _mock_response(200, text=ESEARCH_EMPTY_XML)

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            return_value=search_resp,
        ):
            result = await _search_variants("NONEXISTENT_GENE_XYZ")

        assert result["total_results"] == 0
        assert "No variants found" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_esearch_api_error(self):
        error_resp = _mock_response(500, text="Internal error")

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            return_value=error_resp,
        ):
            result = await _search_variants("BRCA1")

        assert "error" in result
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        result = await _search_variants("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_network_exception_handled(self):
        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            side_effect=Exception("DNS resolution failed"),
        ):
            result = await _search_variants("TP53")

        assert "error" in result
        assert "DNS" in result["error"]


# ---------------------------------------------------------------------------
# Tests: get_variant_details
# ---------------------------------------------------------------------------


class TestGetVariantDetails:
    @pytest.mark.asyncio
    async def test_success(self):
        resp = _mock_response(200, text=ESUMMARY_XML)

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await _get_variant_details("37079")

        assert result["variant_id"] == "37079"
        assert result["details"]["gene"] == "BRCA1"

    @pytest.mark.asyncio
    async def test_non_numeric_id_rejected(self):
        result = await _get_variant_details("abc123")
        assert "error" in result
        assert "numeric" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_variant_not_found(self):
        empty_xml = '<?xml version="1.0"?><DocumentSummarySet></DocumentSummarySet>'
        resp = _mock_response(200, text=empty_xml)

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await _get_variant_details("99999999")

        assert "not found" in result.get("error", "").lower()


# ---------------------------------------------------------------------------
# Tests: search_gene_info
# ---------------------------------------------------------------------------


class TestSearchGeneInfo:
    @pytest.mark.asyncio
    async def test_success_list_response(self):
        resp = _mock_response(200, json_data=DBSNP_RESPONSE)

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await _search_gene_info("BRCA1")

        assert result["gene"] == "BRCA1"
        assert result["total_results"] == 2
        assert result["results"][0]["rsid"] == "rs80357906"

    @pytest.mark.asyncio
    async def test_success_dict_response(self):
        single = {"refsnp_id": "rs80357906", "primary_snapshot_data": {"variant_type": "snv"}}
        resp = _mock_response(200, json_data=single)

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await _search_gene_info("BRCA1")

        assert result["total_results"] == 1

    @pytest.mark.asyncio
    async def test_gene_not_found_404(self):
        resp = _mock_response(404)

        with patch(
            "mcp_servers.genomic.server.resilient_get",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await _search_gene_info("FAKEGENE")

        assert result["total_results"] == 0
        assert "No variants found" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_empty_gene_name_returns_error(self):
        result = await _search_gene_info("")
        assert "error" in result
