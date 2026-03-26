# KG → RAG Citation Adapter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Memgraph knowledge graph results appear as citable RAG sources in the frontend, and bridge KG visualization nodes to the Sources panel.

**Architecture:** New `kg_search` DynamicTool converts KG MCP server results into RAG citations (`kg0rN`). Frontend citation regex/parser extended to support `kg` prefix. KG visualization gets callback props to bridge Study nodes to the Sources panel. SSE transport unchanged — reuses existing `rag_metadata` flow.

**Tech Stack:** Python (DynamicTool, ADK), TypeScript/React (citations, RAGInfoPanel), Memgraph (Cypher via neo4j driver), pytest, vitest

**Design doc:** `docs/plans/2026-03-13-kg-rag-citation-adapter-design.md` (Rev 3, architect-approved 10/10)

---

## Task 1: Backend — `kg_search` DynamicTool (Tests First)

**Files:**
- Create: `medexpert/tests/unit/test_kg_search.py`
- Create: `medexpert/src/lifesci_tools/kg_search.py`

### Step 1: Write failing tests for `kg_search`

Create `medexpert/tests/unit/test_kg_search.py`:

```python
"""Tests for kg_search DynamicTool."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Node-to-RAG conversion tests
# ---------------------------------------------------------------------------

class TestKgSearchStudyNode:
    """Study nodes should produce RAG sources with URLs from pmid/nct_id/doi."""

    @pytest.mark.asyncio
    async def test_study_node_pmid_url(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "Study 38901234", pmid="38901234", title="RCT of Drug X")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
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
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "trial"}, mock_tool_context)

        src = result["rag_metadata"]["sources"][0]
        assert "NCT06123456" in src["sourceUrl"]

    @pytest.mark.asyncio
    async def test_study_node_doi_url(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "doi-study", doi="10.1234/test.2024")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "study"}, mock_tool_context)

        src = result["rag_metadata"]["sources"][0]
        assert "10.1234/test.2024" in src["sourceUrl"]

    @pytest.mark.asyncio
    async def test_study_evidence_grade_from_properties(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "graded", evidence_grade="High")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)

        assert result["rag_metadata"]["sources"][0]["metadata"]["evidence_grade"] == "High"

    @pytest.mark.asyncio
    async def test_study_evidence_grade_default(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Study"], "ungraded")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)

        assert result["rag_metadata"]["sources"][0]["metadata"]["evidence_grade"] == "Moderate"


class TestKgSearchEntityNodes:
    """Disease/Drug/Gene nodes should produce RAG sources without external URLs."""

    @pytest.mark.asyncio
    async def test_disease_node(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Disease"], "breast cancer", "A common malignancy")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "breast cancer"}, mock_tool_context)

        src = result["rag_metadata"]["sources"][0]
        assert src["citationId"] == "kg0r0"
        assert src["sourceUrl"] is None
        assert "breast cancer" in src["contentPreview"]
        assert src["metadata"]["labels"] == ["Disease"]

    @pytest.mark.asyncio
    async def test_drug_node(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Drug"], "bevacizumab", "Anti-VEGF monoclonal antibody")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "bevacizumab"}, mock_tool_context)

        src = result["rag_metadata"]["sources"][0]
        assert src["metadata"]["labels"] == ["Drug"]
        assert src["metadata"]["evidence_grade"] == ""

    @pytest.mark.asyncio
    async def test_gene_node(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Gene"], "BRCA1", "Tumor suppressor gene")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "BRCA1"}, mock_tool_context)

        src = result["rag_metadata"]["sources"][0]
        assert src["metadata"]["labels"] == ["Gene"]


class TestKgSearchCitationIds:
    """Citation IDs follow kg0rN pattern."""

    @pytest.mark.asyncio
    async def test_multiple_nodes_sequential_ids(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        nodes = [
            _make_node(["Disease"], "cancer"),
            _make_node(["Drug"], "aspirin"),
            _make_node(["Gene"], "TP53"),
        ]
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": nodes, "total_results": 3}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)

        assert result["valid_citation_ids"] == ["kg0r0", "kg0r1", "kg0r2"]
        assert result["num_sources"] == 3

    @pytest.mark.asyncio
    async def test_search_type_is_kb_search(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Disease"], "test")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)

        assert result["rag_metadata"]["searchType"] == "kb_search"


class TestKgSearchGracefulDegradation:
    """Tool should never raise — return structured status on failure."""

    @pytest.mark.asyncio
    async def test_memgraph_unavailable(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
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

        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [], "total_results": 0}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "nonexistent"}, mock_tool_context)

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

        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("unexpected")
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "test"}, mock_tool_context)

        assert result["status"] == "kg_unavailable"
        assert result["rag_metadata"] is None


class TestKgSearchSessionGraph:
    """When session_id is provided, queries session graph instead."""

    @pytest.mark.asyncio
    async def test_session_graph_mode(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        session_result = {
            "success": True,
            "nodes": [
                _make_node(["Disease"], "cancer"),
                _make_node(["Study"], "study1", pmid="12345"),
                _make_node(["Session"], "sess-1"),  # Should be filtered out
                _make_node(["Specialist"], "LitSpec"),  # Should be filtered out
            ],
            "edges": [],
            "total_nodes": 4,
        }
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = session_result
            tool = KgSearchTool()
            result = await tool._run_async_impl(
                {"query": "test", "session_id": "sess-1"}, mock_tool_context
            )

        # Session and Specialist nodes should be filtered — only Disease + Study
        assert result["num_sources"] == 2
        labels = [s["metadata"]["labels"] for s in result["rag_metadata"]["sources"]]
        assert ["Disease"] in labels
        assert ["Study"] in labels
        assert ["Session"] not in labels
        assert ["Specialist"] not in labels


class TestKgSearchFormattedResults:
    """Formatted results should contain citation IDs for LLM context."""

    @pytest.mark.asyncio
    async def test_formatted_results_contain_cite_markers(self, mock_tool_context):
        from lifesci_tools.kg_search import KgSearchTool

        node = _make_node(["Drug"], "aspirin", "NSAID pain reliever")
        with patch("lifesci_tools.kg_search._call_kg_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"success": True, "results": [node], "total_results": 1}
            tool = KgSearchTool()
            result = await tool._run_async_impl({"query": "aspirin"}, mock_tool_context)

        assert "[[cite:kg0r0]]" in result["formatted_results"]
        assert "aspirin" in result["formatted_results"]
```

