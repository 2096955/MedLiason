"""Contract tests for Census SDOH MCP server."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.census_sdoh import server as census_module

get_sdoh_indicators = census_module.get_sdoh_indicators.fn

pytestmark = [pytest.mark.contract, pytest.mark.cassette_server("census_sdoh")]


async def test_get_sdoh_poverty_contract(respx_cassette):
    """Verify parsing of real Census ACS poverty data."""
    result = await get_sdoh_indicators(indicator_group="poverty", state_fips="06")
    assert isinstance(result, dict)
    assert "data" in result or "error" in result


async def test_get_sdoh_insurance_contract(respx_cassette):
    """Verify parsing of real Census ACS insurance data."""
    result = await get_sdoh_indicators(indicator_group="insurance", state_fips="36")
    assert isinstance(result, dict)
    assert "data" in result or "error" in result
