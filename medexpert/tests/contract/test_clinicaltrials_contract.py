"""Contract tests for ClinicalTrials.gov MCP server.

These tests replay recorded API responses to verify parsing logic
against real response shapes. Skip when cassettes are missing.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mcp_servers.clinicaltrials import server as ct_module

search_trials = ct_module.search_trials.fn
get_trial_details = ct_module.get_trial_details.fn

pytestmark = [pytest.mark.contract, pytest.mark.cassette_server("clinicaltrials")]


async def test_search_trials_diabetes_contract(respx_cassette):
    """Verify parsing of real ClinicalTrials.gov search response."""
    result = await search_trials(query="type 2 diabetes", max_results=5)
    assert isinstance(result, dict)
    assert "trials" in result
    if result["trials"]:
        trial = result["trials"][0]
        assert "nct_id" in trial or "nctId" in trial


async def test_get_trial_details_contract(respx_cassette):
    """Verify parsing of a real trial detail response."""
    result = await get_trial_details(nct_id="NCT04000165")
    assert isinstance(result, dict)
    # Should have parsed trial details or an error
    assert "nct_id" in result or "nctId" in result or "error" in result