### Step 2: Run tests to verify they fail

Run: `cd medexpert && python -m pytest tests/unit/test_kg_search.py -v --no-header 2>&1 | head -30`
Expected: `ModuleNotFoundError: No module named 'lifesci_tools.kg_search'`

### Step 3: Implement `kg_search.py`

Create `medexpert/src/lifesci_tools/kg_search.py`:

```python
"""KG Search — queries the Knowledge Graph and produces RAG citations.

Wraps the Knowledge Graph MCP server functions (query_knowledge_graph,
get_session_graph) and converts Memgraph node results into RAGSource
objects for the frontend citation pipeline.

Citation ID namespace: kg0rN (distinct from s0rN in source_collector).
searchType: "kb_search" (already defined in frontend fe.ts types).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext
from google.genai import types as adk_types

from solace_agent_mesh.agent.tools.dynamic_tool import DynamicTool
from solace_agent_mesh.common.rag_dto import create_rag_source, create_rag_search_result

logger = logging.getLogger(__name__)

# URL templates (same as source_collector)
_ID_URL_TEMPLATES = {
    "pmid": "https://pubmed.ncbi.nlm.nih.gov/{id}/",
    "nct_id": "https://clinicaltrials.gov/study/{id}",
    "doi": "https://doi.org/{id}",
}

# Node labels to exclude from RAG sources (infrastructure, not content)
_EXCLUDED_LABELS = {"Session", "Specialist"}


async def _call_kg_tool(tool_name: str, arguments: dict) -> dict:
    """Call a KG MCP server tool via Python import (same-process pattern)."""
    try:
        from mcp_servers.knowledge_graph.server import (
            get_session_graph,
            query_knowledge_graph,
        )

        tool_map = {
            "query_knowledge_graph": query_knowledge_graph,
            "get_session_graph": get_session_graph,
        }

        fn = tool_map.get(tool_name)
        if fn is None:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        # Unwrap FastMCP FunctionTool
        if hasattr(fn, "fn"):
            fn = fn.fn
        return await fn(**arguments)

    except ImportError:
        return {
            "success": False,
            "error": "Knowledge graph MCP server not available",
            "error_category": "service_unavailable",
        }
    except Exception as exc:
        logger.error("KG tool call failed: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "error_category": "api_error",
        }


def _build_url_from_props(props: dict) -> str:
    """Construct a URL from node properties (pmid, nct_id, doi)."""
    for id_type, template in _ID_URL_TEMPLATES.items():
        value = props.get(id_type)
        if value:
            return template.format(id=value)
    return ""


def _node_to_rag_source(node: dict, index: int) -> Dict[str, Any]:
    """Convert a KG node dict into a RAG source via create_rag_source."""
    citation_id = f"kg0r{index}"
    labels = node.get("labels", [])
    props = node.get("properties", {})
    name = node.get("name", "")
    description = node.get("description", "")
    is_study = "Study" in labels

    url = _build_url_from_props(props) if is_study else ""
    content_preview = description[:200] if description else name

    # Evidence grade: read from properties for Study, empty for entities
    evidence_grade = ""
    if is_study:
        evidence_grade = props.get("evidence_grade", "Moderate")

    return create_rag_source(
        citation_id=citation_id,
        file_id=f"kg_node_{index}",
        filename=name,
        title=props.get("title", name),
        source_url=url or None,
        url=url or None,
        content_preview=content_preview,
        relevance_score=1.0,
        source_type="kb_search",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        metadata={
            "title": props.get("title", name),
            "link": url,
            "type": "kb_search",
            "labels": labels,
            "source": "knowledge_graph",
            "node_id": node.get("id", ""),
            "evidence_grade": evidence_grade,
            "favicon": (
                f"https://www.google.com/s2/favicons?domain={url}&sz=32"
                if url else ""
            ),
        },
    )


class KgSearchTool(DynamicTool):
    """Queries the Knowledge Graph and returns results as RAG citations."""

    tool_name = "kg_search"
    tool_description = (
        "Search the MedExpert Knowledge Graph (Memgraph) for entities and "
        "relationships from prior research sessions. Returns RAG-formatted "
        "citations with [[cite:kg0rN]] markers you can use in your answer. "
        "Call at STEP 1 (PLAN) to check what is already known before "
        "delegating to specialists."
    )
    parameters_schema = adk_types.Schema(
        type=adk_types.Type.OBJECT,
        properties={
            "query": adk_types.Schema(
                type=adk_types.Type.STRING,
                description="Search query (entity name, disease, drug, gene, etc.).",
            ),
            "entity_types": adk_types.Schema(
                type=adk_types.Type.ARRAY,
                description="Optional filter by node labels: Disease, Drug, Gene, Study.",
                items=adk_types.Schema(type=adk_types.Type.STRING),
            ),
            "session_id": adk_types.Schema(
                type=adk_types.Type.STRING,
                description="Retrieve a specific prior session's graph.",
            ),
            "limit": adk_types.Schema(
                type=adk_types.Type.INTEGER,
                description="Max results (default 10, max 50).",
            ),
        },
        required=["query"],
    )

    async def _run_async_impl(
        self,
        args: dict,
        tool_context: ToolContext,
        credential: Optional[str] = None,
    ) -> Dict[str, Any]:
        log_id = "[kg_search]"
        query = args.get("query", "").strip()

        if not query:
            return {"status": "invalid_query", "rag_metadata": None, "num_sources": 0}

        session_id = args.get("session_id")
        try:
            limit = min(max(int(args.get("limit", 10)), 1), 50)
        except (TypeError, ValueError):
            limit = 10

        try:
            if session_id:
                raw = await _call_kg_tool("get_session_graph", {"session_id": session_id})
                nodes = raw.get("nodes", []) if raw.get("success") else []
            else:
                entity_types = args.get("entity_types")
                raw = await _call_kg_tool(
                    "query_knowledge_graph",
                    {"query": query, "entity_types": entity_types, "limit": limit},
                )
                nodes = raw.get("results", []) if raw.get("success") else []

            if not raw.get("success"):
                logger.warning("%s KG query failed: %s", log_id, raw.get("error", "unknown"))
                return {"status": "kg_unavailable", "rag_metadata": None, "num_sources": 0}

            # Filter out infrastructure nodes (Session, Specialist)
            content_nodes = [
                n for n in nodes
                if not any(lbl in _EXCLUDED_LABELS for lbl in n.get("labels", []))
            ]

            if not content_nodes:
                return {"status": "no_kg_results", "rag_metadata": None, "num_sources": 0}

            # Convert to RAG sources
            rag_sources = []
            valid_ids = []
            for i, node in enumerate(content_nodes[:limit]):
                rag_sources.append(_node_to_rag_source(node, i))
                valid_ids.append(f"kg0r{i}")

            rag_metadata = create_rag_search_result(
                query=query,
                search_type="kb_search",
                timestamp=datetime.now(timezone.utc).isoformat(),
                sources=rag_sources,
            )

            # Build formatted results for LLM
            lines = [f"=== KNOWLEDGE GRAPH RESULTS ({len(rag_sources)} entities) ==="]
            lines.append(f"Query: {query}")
            lines.append(f"Citation IDs: {', '.join(valid_ids)}")
            lines.append("")
            for i, node in enumerate(content_nodes[:limit]):
                cid = f"kg0r{i}"
                lines.append(f"--- KG ENTITY {i + 1} ---")
                lines.append(f"CITATION ID: [[cite:{cid}]]")
                lines.append(f"TYPE: {', '.join(node.get('labels', []))}")
                lines.append(f"NAME: {node.get('name', 'N/A')}")
                desc = node.get("description", "")
                if desc:
                    lines.append(f"DESCRIPTION: {desc[:300]}")
                url = _build_url_from_props(node.get("properties", {}))
                if url:
                    lines.append(f"URL: {url}")
                lines.append("")
            lines.append("=== END KNOWLEDGE GRAPH RESULTS ===")

            logger.info(
                "%s Found %d KG entities for query: %s",
                log_id, len(rag_sources), query[:80],
            )

            return {
                "status": "found",
                "formatted_results": "\n".join(lines),
                "rag_metadata": rag_metadata,
                "valid_citation_ids": valid_ids,
                "num_sources": len(rag_sources),
            }

        except Exception as exc:
            logger.error("%s Unexpected error: %s", log_id, exc)
            return {"status": "kg_unavailable", "rag_metadata": None, "num_sources": 0}
```

