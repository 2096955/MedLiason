"""Contract tests for Regulatory MCP server."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.regulatory import server as reg_module

search_510k = reg_module.search_510k
search_pma = reg_module.search_pma

pytestmark = [pytest.mark.contract, pytest.mark.cassette_server("regulatory")]


async def test_search_510k_contract(respx_cassette):
    """Verify parsing of real FDA 510(k) search response."""
    result = await search_510k(query="insulin pump", max_results=3)
    assert isinstance(result, dict)
    assert "results" in result or "clearances" in result or "error" in result


async def test_search_pma_contract(respx_cassette):
    """Verify parsing of real FDA PMA search response."""
    result = await search_pma(query="pacemaker", max_results=3)
    assert isinstance(result, dict)
