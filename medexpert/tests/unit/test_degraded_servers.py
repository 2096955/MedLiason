"""Tests for Honest Degradation MCP Servers.

Combined tests for knowledge_graph, societies, and vigibase servers.
Verifies structured error responses, fallback search queries, and
that NO medical data is ever fabricated.
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

# FastMCP @mcp.tool() wraps functions in FunctionTool objects.
# Access the underlying async function via the .fn attribute.

# Knowledge Graph
from mcp_servers.knowledge_graph import server as kg_module

_query_knowledge_graph = kg_module.query_knowledge_graph.fn
_get_entity_relationships = kg_module.get_entity_relationships.fn

# Societies
from mcp_servers.societies import server as soc_module

_search_society_guidelines = soc_module.search_society_guidelines.fn
_get_guideline_recommendations = soc_module.get_guideline_recommendations.fn

# VigiBase
from mcp_servers.vigibase import server as vigi_module

_search_adverse_reactions = vigi_module.search_adverse_reactions.fn
_get_signal_data = vigi_module.get_signal_data.fn


def _mock_response(status_code: int, data=None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if data is not None:
        resp.json.return_value = data
    resp.text = json.dumps(data) if data else ""
    return resp


# ===========================================================================
# Knowledge Graph Tests
# ===========================================================================


class TestKnowledgeGraphDegradation:
    """Test that KG returns structured errors when Memgraph is unavailable."""

    @pytest.mark.asyncio
    async def test_no_connection_returns_structured_error(self):
        """Without MEMGRAPH_URL, should return structured error."""
        with patch(
            "mcp_servers.knowledge_graph.server._get_driver",
            return_value=None,
        ):
            result = await _query_knowledge_graph("BRCA1 breast cancer")

        assert result["success"] is False
        assert result["results"] == []
        assert result["error_category"] == "service_unavailable"
        assert "BRCA1" in result["fallback_search_query"]

    @pytest.mark.asyncio
    async def test_never_fabricates_data(self):
        """CRITICAL: Even with a query, results must be empty when no connection."""
        with patch(
            "mcp_servers.knowledge_graph.server._get_driver",
            return_value=None,
        ):
            result = await _query_knowledge_graph(
                "metformin diabetes treatment pathways",
                entity_types=["Drug", "Disease"],
            )

        assert result["results"] == []
        # The fallback should include entity type hints
        assert "Drug" in result["fallback_search_query"] or "Disease" in result["fallback_search_query"]

    @pytest.mark.asyncio
    async def test_entity_relationships_degradation(self):
        with patch(
            "mcp_servers.knowledge_graph.server._get_driver",
            return_value=None,
        ):
            result = await _get_entity_relationships("BRCA1")

        assert result["success"] is False
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        result = await _query_knowledge_graph("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fallback_includes_entity_types(self):
        """Entity types should be included in the fallback search query."""
        with patch(
            "mcp_servers.knowledge_graph.server._get_driver",
            return_value=None,
        ):
            result = await _query_knowledge_graph(
                "TP53", entity_types=["Gene", "Protein"]
            )

        fallback = result["fallback_search_query"]
        assert "TP53" in fallback
        assert "Gene" in fallback or "Protein" in fallback


# ===========================================================================
# Societies Tests
# ===========================================================================


class TestSocietiesDegradation:
    """Test that unconfigured societies return not_implemented responses."""

    @pytest.mark.asyncio
    async def test_unconfigured_society_returns_not_implemented(self):
        """When no env var is set, should return not_implemented."""
        with patch.dict(os.environ, {}, clear=True):
            result = await _search_society_guidelines(
                "asco", "breast cancer screening"
            )

        assert result["status"] == "not_implemented"
        assert "No scraper configured" in result["reason"]
        assert "site:asco.org" in result["fallback_search_query"]

    @pytest.mark.asyncio
    async def test_unknown_society_returns_fallback(self):
        """A society not in SOCIETY_CONFIGS should still degrade gracefully."""
        with patch.dict(os.environ, {}, clear=True):
            result = await _search_society_guidelines(
                "unknown_society", "treatment guidelines"
            )

        assert result["status"] == "not_implemented"
        assert isinstance(result["fallback_search_query"], str)
        assert isinstance(result["available_societies"], list)

    @pytest.mark.asyncio
    async def test_configured_society_queries_api(self):
        """When SOCIETY_ASCO_API_URL is set, should actually query the endpoint."""
        mock_data = [
            {"title": "Breast Cancer Screening Guideline", "year": 2024}
        ]
        resp = _mock_response(200, mock_data)

        with patch.dict(
            os.environ, {"SOCIETY_ASCO_API_URL": "http://asco-scraper:8080"}, clear=True
        ):
            with patch(
                "mcp_servers.societies.server.resilient_get",
                new_callable=AsyncMock,
                return_value=resp,
            ) as mock_get:
                result = await _search_society_guidelines(
                    "asco", "breast cancer screening"
                )

        assert result["total_results"] == 1
        assert result["results"][0]["title"] == "Breast Cancer Screening Guideline"
        # Verify it called the configured URL
        call_url = mock_get.call_args[0][0]
        assert "asco-scraper" in call_url

    @pytest.mark.asyncio
    async def test_recommendations_unconfigured(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await _get_guideline_recommendations(
                "who", "hypertension management"
            )

        assert result["status"] == "not_implemented"
        assert "site:who.int" in result["fallback_search_query"]

    @pytest.mark.asyncio
    async def test_available_societies_reflects_env(self):
        """Available societies list should only contain configured ones."""
        with patch.dict(
            os.environ,
            {"SOCIETY_AHA_API_URL": "http://aha-scraper:8080"},
            clear=True,
        ):
            result = await _search_society_guidelines(
                "asco", "test"
            )

        assert result["status"] == "not_implemented"
        assert "aha" in result["available_societies"]
        assert "asco" not in result["available_societies"]

    @pytest.mark.asyncio
    async def test_never_fabricates_api_urls(self):
        """CRITICAL: Result should NEVER contain fabricated URLs."""
        with patch.dict(os.environ, {}, clear=True):
            result = await _search_society_guidelines(
                "nccn", "lung cancer staging"
            )

        # The result should not contain any http URLs in the reason
        assert "http" not in result["reason"].lower()
        # Fallback should use site: prefix, not a made-up API URL
        assert "site:" in result["fallback_search_query"]


# ===========================================================================
# VigiBase Tests
# ===========================================================================


class TestVigibaseDegradation:
    """Test that VigiBase always returns browser_automation_required."""

    @pytest.mark.asyncio
    async def test_search_returns_browser_required(self):
        result = await _search_adverse_reactions("metformin")

        assert result["status"] == "browser_automation_required"
        assert "WHO" in result["reason"]
        assert result["drug_name"] == "metformin"
        assert "metformin" in result["fallback_search_query"]
        assert "vigibase" in result["fallback_search_query"]
        assert "OpenFDA FAERS" in str(result["alternative_sources"])

    @pytest.mark.asyncio
    async def test_signal_data_returns_browser_required(self):
        result = await _get_signal_data("ibuprofen")

        assert result["status"] == "browser_automation_required"
        assert result["drug_name"] == "ibuprofen"
        assert result["tool"] == "get_signal_data"

    @pytest.mark.asyncio
    async def test_alternative_sources_are_actionable(self):
        """Alternative sources should point to real, accessible databases."""
        result = await _search_adverse_reactions("aspirin")

        alternatives = result["alternative_sources"]
        assert len(alternatives) >= 2
        # All alternatives should be real databases
        known_alternatives = {
            "OpenFDA FAERS (FDA Adverse Event Reporting System)",
            "PubMed case reports",
            "EMA EudraVigilance",
            "MedWatch Safety Reports",
        }
        for alt in alternatives:
            assert alt in known_alternatives, f"Unknown alternative source: {alt}"

    @pytest.mark.asyncio
    async def test_fallback_query_format(self):
        """Fallback search query should target WHO VigiBase site."""
        result = await _search_adverse_reactions("semaglutide")

        fallback = result["fallback_search_query"]
        assert "semaglutide" in fallback
        assert "site:who.int" in fallback

    @pytest.mark.asyncio
    async def test_injection_in_drug_name_sanitized(self):
        """Drug name should be sanitized even though it is returned in response."""
        result = await _search_adverse_reactions("aspirin'; DROP TABLE users; --")

        # sanitize_query removes "; DROP" pattern and quotes
        assert "DROP TABLE" not in result["drug_name"]
        # Semicolons (the injection vector) are stripped via quote removal
        assert "'" not in result["drug_name"]
