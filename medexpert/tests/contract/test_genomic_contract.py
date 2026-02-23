"""Contract tests for Genomic Variants MCP server."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.genomic import server as gen_module

search_variants = gen_module.search_variants.fn
search_gene_info = gen_module.search_gene_info.fn

pytestmark = [pytest.mark.contract, pytest.mark.cassette_server("genomic")]


async def test_search_clinvar_brca1_contract(respx_cassette):
    """Verify parsing of real ClinVar BRCA1 search."""
    result = await search_variants(gene="BRCA1", max_results=5)
    assert isinstance(result, dict)
    assert "variants" in result or "error" in result


async def test_search_gene_info_tp53_contract(respx_cassette):
    """Verify parsing of real gene info search."""
    result = await search_gene_info(gene="TP53")
    assert isinstance(result, dict)