### Step 4: Run tests to verify they pass

Run: `cd medexpert && python -m pytest tests/unit/test_kg_search.py -v --no-header`
Expected: All 16 tests PASS

### Step 5: Commit

```bash
git add medexpert/src/lifesci_tools/kg_search.py medexpert/tests/unit/test_kg_search.py
git commit --signoff -m "feat(kg_search): add DynamicTool for KG → RAG citation adapter"
```

---

## Task 2: Protocol Validator + Error Recovery Hints

**Files:**
- Modify: `medexpert/src/lifesci_tools/protocol_step_validator.py:44`
- Modify: `medexpert/src/lifesci_tools/error_recovery_hints.py:56-59`
- Modify: `medexpert/tests/unit/test_protocol_step_validator.py`

### Step 1: Write failing tests

Add to `medexpert/tests/unit/test_protocol_step_validator.py`:

```python
class TestKgSearchProtocolStep:
    """kg_search should be allowed at step 1 (PLAN) only."""

    def test_kg_search_allowed_at_step_1(self):
        from lifesci_tools.protocol_step_validator import _is_tool_allowed
        assert _is_tool_allowed("kg_search", 1) is True

    def test_kg_search_rejected_at_step_0(self):
        from lifesci_tools.protocol_step_validator import _is_tool_allowed
        assert _is_tool_allowed("kg_search", 0) is False

    def test_kg_search_rejected_at_step_2(self):
        from lifesci_tools.protocol_step_validator import _is_tool_allowed
        assert _is_tool_allowed("kg_search", 2) is False

    def test_kg_search_rejected_at_step_3_through_6(self):
        from lifesci_tools.protocol_step_validator import _is_tool_allowed
        for step in (3, 4, 5, 6):
            assert _is_tool_allowed("kg_search", step) is False, f"Failed at step {step}"
```

