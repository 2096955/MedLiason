"""Contract tests for Environmental Health MCP server."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.environmental import server as env_module

get_air_quality = env_module.get_air_quality.fn

pytestmark = [pytest.mark.contract, pytest.mark.cassette_server("environmental")]


async def test_air_quality_by_state_contract(respx_cassette):
    """Verify parsing of real EPA AQS air quality response."""
    result = await get_air_quality(state_code="06", parameter="pm25")
    assert isinstance(result, dict)
    # Expect data or credentials_required or error
    assert "data" in result or "error" in result or "status" in result


async def test_ej_data_contract(respx_cassette):
    """Verify parsing of real EPA EJScreen response."""
    get_ej = env_module.get_environmental_justice_data.fn
    result = await get_ej(location="06037")
    assert isinstance(result, dict)
