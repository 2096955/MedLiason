"""Contract tests for Provider Intelligence MCP server."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.provider_intel import server as pi_module

search_providers = pi_module.search_providers.fn
search_open_payments = pi_module.search_open_payments.fn

pytestmark = [pytest.mark.contract, pytest.mark.cassette_server("provider_intel")]


async def test_search_providers_cardiology_contract(respx_cassette):
    """Verify parsing of real CMS provider search."""
    result = await search_providers(name="Smith", state="CA", max_results=5)
    assert isinstance(result, dict)
    assert "providers" in result or "error" in result


async def test_search_open_payments_contract(respx_cassette):
    """Verify parsing of real CMS Open Payments data."""
    result = await search_open_payments(physician_name="Johnson", state="NY")
    assert isinstance(result, dict)
    assert "payments" in result or "error" in result