### Step 2: Run tests to verify they fail

Run: `cd medexpert && python -m pytest tests/unit/test_protocol_step_validator.py::TestKgSearchProtocolStep -v --no-header`
Expected: FAIL (kg_search not in step 1 allowed set)

### Step 3: Add `kg_search` to step 1

In `medexpert/src/lifesci_tools/protocol_step_validator.py` line 44, add `"kg_search"` to the step 1 set:

```python
# Before:
1: {"query_decomposer", "memory_plane", "session_state", "web_request"},
# After:
1: {"query_decomposer", "memory_plane", "session_state", "web_request", "kg_search"},
```

### Step 4: Add error recovery hints

In `medexpert/src/lifesci_tools/error_recovery_hints.py`, add before the wildcard hints section (before line 60):

```python
    ("knowledge_graph", "service_unavailable"): (
        "Knowledge graph (Memgraph) is not running. "
        "KG search results will be skipped — research continues with live sources."
    ),
    ("knowledge_graph", "circuit_open"): (
        "Knowledge graph circuit breaker open. "
        "KG search skipped — research continues with live sources."
    ),
```

### Step 5: Write tests for error recovery hints

Add to the test file (or create `medexpert/tests/unit/test_error_recovery_hints.py` if no existing tests):

```python
class TestKnowledgeGraphRecoveryHints:
    def test_service_unavailable_hint(self):
        from lifesci_tools.error_recovery_hints import get_recovery_hint
        hint = get_recovery_hint("knowledge_graph", "service_unavailable")
        assert hint is not None
        assert "Memgraph" in hint

    def test_circuit_open_hint(self):
        from lifesci_tools.error_recovery_hints import get_recovery_hint
        hint = get_recovery_hint("knowledge_graph", "circuit_open")
        assert hint is not None
        assert "circuit breaker" in hint
```

