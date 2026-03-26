"""Tests for kg_search DynamicTool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_tool_context():
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.id = "test-session-001"
    ctx.state = {}
    return ctx


def _make_node(labels, name, description="", **extra_props):
    """Helper to create a KG node dict matching query_knowledge_graph output."""
    return {
        "id": f"elem-{name}",
        "labels": labels,
        "name": name,
        "description": description,
        "properties": {"name": name, "description": description, **extra_props},
    }


class TestKgSearchStudyNode:
    @pytest.mark.asyncio
    async def test_study_node_pmid_url(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(
            ["Study"], "Study 38901234", pmid="38901234", title="RCT of Drug X"
        )
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "Drug X"}, mock_tool_context)
        assert result["status"] == "found"
        assert result["num_sources"] == 1
        assert result["valid_citation_ids"] == ["kg0r0"]
        src = result["rag_metadata"]["sources"][0]
        assert src["citationId"] == "kg0r0"
        assert "38901234" in src["sourceUrl"]
        assert src["metadata"]["source"] == "knowledge_graph"

    @pytest.mark.asyncio
    async def test_study_node_nct_url(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "NCT06123456", nct_id="NCT06123456")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "trial"}, mock_tool_context)
        src = result["rag_metadata"]["sources"][0]
        assert "NCT06123456" in src["sourceUrl"]

    @pytest.mark.asyncio
    async def test_study_node_doi_url(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "doi-study", doi="10.1234/test.2024")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "study"}, mock_tool_context)
        src = result["rag_metadata"]["sources"][0]
        assert "10.1234/test.2024" in src["sourceUrl"]

    @pytest.mark.asyncio
    async def test_study_evidence_grade_from_properties(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "graded", evidence_grade="High")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)
        assert (
            result["rag_metadata"]["sources"][0]["metadata"]["evidence_grade"] == "High"
        )

    @pytest.mark.asyncio
    async def test_study_evidence_grade_default(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "ungraded")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)
        assert (
            result["rag_metadata"]["sources"][0]["metadata"]["evidence_grade"]
            == "Moderate"
        )


class TestKgSearchEntityNodes:
    @pytest.mark.asyncio
    async def test_disease_node(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Disease"], "breast cancer", "A common malignancy")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl(
                {"query": "breast cancer"}, mock_tool_context
            )
        src = result["rag_metadata"]["sources"][0]
        assert src["citationId"] == "kg0r0"
        assert src["sourceUrl"] is None
        assert "breast cancer" in src["contentPreview"]
        assert src["metadata"]["labels"] == ["Disease"]

    @pytest.mark.asyncio
    async def test_drug_node(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Drug"], "bevacizumab", "Anti-VEGF monoclonal antibody")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl(
                {"query": "bevacizumab"}, mock_tool_context
            )
        src = result["rag_metadata"]["sources"][0]
        assert src["metadata"]["labels"] == ["Drug"]
        assert src["metadata"]["evidence_grade"] == ""

    @pytest.mark.asyncio
    async def test_gene_node(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Gene"], "BRCA1", "Tumor suppressor gene")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "BRCA1"}, mock_tool_context)
        src = result["rag_metadata"]["sources"][0]
        assert src["metadata"]["labels"] == ["Gene"]


class TestKgSearchCitationIds:
    @pytest.mark.asyncio
    async def test_multiple_nodes_sequential_ids(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        nodes = [
            _make_node(["Disease"], "cancer"),
            _make_node(["Drug"], "aspirin"),
            _make_node(["Gene"], "TP53"),
        ]
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": nodes,
                "total_results": 3,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)
        assert result["valid_citation_ids"] == ["kg0r0", "kg0r1", "kg0r2"]
        assert result["num_sources"] == 3

    @pytest.mark.asyncio
    async def test_search_type_is_kb_search(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Disease"], "test")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)
        assert result["rag_metadata"]["searchType"] == "kb_search"


class TestKgSearchGracefulDegradation:
    @pytest.mark.asyncio
    async def test_memgraph_unavailable(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": False,
                "error": "Memgraph connection required",
                "error_category": "service_unavailable",
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)
        assert result["status"] == "kg_unavailable"
        assert result["rag_metadata"] is None
        assert result["num_sources"] == 0

    @pytest.mark.asyncio
    async def test_empty_results(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [],
                "total_results": 0,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl(
                {"query": "nonexistent"}, mock_tool_context
            )
        assert result["status"] == "no_kg_results"
        assert result["rag_metadata"] is None

    @pytest.mark.asyncio
    async def test_invalid_query(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        tool = KgSearchTool()
        result = await tool._run_async_impl({"query": ""}, mock_tool_context)
        assert result["status"] == "invalid_query"

    @pytest.mark.asyncio
    async def test_exception_does_not_raise(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = RuntimeError("unexpected")
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)
        assert result["status"] == "kg_unavailable"
        assert result["rag_metadata"] is None


class TestKgSearchSessionGraph:
    @pytest.mark.asyncio
    async def test_session_graph_mode(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        session_result = {
            "success": True,
            "nodes": [
                _make_node(["Disease"], "cancer"),
                _make_node(["Study"], "study1", pmid="12345"),
                _make_node(["Session"], "sess-1"),
                _make_node(["Specialist"], "LitSpec"),
            ],
            "edges": [],
            "total_nodes": 4,
        }
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = session_result
            tool = KgSearchTool()
            result = await tool._run_async_impl(
                {"query": "test", "session_id": "sess-1"}, mock_tool_context
            )
        assert result["num_sources"] == 2
        labels = [s["metadata"]["labels"] for s in result["rag_metadata"]["sources"]]
        assert ["Disease"] in labels
        assert ["Study"] in labels
        assert ["Session"] not in labels
        assert ["Specialist"] not in labels


class TestKgSearchFormattedResults:
    @pytest.mark.asyncio
    async def test_formatted_results_contain_cite_markers(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Drug"], "aspirin", "NSAID pain reliever")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "aspirin"}, mock_tool_context)
        assert "[[cite:kg0r0]]" in result["formatted_results"]
        assert "aspirin" in result["formatted_results"]


class TestKgSearchEntityTypes:
    """entity_types parameter should be forwarded to query_knowledge_graph."""

    @pytest.mark.asyncio
    async def test_entity_types_forwarded(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [],
                "total_results": 0,
            }
            tool = KgSearchTool()
            await tool._run_async_impl(
                {"query": "test", "entity_types": ["Disease", "Drug"]},
                mock_tool_context,
            )
        mock_call.assert_called_once_with(
            "query_knowledge_graph",
            {"query": "test", "entity_types": ["Disease", "Drug"], "limit": 10},
        )

    @pytest.mark.asyncio
    async def test_entity_types_none_by_default(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [],
                "total_results": 0,
            }
            tool = KgSearchTool()
            await tool._run_async_impl({"query": "test"}, mock_tool_context)
        mock_call.assert_called_once_with(
            "query_knowledge_graph",
            {"query": "test", "entity_types": None, "limit": 10},
        )


class TestKgSearchLimitClamping:
    """limit parameter should be clamped to [1, 50]."""

    @pytest.mark.asyncio
    async def test_limit_zero_clamped_to_1(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Disease"], "test")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl(
                {"query": "test", "limit": 0}, mock_tool_context
            )
        assert result["num_sources"] == 1  # limit clamped to 1, 1 node returned

    @pytest.mark.asyncio
    async def test_limit_100_clamped_to_50(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [],
                "total_results": 0,
            }
            tool = KgSearchTool()
            await tool._run_async_impl(
                {"query": "test", "limit": 100}, mock_tool_context
            )
        # Verify limit=50 was passed
        call_args = mock_call.call_args
        assert call_args[0][1]["limit"] == 50

    @pytest.mark.asyncio
    async def test_limit_string_fallback_to_10(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [],
                "total_results": 0,
            }
            tool = KgSearchTool()
            await tool._run_async_impl(
                {"query": "test", "limit": "abc"}, mock_tool_context
            )
        call_args = mock_call.call_args
        assert call_args[0][1]["limit"] == 10

    @pytest.mark.asyncio
    async def test_limit_none_defaults_to_10(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [],
                "total_results": 0,
            }
            tool = KgSearchTool()
            await tool._run_async_impl(
                {"query": "test", "limit": None}, mock_tool_context
            )
        call_args = mock_call.call_args
        assert call_args[0][1]["limit"] == 10


class TestKgSearchNoIdentifiers:
    """Study node with no pmid/nct_id/doi should get empty URL."""

    @pytest.mark.asyncio
    async def test_study_no_identifiers(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "orphan-study")
        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [node],
                "total_results": 1,
            }
            tool = KgSearchTool()
            result = await tool._run_async_impl(
                {"query": "test"}, mock_tool_context
            )

        src = result["rag_metadata"]["sources"][0]
        assert src["sourceUrl"] is None  # No identifiers -> no URL


class TestKgSearchQuerySanitization:
    """Query should be sanitized if _security module is available."""

    @pytest.mark.asyncio
    async def test_sanitized_query_used(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch(
            "lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = {
                "success": True,
                "results": [],
                "total_results": 0,
            }
            tool = KgSearchTool()
            # Normal query should pass through
            await tool._run_async_impl(
                {"query": "breast cancer"}, mock_tool_context
            )
        assert mock_call.called
