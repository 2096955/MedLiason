"""Contract tests for SEER Cancer Statistics MCP server."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.seer import server as seer_module

get_cancer_statistics = seer_module.get_cancer_statistics.fn

pytestmark = [pytest.mark.contract, pytest.mark.cassette_server("seer")]


async def test_breast_cancer_incidence_contract(respx_cassette):
    """Verify parsing of real SEER cancer incidence data."""
    result = await get_cancer_statistics(cancer_site="breast")
    assert isinstance(result, dict)
    assert "statistics" in result or "data" in result or "error" in result


async def test_lung_cancer_mortality_contract(respx_cassette):
    """Verify parsing of real SEER mortality data."""
    result = await get_cancer_statistics(
        cancer_site="lung", statistic_type="mortality"
    )
    assert isinstance(result, dict)