### Step 6: Run tests to verify they pass

Run: `cd medexpert && python -m pytest tests/unit/test_protocol_step_validator.py::TestKgSearchProtocolStep -v --no-header`
Expected: All 4 protocol tests PASS + 2 recovery hint tests PASS

### Step 7: Commit

```bash
git add medexpert/src/lifesci_tools/protocol_step_validator.py medexpert/src/lifesci_tools/error_recovery_hints.py medexpert/tests/unit/test_protocol_step_validator.py medexpert/tests/unit/test_error_recovery_hints.py
git commit --signoff -m "feat(protocol): add kg_search to step 1 + knowledge_graph recovery hints"
```

---

## Task 3: Orchestrator YAML Config (All 3 Variants)

**Files:**
- Modify: `medexpert/configs/agents/orchestrator.yaml`
- Modify: `medexpert/configs/pro/agents/orchestrator.yaml`
- Modify: `medexpert/configs/opus/agents/orchestrator.yaml`

### Step 1: Add `kg_search` tool to orchestrator tools list

In each orchestrator YAML, find the `dynamic_tools` list under `apps[0].app_config.agent_config.dynamic_tools` and add:

```yaml
- module_name: lifesci_tools.kg_search
  class_name: KgSearchTool
```

### Step 2: Update STEP 1 prompt

In each orchestrator YAML, find the `**STEP 1 — PLAN**` section and add after the existing text:

```
        Before calling `query_decomposer`, call `kg_search` with the user's
        question to check for prior knowledge in the graph. If results found,
        include the KG findings in your planning context — they may reduce
        the number of specialists needed.
```

### Step 3: Run existing tests to verify no regression

Run: `cd medexpert && python -m pytest tests/unit/test_protocol_step_validator.py tests/unit/test_kg_search.py -v --no-header`
Expected: All tests PASS

### Step 4: Commit

```bash
git add medexpert/configs/agents/orchestrator.yaml medexpert/configs/pro/agents/orchestrator.yaml medexpert/configs/opus/agents/orchestrator.yaml
git commit --signoff -m "feat(orchestrator): wire kg_search tool + STEP 1 KG lookup instruction"
```

---

## Task 4: Frontend — Citation Regex + Parser (`citations.ts`)

**Files:**
- Modify: `client/webui/frontend/src/lib/utils/citations.ts:21-28,51-75,112-127,192,282-309`

### Step 1: Update all regex patterns

In `citations.ts`:

**Line 21** — `CITATION_PATTERN`:
```typescript
export const CITATION_PATTERN = /\[?\[cite:(s\d+r\d+|research\d+|kg\d+r\d+)\]\]?/g;
```

**Line 25** — `MULTI_CITATION_PATTERN`:
```typescript
export const MULTI_CITATION_PATTERN = /\[?\[cite:((?:s\d+r\d+|research\d+|kg\d+r\d+)(?:\s*,\s*(?:cite:)?(?:s\d+r\d+|research\d+|kg\d+r\d+))+)\]\]?/g;
```

**Line 28** — `INDIVIDUAL_CITATION_PATTERN`:
```typescript
export const INDIVIDUAL_CITATION_PATTERN = /(?:cite:)?(s\d+r\d+|research\d+|kg\d+r\d+)/g;
```

### Step 2: Update `Citation` interface type union

**Line 33** — `Citation` interface:
```typescript
type: "search" | "research" | "kg";
```

### Step 3: Add KG branch to `parseCitationId`

After the research branch (line 71), add:
```typescript
    // Try kg format: kg{turn}r{index}
    const kgMatch = citationId.match(/^kg(\d+)r(\d+)$/);
    if (kgMatch) {
        return {
            type: "kg",
            sourceId: parseInt(kgMatch[2], 10),
        };
    }
```

### Step 4: Add KG tooltip to `getCitationTooltip`

Before the final fallback (`if (!citation.source)` at line 303), add:
```typescript
    const isKg = citation.type === "kg";
    if (isKg) {
        const labels = citation.source?.metadata?.labels;
        const name = citation.source?.metadata?.title || citation.source?.filename;
        const sourceUrl = citation.source?.sourceUrl || citation.source?.url;
        if (sourceUrl) {
            return name ? `${name}\n${sourceUrl}` : sourceUrl;
        }
        const labelStr = Array.isArray(labels) ? labels.join(", ") : "Entity";
        return `Knowledge Graph: ${labelStr} — ${name || "Unknown"}`;
    }
```

### Step 5: Update `afterMatch` skip-logic regex (F1 — critical)

**Line 145** — the `afterMatch` regex that detects when a single citation is part of a multi-citation group. Without this, mixed `[[cite:kg0r0, s0r1]]` causes duplicate parsing:
```typescript
// Before:
if (afterMatch.match(/^\s*,\s*(?:s\d+r\d+|research\d+)/)) {
// After:
if (afterMatch.match(/^\s*,\s*(?:s\d+r\d+|research\d+|kg\d+r\d+)/)) {
```

**Line 150** — the `beforeMatch` regex:
```typescript
// Before:
if (beforeMatch.match(/\[?\[cite:[^\]]*,\s*$/)) {
// No change needed — this regex is already generic ([^\]]*).
```

### Step 6: Switch `removeCitationMarkers` to `CLEANUP_REGEX` (F12)

**Line 192**:
```typescript
// Before:
return text.replace(CITATION_PATTERN, "");
// After:
return text.replace(CLEANUP_REGEX, "");
```

### Step 7: Commit

```bash
git add client/webui/frontend/src/lib/utils/citations.ts
git commit --signoff -m "feat(citations): add kg citation ID support to regex + parser + tooltip"
```

---

## Task 5: Frontend — Citation.tsx Private Copies

**Files:**
- Modify: `client/webui/frontend/src/lib/components/chat/Citation.tsx:429-481`

### Step 1: Update `parseCitationIdLocal` (line 429)

Change return type and add KG branch after research branch (line 446):
```typescript
function parseCitationIdLocal(citationId: string): { type: "search" | "research" | "kg"; sourceId: number } | null {
    // ... existing search and research branches ...

    // Try kg format: kg{turn}r{index}
    const kgMatch = citationId.match(/^kg(\d+)r(\d+)$/);
    if (kgMatch) {
        return {
            type: "kg",
            sourceId: parseInt(kgMatch[2], 10),
        };
    }

    return null;
}
```

### Step 2: Update `parseMultiCitationIds` (line 455)

Change type union:
```typescript
function parseMultiCitationIds(content: string): Array<{ type: "search" | "research" | "kg"; sourceId: number; citationId: string }> {
    const results: Array<{ type: "search" | "research" | "kg"; sourceId: number; citationId: string }> = [];
```

### Step 3: Update `COMBINED_CITATION_PATTERN` (line 481)

```typescript
const COMBINED_CITATION_PATTERN = /\[?\[cite:((?:s\d+r\d+|research\d+|kg\d+r\d+)(?:\s*,\s*(?:cite:)?(?:s\d+r\d+|research\d+|kg\d+r\d+))*)\]\]?/g;
```

### Step 4: Add KG display text to citation chip

MedExpert's Citation component shows domain names/titles, not numeric IDs. Find `getCitationDisplayText` (around line 104-111) and add a KG branch before the existing logic:

```typescript
// Add at the top of getCitationDisplayText, before existing logic:
if (citation.type === "kg") {
    const name = citation.source?.metadata?.title || citation.source?.filename || "KG Entity";
    const labels = citation.source?.metadata?.labels;
    const labelStr = Array.isArray(labels) ? labels[0] : "";
    return labelStr ? `KG: ${labelStr} - ${name}` : `KG: ${name}`;
}
```

This ensures KG citations show "KG: BRCA1" or "KG: breast cancer" instead of a raw domain name.

### Step 5: Commit

```bash
git add client/webui/frontend/src/lib/components/chat/Citation.tsx
git commit --signoff -m "feat(Citation): add kg citation support to private regex copies + K prefix"
```

---

## Task 6: Frontend — Citation Tests

**Files:**
- Create: `client/webui/frontend/src/lib/utils/__tests__/citations.test.ts`

### Step 1: Create KG citation test file

Create `client/webui/frontend/src/lib/utils/__tests__/citations.test.ts` with these imports and tests:

```typescript
import { describe, it, expect } from "vitest";
import {
    CITATION_PATTERN,
    MULTI_CITATION_PATTERN,
    parseCitations,
    getCitationTooltip,
    removeCitationMarkers,
    type Citation,
} from "../citations";

describe("KG citations", () => {
    it("CITATION_PATTERN matches [[cite:kg0r0]]", () => {
        const text = "Evidence shows [[cite:kg0r0]] that...";
        CITATION_PATTERN.lastIndex = 0;
        const match = CITATION_PATTERN.exec(text);
        expect(match).not.toBeNull();
        expect(match![1]).toBe("kg0r0");
    });

    it("CITATION_PATTERN matches [[cite:kg0r12]]", () => {
        const text = "[[cite:kg0r12]]";
        CITATION_PATTERN.lastIndex = 0;
        const match = CITATION_PATTERN.exec(text);
        expect(match![1]).toBe("kg0r12");
    });

    it("parseCitations parses kg0r0 with type kg", () => {
        const text = "Result [[cite:kg0r0]] here";
        const citations = parseCitations(text);
        expect(citations).toHaveLength(1);
        expect(citations[0].type).toBe("kg");
        expect(citations[0].sourceId).toBe(0);
        expect(citations[0].citationId).toBe("kg0r0");
    });

    it("parseCitations handles mixed kg + s citations", () => {
        const text = "KG [[cite:kg0r0]] and web [[cite:s0r1]] sources";
        const citations = parseCitations(text);
        expect(citations).toHaveLength(2);
        expect(citations[0].type).toBe("kg");
        expect(citations[1].type).toBe("search");
    });

    it("MULTI_CITATION_PATTERN matches [[cite:kg0r0, s0r1]]", () => {
        const text = "[[cite:kg0r0, s0r1]]";
        MULTI_CITATION_PATTERN.lastIndex = 0;
        const match = MULTI_CITATION_PATTERN.exec(text);
        expect(match).not.toBeNull();
    });

    it("getCitationTooltip returns KG entity format", () => {
        const citation: Citation = {
            marker: "[[cite:kg0r0]]",
            type: "kg",
            sourceId: 0,
            position: 0,
            citationId: "kg0r0",
            source: {
                citationId: "kg0r0",
                contentPreview: "A gene",
                relevanceScore: 1.0,
                metadata: { labels: ["Gene"], title: "BRCA1" },
            } as any,
        };
        const tooltip = getCitationTooltip(citation);
        expect(tooltip).toContain("Knowledge Graph");
        expect(tooltip).toContain("BRCA1");
    });

    it("getCitationTooltip returns URL for Study nodes", () => {
        const citation: Citation = {
            marker: "[[cite:kg0r0]]",
            type: "kg",
            sourceId: 0,
            position: 0,
            citationId: "kg0r0",
            source: {
                citationId: "kg0r0",
                contentPreview: "RCT",
                relevanceScore: 1.0,
                sourceUrl: "https://pubmed.ncbi.nlm.nih.gov/12345/",
                metadata: { title: "Study of Drug X" },
            } as any,
        };
        const tooltip = getCitationTooltip(citation);
        expect(tooltip).toContain("Study of Drug X");
        expect(tooltip).toContain("pubmed");
    });

    it("removeCitationMarkers strips kg citations", () => {
        const text = "Before [[cite:kg0r0]] after";
        const cleaned = removeCitationMarkers(text);
        expect(cleaned).toBe("Before  after");
    });
});
```

### Step 2: Run frontend tests

Run: `cd client/webui/frontend && npx vitest run --reporter verbose 2>&1 | tail -20`
Expected: All tests PASS

### Step 3: Commit

```bash
git add client/webui/frontend/src/lib/utils/__tests__/citations.test.ts
git commit --signoff -m "test(citations): add KG citation regex, parser, and tooltip tests"
```

---

## Task 7: Frontend — RAGInfoPanel `kb_search` Section

**Files:**
- Modify: `client/webui/frontend/src/lib/components/chat/rag/RAGInfoPanel.tsx`

### Step 1: Add KG section rendering

In `RAGInfoPanel.tsx`, the `VirtualizedSourceCardList` component (line 224) receives all `ragData` and sorts/renders sources. Add KG section BEFORE the existing source list.

**1.** Add `Network` to the Lucide imports at the top.

**2.** Inside the Sources `TabsContent` (around line 727, before `<VirtualizedSourceCardList>`), add a KG section:

```typescript
{/* Knowledge Graph sources — rendered before web sources */}
{ragData?.some(r => r.searchType === "kb_search") && (
    <div className="mb-3">
        <div className="flex items-center gap-2 mb-2 text-sm font-medium text-purple-400">
            <Network className="h-4 w-4" />
            Knowledge Graph
        </div>
        <div className="space-y-2">
            {ragData
                .filter(r => r.searchType === "kb_search")
                .flatMap(r => r.sources)
                .map((source) => (
                    <div key={source.citationId} className="border-l-2 border-purple-500/40 pl-2">
                        <SourceCard source={source} isHighlighted={highlightedSourceId === source.citationId} />
                    </div>
                ))}
        </div>
    </div>
)}
```

**Note:** `SourceCard` does NOT accept a `className` prop (interface is `{source, isHighlighted?}`). The purple left border is applied via a wrapper `<div>` instead. This avoids modifying the existing `SourceCard` component.

### Step 2: Run frontend tests

Run: `cd client/webui/frontend && npx vitest run --reporter verbose 2>&1 | tail -20`
Expected: PASS

### Step 3: Commit

```bash
git add client/webui/frontend/src/lib/components/chat/rag/RAGInfoPanel.tsx
git commit --signoff -m "feat(RAGInfoPanel): add Knowledge Graph section for kb_search sources"
```

---

## Task 8: Frontend — KG Visualization Citation Bridge

**Files:**
- Modify: `client/webui/frontend/src/lib/components/knowledgeGraph/EntityDetailPanel.tsx:18-22`
- Modify: `client/webui/frontend/src/lib/components/knowledgeGraph/KnowledgeGraphPage.tsx:24-30`

### Step 1: Add callback props to EntityDetailPanel

Update the interface at line 18:
```typescript
interface EntityDetailPanelProps {
    nodeId: string;
    nodeData: Record<string, unknown>;
    onClose: () => void;
    onViewSource?: (identifier: { type: "pmid" | "nct_id" | "doi"; value: string }) => void;
}
```

After the existing PubMed/ClinicalTrials links (find `pmid` and `nctId` link rendering), add a "View in Sources" button:
```typescript
{onViewSource && (pmid || nctId) && (
    <Button
        variant="outline"
        size="sm"
        onClick={() => onViewSource({
            type: pmid ? "pmid" : "nct_id",
            value: (pmid || nctId)!,
        })}
    >
        View in Sources
    </Button>
)}
```

### Step 2: Implement callback in KnowledgeGraphPage

For MVP, the "View in Sources" button always opens the external URL directly. The KG page is a separate route from `/chat`, so opening the chat Sources panel would be invisible. Cross-page bridging is deferred.

Inside the component (line 24+), add:
```typescript
const handleViewSource = useCallback(({ type, value }: { type: string; value: string }) => {
    const url = type === "pmid" ? `https://pubmed.ncbi.nlm.nih.gov/${value}/`
        : type === "nct_id" ? `https://clinicaltrials.gov/study/${value}`
        : `https://doi.org/${value}`;
    window.open(url, "_blank");
}, []);
```

**Note:** `onSearchSources` for Disease/Drug/Gene nodes is deferred to a future iteration — it requires cross-page navigation to the chat Sources panel which adds complexity beyond MVP scope.

Pass the callback to EntityDetailPanel:
```typescript
<EntityDetailPanel
    nodeId={selectedNode.id}
    nodeData={selectedNode.data}
    onClose={() => setSelectedNode(null)}
    onViewSource={handleViewSource}
/>
```

### Step 3: Run frontend tests

Run: `cd client/webui/frontend && npx vitest run --reporter verbose 2>&1 | tail -20`
Expected: PASS

### Step 4: Commit

```bash
git add client/webui/frontend/src/lib/components/knowledgeGraph/EntityDetailPanel.tsx client/webui/frontend/src/lib/components/knowledgeGraph/KnowledgeGraphPage.tsx
git commit --signoff -m "feat(KG): add View in Sources bridge from graph visualization to RAG panel"
```

---

## Task 9: Run Full Test Suite

### Step 1: Run backend tests

Run: `cd medexpert && python -m pytest tests/unit/test_kg_search.py tests/unit/test_protocol_step_validator.py -v --no-header`
Expected: All tests PASS

### Step 2: Run frontend tests

Run: `cd client/webui/frontend && npx vitest run --reporter verbose 2>&1 | tail -30`
Expected: All tests PASS (1 pre-existing Storybook failure acceptable)

### Step 3: Run linting

Run: `cd medexpert && ruff check src/lifesci_tools/kg_search.py && ruff format --check src/lifesci_tools/kg_search.py`
Expected: No errors

### Step 4: Final commit if any fixes needed

```bash
git add -u && git commit --signoff -m "fix: address lint/test issues from KG-RAG adapter"
```

**Note:** Use `git add -u` (tracked files only) not `git add -A` to avoid staging untracked files.
