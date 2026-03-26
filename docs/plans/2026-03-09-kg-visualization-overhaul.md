# Knowledge Graph Visualization Overhaul — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix source propagation bugs and replace the Cytoscape knowledge graph with an SVG columnar renderer showing the actual agent pipeline flow.

**Architecture:** Backend graph model changes (new FOUND/EVIDENCED_BY edge types, raw source storage) + frontend rewrite from Cytoscape to pure SVG with 4-column DAG layout (Sessions → Specialists → Entities → Studies). Light background theme.

**Tech Stack:** Python (graph_writer, source_collector, router, MCP server), React + SVG (GraphCanvas), TypeScript, Vitest, pytest.

**Design doc:** `docs/plans/2026-03-09-kg-visualization-overhaul-design.md`

---

## Task 1: Store raw sources in Redis (B2)

**Files:**
- Modify: `medexpert/src/lifesci_tools/source_collector.py:440-460`
- Test: `medexpert/tests/unit/test_source_collector.py` (existing, add test)

**Step 1: Write the failing test**

Add to the test file for source_collector (find the test class that tests `publish_sources`):

```python
@pytest.mark.asyncio
async def test_publish_sources_stores_raw_sources_in_redis(mock_tool_context, ...):
    """publish_sources should store raw sources array in Redis alongside citation_map."""
    # Setup: sources with pmid/nct_id fields
    sources = [
        {"title": "Study A", "pmid": "12345678", "snippet": "...", "publication_year": "2024"},
        {"title": "Trial B", "nct_id": "NCT01234567", "snippet": "..."},
    ]
    args = {"query": "test query", "sources": json.dumps(sources)}

    # Execute
    result = await tool._run_async_impl(args, mock_tool_context)

    # Verify: raw sources stored in Redis under published_sources_raw key
    # (mock Redis and check .set() was called with the right key)
    assert result["status"] == "published"
    # Check that Redis .set() was called with key containing "published_sources_raw"
    redis_calls = mock_redis.set.call_args_list
    raw_key = next((c for c in redis_calls if "published_sources_raw" in str(c)), None)
    assert raw_key is not None, "Expected Redis set call with published_sources_raw key"
    stored = json.loads(raw_key[0][1])
    assert len(stored) == 2
    assert stored[0]["pmid"] == "12345678"
```

**Step 2: Run test to verify it fails**

Run: `cd medexpert && python -m pytest tests/unit/test_source_collector.py -k "raw_sources_in_redis" -v`
Expected: FAIL — no Redis call with `published_sources_raw` key.

**Step 3: Implement the fix**

In `medexpert/src/lifesci_tools/source_collector.py`, after the existing `citation_map_json` Redis store block (line ~447), add:

```python
                    # Also store the raw sources array for graph_writer
                    # (citation_map has {id, title, snippet, url} — no pmid/nct_id/doi)
                    raw_key = f"medexpert:{session_id}:evidence:published_sources_raw"
                    raw_json = json.dumps(sources)
                    r.set(raw_key, raw_json.encode("utf-8"), ex=3600)
                    logger.info(
                        "%s Auto-stored raw sources in Redis (%d entries, key=%s)",
                        log_id, len(sources), raw_key,
                    )
```

**Step 4: Run test to verify it passes**

Run: `cd medexpert && python -m pytest tests/unit/test_source_collector.py -k "raw_sources_in_redis" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add medexpert/src/lifesci_tools/source_collector.py medexpert/tests/unit/test_source_collector.py
git commit --signoff -m "fix(source_collector): store raw sources in Redis for graph_writer"
```

---

## Task 2: Update graph_writer to read raw sources + new edge model (B1, B4)

**Files:**
- Modify: `medexpert/src/lifesci_tools/graph_writer.py:309-410` (build_session_cypher)
- Modify: `medexpert/src/lifesci_tools/graph_writer.py:416-485` (_read_session_data)
- Test: `medexpert/tests/unit/test_graph_writer.py`

### Step 1: Write failing tests for new edge model

Add to `medexpert/tests/unit/test_graph_writer.py`:

```python
def test_build_session_cypher_new_edge_model():
    """New model: Session-QUERIED->Specialist, Specialist-FOUND->Entity, Entity-EVIDENCED_BY->Study."""
    statements = build_session_cypher(
        session_id="s1",
        query_text="diabetes treatment",
        domain="endocrinology",
        specialists_used=["DrugSpecialist"],
        studies=[{"pmid": "12345678", "nct_id": "", "doi": "", "title": "Study", "year": "2024"}],
        diseases=["diabetes"],
        drugs=["metformin"],
        genes=[],
        # New param: maps specialist -> entities they found
        specialist_entities={"DrugSpecialist": {"drugs": ["metformin"], "diseases": ["diabetes"]}},
        # New param: maps entity -> studies that evidence it
        entity_studies={"metformin": ["12345678"], "diabetes": ["12345678"]},
    )
    cypher_text = " ".join(c for c, _ in statements)
    # Should have QUERIED (session->specialist)
    assert "QUERIED" in cypher_text
    # Should have FOUND (specialist->entity)
    assert "FOUND" in cypher_text
    # Should have EVIDENCED_BY (entity->study)
    assert "EVIDENCED_BY" in cypher_text
    # Should NOT have old ABOUT or CITED edges from session
    session_about = [c for c, p in statements if "Session" in c and "ABOUT" in c]
    assert len(session_about) == 0, "Session should not have ABOUT edges in new model"


def test_build_session_cypher_stub_study():
    """Studies referenced in findings but not in source list get partial: true."""
    statements = build_session_cypher(
        session_id="s1",
        query_text="test",
        domain="general",
        specialists_used=[],
        studies=[{"pmid": "99999999", "nct_id": "", "doi": "", "title": "", "year": "", "partial": True}],
        diseases=[],
        drugs=[],
        genes=[],
        specialist_entities={},
        entity_studies={},
    )
    # Find the study MERGE statement — should set partial = true
    study_stmts = [(c, p) for c, p in statements if "Study" in c]
    assert len(study_stmts) >= 1
    cypher, params = study_stmts[0]
    assert params.get("partial") is True or "partial" in cypher


def test_build_session_cypher_cited_cross_ref():
    """CITED edges between studies (cross-references)."""
    statements = build_session_cypher(
        session_id="s1",
        query_text="test",
        domain="general",
        specialists_used=[],
        studies=[
            {"pmid": "11111111", "nct_id": "", "doi": "", "title": "A", "year": "2024"},
            {"pmid": "22222222", "nct_id": "", "doi": "", "title": "B", "year": "2024"},
        ],
        diseases=[],
        drugs=[],
        genes=[],
        specialist_entities={},
        entity_studies={},
        cross_references=[("11111111", "22222222")],
    )
    cited_stmts = [(c, p) for c, p in statements if "CITED" in c]
    assert len(cited_stmts) >= 1
```

### Step 2: Run tests to verify they fail

Run: `cd medexpert && python -m pytest tests/unit/test_graph_writer.py -k "new_edge_model or stub_study or cited_cross_ref" -v`
Expected: FAIL — `build_session_cypher()` doesn't accept the new params.

### Step 3: Update _read_session_data to read raw sources

In `medexpert/src/lifesci_tools/graph_writer.py`, update `_read_session_data()` (around line 452):

```python
            # Read raw sources (stored by source_collector with full pmid/nct_id/doi)
            raw_sources = await client.get(f"{pfx}:evidence:published_sources_raw")
            if raw_sources:
                try:
                    data["sources"] = json.loads(raw_sources)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Fallback: citation_map_json (has {id, title, snippet, url} — no pmid/doi)
            if not data["sources"]:
                citation_map_raw = await client.get(f"{pfx}:evidence:citation_map_json")
                # ... existing fallback code ...
```

### Step 4: Rewrite build_session_cypher with new edge model

Replace `build_session_cypher()` (lines 309-410) with:

```python
def build_session_cypher(
    session_id: str,
    query_text: str,
    domain: str,
    specialists_used: list[str],
    studies: list[dict],
    diseases: list[str],
    drugs: list[str],
    genes: list[str],
    specialist_entities: dict[str, dict[str, list[str]]] | None = None,
    entity_studies: dict[str, list[str]] | None = None,
    cross_references: list[tuple[str, str]] | None = None,
) -> list[tuple[str, dict]]:
    """Build Cypher for the session graph using the pipeline DAG model.

    Edge model:
      Session -QUERIED-> Specialist -FOUND-> Entity -EVIDENCED_BY-> Study
                                              Study -CITED-> Study
    """
    statements = []
    now = datetime.now(timezone.utc).isoformat()
    specialist_entities = specialist_entities or {}
    entity_studies = entity_studies or {}
    cross_references = cross_references or []

    # 1. Session node
    statements.append((
        "MERGE (s:Session {session_id: $sid}) "
        "ON CREATE SET s.query = $query, s.domain = $domain, s.created_at = $now "
        "ON MATCH SET s.query = $query",
        {"sid": session_id, "query": query_text[:1000], "domain": domain[:200], "now": now},
    ))

    # 2. Specialist nodes + Session-QUERIED->Specialist
    for spec in specialists_used:
        statements.append((
            "MERGE (sp:Specialist {name: $name}) "
            "WITH sp "
            "MATCH (s:Session {session_id: $sid}) "
            "MERGE (s)-[:QUERIED]->(sp)",
            {"name": spec, "sid": session_id},
        ))

    # 3. Study nodes (create all studies first, with partial flag)
    study_id_map = {}  # primary_id -> merge_prop for later EVIDENCED_BY lookups
    for study in studies:
        if study.get("pmid"):
            merge_prop, merge_val = "pmid", study["pmid"]
        elif study.get("nct_id"):
            merge_prop, merge_val = "nct_id", study["nct_id"]
        elif study.get("doi"):
            merge_prop, merge_val = "doi", study["doi"]
        else:
            continue
        study_id_map[merge_val] = merge_prop
        is_partial = study.get("partial", False)
        statements.append((
            f"MERGE (st:Study {{{merge_prop}: $primary_id}}) "
            "ON CREATE SET st.pmid = $pmid, st.nct_id = $nct_id, st.doi = $doi, "
            "st.title = $title, st.year = $year, st.partial = $partial, st.created_at = $now",
            {
                "primary_id": merge_val,
                "pmid": study.get("pmid", ""),
                "nct_id": study.get("nct_id", ""),
                "doi": study.get("doi", ""),
                "title": study.get("title", ""),
                "year": study.get("year", ""),
                "partial": is_partial,
                "now": now,
            },
        ))

    # 4. Entity nodes
    all_entities: list[tuple[str, str, str]] = []  # (label, name, truncated)
    for d in diseases:
        all_entities.append(("Disease", d, d[:200]))
    for dr in drugs:
        all_entities.append(("Drug", dr, dr[:200]))
    for g in genes:
        all_entities.append(("Gene", g, g[:100]))

    for label, name, truncated in all_entities:
        statements.append((
            f"MERGE (e:{label} {{name: $name}}) "
            "ON CREATE SET e.created_at = $now",
            {"name": truncated, "now": now},
        ))

    # 5. Specialist-FOUND->Entity edges
    for spec, entities in specialist_entities.items():
        for entity_type in ("diseases", "drugs", "genes"):
            label_map = {"diseases": "Disease", "drugs": "Drug", "genes": "Gene"}
            label = label_map[entity_type]
            for entity_name in entities.get(entity_type, []):
                statements.append((
                    f"MATCH (sp:Specialist {{name: $spec}}) "
                    f"MATCH (e:{label} {{name: $ename}}) "
                    "MERGE (sp)-[:FOUND]->(e)",
                    {"spec": spec, "ename": entity_name[:200]},
                ))

    # 6. Entity-EVIDENCED_BY->Study edges
    for entity_name, study_ids in entity_studies.items():
        for study_id in study_ids:
            if study_id not in study_id_map:
                continue
            merge_prop = study_id_map[study_id]
            # Determine entity label by checking which list it's in
            if entity_name in diseases:
                elabel = "Disease"
            elif entity_name in drugs:
                elabel = "Drug"
            elif entity_name in genes:
                elabel = "Gene"
            else:
                continue
            statements.append((
                f"MATCH (e:{elabel} {{name: $ename}}) "
                f"MATCH (st:Study {{{merge_prop}: $study_id}}) "
                "MERGE (e)-[:EVIDENCED_BY]->(st)",
                {"ename": entity_name[:200], "study_id": study_id},
            ))

    # 7. Study-CITED->Study (cross-references)
    for from_id, to_id in cross_references:
        if from_id not in study_id_map or to_id not in study_id_map:
            continue
        from_prop = study_id_map[from_id]
        to_prop = study_id_map[to_id]
        statements.append((
            f"MATCH (s1:Study {{{from_prop}: $from_id}}) "
            f"MATCH (s2:Study {{{to_prop}: $to_id}}) "
            "MERGE (s1)-[:CITED]->(s2)",
            {"from_id": from_id, "to_id": to_id},
        ))

    return statements
```

### Step 5: Update _do_write to build specialist_entities and entity_studies maps

In `_do_write()`, after entity extraction (around line 666), add logic to build the attribution maps from `agent_name` field on sources:

```python
        # Build specialist -> entity attribution map from source metadata
        specialist_entities = {}
        for src in sources:
            agent = src.get("agent_name", "")
            if not agent:
                continue
            if agent not in specialist_entities:
                specialist_entities[agent] = {"diseases": [], "drugs": [], "genes": []}
            # ... attribute entities to specialists based on source_type/agent_name

        # Build entity -> studies map
        entity_studies = {}
        for src in sources:
            primary_id = src.get("pmid") or src.get("nct_id") or src.get("doi") or ""
            if not primary_id:
                continue
            title = (src.get("title") or "").lower()
            for d in diseases:
                if d.lower() in title or d.lower() in (src.get("snippet") or "").lower():
                    entity_studies.setdefault(d, []).append(primary_id)
            for dr in drugs:
                if dr.lower() in title or dr.lower() in (src.get("snippet") or "").lower():
                    entity_studies.setdefault(dr, []).append(primary_id)
            for g in genes:
                if g.upper() in (src.get("title") or "") or g.upper() in (src.get("snippet") or ""):
                    entity_studies.setdefault(g, []).append(primary_id)

        # Extract cross-references from inline [[pmid:X]] in findings
        cross_references = []
        pmid_set = {s.get("pmid") for s in studies if s.get("pmid")}
        # ... parse findings text for co-occurring PMIDs within same paragraph
```

### Step 6: Update existing tests that test old build_session_cypher signature

Update `test_build_session_cypher_basic` and `test_build_session_cypher_idempotent` to pass the new optional params (they should still work with defaults).

### Step 7: Run all graph_writer tests

Run: `cd medexpert && python -m pytest tests/unit/test_graph_writer.py -v`
Expected: ALL PASS

### Step 8: Commit

```bash
git add medexpert/src/lifesci_tools/graph_writer.py medexpert/tests/unit/test_graph_writer.py
git commit --signoff -m "feat(graph_writer): new DAG edge model — FOUND, EVIDENCED_BY, CITED"
```

---

## Task 3: Update knowledge_graph MCP server for new edge types (B3)

**Files:**
- Modify: `medexpert/src/mcp_servers/knowledge_graph/server.py:367-450` (get_session_graph)
- Test: `medexpert/tests/unit/test_knowledge_graph_server.py`

### Step 1: Write failing test for new edge types in session graph

Add to `medexpert/tests/unit/test_knowledge_graph_server.py`:

```python
@pytest.mark.asyncio
async def test_get_session_graph_returns_new_edge_types(mock_driver):
    """Session graph should return QUERIED, FOUND, EVIDENCED_BY, CITED edge types."""
    # Setup mock driver to return records with new edge types
    # ... (mock the Cypher result with FOUND and EVIDENCED_BY edges)
    result = await get_session_graph("test-session-001")
    assert result["success"] is True
    edge_labels = {e["label"] for e in result["edges"]}
    # Should support new edge types
    assert "QUERIED" in edge_labels or len(result["edges"]) == 0  # may be empty in mock
```

### Step 2: Update get_session_graph Cypher

The existing Cypher at line 369-373 does a 2-hop traversal from Session. Update it to traverse the new pipeline:

```python
        # Get the full pipeline graph: Session->Specialist->Entity->Study
        # Also get Study->Study CITED cross-references
        cypher = (
            "MATCH (s:Session {session_id: $sid})-[r1:QUERIED]->(sp:Specialist) "
            "OPTIONAL MATCH (sp)-[r2:FOUND]->(e) "
            "OPTIONAL MATCH (e)-[r3:EVIDENCED_BY]->(st:Study) "
            "OPTIONAL MATCH (st)-[r4:CITED]->(st2:Study) "
            "RETURN s, r1, sp, r2, e, r3, st, r4, st2 LIMIT 500"
        )
```

### Step 3: Run tests

Run: `cd medexpert && python -m pytest tests/unit/test_knowledge_graph_server.py -v`
Expected: ALL PASS

### Step 4: Commit

```bash
git add medexpert/src/mcp_servers/knowledge_graph/server.py medexpert/tests/unit/test_knowledge_graph_server.py
git commit --signoff -m "feat(knowledge_graph): update session graph Cypher for pipeline DAG model"
```

---

## Task 4: Fix explore endpoint to return edges (B3)

**Files:**
- Modify: `medexpert/src/graph_api/router.py:174-213` (explore_graph)

### Step 1: Write failing test

Create `medexpert/tests/unit/test_graph_api_router.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_explore_endpoint_returns_edges():
    """Explore endpoint should fetch edges between returned nodes, not return empty."""
    mock_nodes = [
        {"id": "1", "labels": ["Disease"], "name": "Diabetes", "properties": {}},
        {"id": "2", "labels": ["Drug"], "name": "Metformin", "properties": {}},
    ]
    mock_edges = [
        {"id": "1-FOUND-2", "source": "1", "target": "2", "label": "FOUND"},
    ]

    with patch("graph_api.router._call_tool") as mock_call:
        # First call: query_knowledge_graph returns nodes
        # Second call: edge query returns edges
        mock_call.side_effect = [
            {"success": True, "results": mock_nodes, "total_results": 2},
            {"success": True, "edges": mock_edges},
        ]

        from graph_api.router import explore_graph
        # Simulate calling the endpoint
        response = await explore_graph(labels=None, limit=100)
        data = response.body  # JSONResponse
        # Should have edges, not empty
        # (exact assertion depends on implementation)
```

### Step 2: Implement the fix

Replace the explore_graph endpoint (lines 174-213) to fetch edges:

```python
@router.get("/explore")
async def explore_graph(
    labels: str | None = Query(None, description="Comma-separated entity types to browse"),
    limit: int = Query(100, ge=1, le=500),
):
    """Browse the knowledge base with entity type filters and real edges."""
    entity_types = [t.strip() for t in labels.split(",")] if labels else None
    result = await _call_tool(
        "query_knowledge_graph",
        {"query": "*", "entity_types": entity_types, "limit": limit},
    )
    if not result.get("success", False):
        return JSONResponse(content=result, status_code=503)

    raw_results = result.get("results", [])
    _STRIP_KEYS = {"id", "labels", "name", "description", "type", "label", "properties"}
    nodes = []
    node_ids = []
    for idx, item in enumerate(raw_results):
        if isinstance(item, dict):
            node_id = item.get("id") or item.get("name") or f"node-{idx}"
            node_labels = item.get("labels") or [item.get("type") or "Entity"]
            nodes.append({
                "id": node_id,
                "name": item.get("name", ""),
                "labels": node_labels if isinstance(node_labels, list) else [node_labels],
                "description": item.get("description", ""),
                "properties": {k: v for k, v in item.items() if k not in _STRIP_KEYS},
            })
            node_ids.append(node_id)

    # Fetch edges between returned nodes in a single Cypher pass
    edges = []
    if node_ids:
        edges = await _fetch_edges_between_nodes(node_ids)

    return JSONResponse(content={
        "success": True,
        "nodes": nodes,
        "edges": edges,
        "total_results": result.get("total_results", len(nodes)),
    })
```

Add the helper function:

```python
async def _fetch_edges_between_nodes(node_ids: list[str]) -> list[dict]:
    """Fetch all edges between a set of node IDs. Single Cypher pass."""
    try:
        from mcp_servers.knowledge_graph.server import _get_driver, _sanitize_cypher_param

        driver = _get_driver()
        if driver is None:
            return []

        # Use element IDs to match within the node set
        cypher = (
            "MATCH (a)-[r]->(b) "
            "WHERE elementId(a) IN $ids AND elementId(b) IN $ids "
            "RETURN elementId(a) AS src, elementId(b) AS tgt, type(r) AS label "
            "LIMIT 500"
        )
        edges = []
        seen = set()
        with driver.session() as session:
            result = session.run(cypher, {"ids": node_ids})
            for record in result:
                edge_id = f"{record['src']}-{record['label']}-{record['tgt']}"
                if edge_id not in seen:
                    seen.add(edge_id)
                    edges.append({
                        "id": edge_id,
                        "source": record["src"],
                        "target": record["tgt"],
                        "label": record["label"],
                    })
        return edges
    except Exception as exc:
        log.warning("Edge fetch failed (non-fatal): %s", exc)
        return []
```

### Step 3: Run tests

Run: `cd medexpert && python -m pytest tests/unit/test_graph_api_router.py -v`
Expected: PASS

### Step 4: Commit

```bash
git add medexpert/src/graph_api/router.py medexpert/tests/unit/test_graph_api_router.py
git commit --signoff -m "fix(graph_api): explore endpoint returns real edges between nodes"
```

---

## Task 5: Update TypeScript types (F)

**Files:**
- Modify: `client/webui/frontend/src/lib/types/fe.ts:277-307`

### Step 1: Update GraphEdge and add new types

```typescript
// Knowledge Graph Types

export interface GraphNode {
    id: string;
    labels: string[];
    name: string;
    description?: string;
    properties: Record<string, unknown>;
}

export type GraphEdgeType = "QUERIED" | "FOUND" | "EVIDENCED_BY" | "CITED";

export interface GraphEdge {
    id: string;
    source: string;
    target: string;
    label: GraphEdgeType | string;
}

export type GraphNodeType = "Session" | "Specialist" | "Disease" | "Drug" | "Gene" | "Study";

export interface SessionGraph {
    session_id: string;
    nodes: GraphNode[];
    edges: GraphEdge[];
    total_nodes: number;
    total_edges: number;
}

export interface GraphStats {
    node_counts: Record<string, number>;
    edge_counts: Record<string, number>;
    total_nodes: number;
    total_edges: number;
}
```

### Step 2: Commit

```bash
git add client/webui/frontend/src/lib/types/fe.ts
git commit --signoff -m "feat(types): add GraphEdgeType and GraphNodeType for KG overhaul"
```

---

## Task 6: Rewrite useGraphData hook

**Files:**
- Modify: `client/webui/frontend/src/lib/hooks/useGraphData.ts`

### Step 1: Rewrite to remove Cytoscape element conversion

The hook should return raw `{nodes, edges}` instead of Cytoscape elements. The SVG renderer will consume them directly.

```typescript
import { useState, useEffect, useCallback } from "react";
import type { GraphNode, GraphEdge } from "@/lib/types";

interface UseGraphDataParams {
    sessionId?: string | null;
    entityTypes?: string[];
}

interface UseGraphDataReturn {
    nodes: GraphNode[];
    edges: GraphEdge[];
    isLoading: boolean;
    error: string | null;
    isEmpty: boolean;
    refetch: () => void;
}

export function useGraphData({ sessionId, entityTypes }: UseGraphDataParams): UseGraphDataReturn {
    const [nodes, setNodes] = useState<GraphNode[]>([]);
    const [edges, setEdges] = useState<GraphEdge[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [fetchKey, setFetchKey] = useState(0);

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        setError(null);

        try {
            let url: string;

            if (sessionId) {
                url = `/api/v1/graph/session/${encodeURIComponent(sessionId)}`;
            } else {
                const params = new URLSearchParams({ limit: "100" });
                if (entityTypes && entityTypes.length > 0) {
                    params.set("labels", entityTypes.join(","));
                }
                url = `/api/v1/graph/explore?${params.toString()}`;
            }

            const res = await fetch(url, { credentials: "include" });

            if (!res.ok) {
                if (res.status === 404) {
                    setNodes([]);
                    setEdges([]);
                    setIsLoading(false);
                    return;
                }
                throw new Error(`Failed to fetch graph data (${res.status})`);
            }

            const data = await res.json();
            setNodes(data.nodes || []);
            setEdges(data.edges || []);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to fetch graph data");
            setNodes([]);
            setEdges([]);
        } finally {
            setIsLoading(false);
        }
    }, [sessionId, entityTypes, fetchKey]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const isEmpty = nodes.length === 0 && edges.length === 0;
    const refetch = useCallback(() => setFetchKey((prev) => prev + 1), []);

    return { nodes, edges, isLoading, error, isEmpty, refetch };
}
```

### Step 2: Commit

```bash
git add client/webui/frontend/src/lib/hooks/useGraphData.ts
git commit --signoff -m "refactor(useGraphData): return raw nodes/edges instead of Cytoscape elements"
```

---

## Task 7: Create GraphCanvas SVG component

**Files:**
- Create: `client/webui/frontend/src/lib/components/knowledgeGraph/GraphCanvas.tsx`
- Reference: User's SVG reference code (see design doc)

This is the largest task. The component should:

### Step 1: Create the layout utility functions

```typescript
// Constants
const EDGE_TYPES = {
    QUERIED: { color: "#0e7490", dash: "", width: 2 },
    FOUND: { color: "#7c3aed", dash: "", width: 2 },
    EVIDENCED_BY: { color: "#64748b", dash: "6,3", width: 1.5 },
    CITED: { color: "#d97706", dash: "4,4", width: 1.8 },
};

const NODE_STYLES = {
    Session: { fill: "#7c3aed", stroke: "#a78bfa", text: "#fff" },
    Specialist: { fill: "#f8fafc", stroke: "#0e7490", text: "#0e7490" },
    Disease: { fill: "#f8fafc", stroke: "#64748b", text: "#1e293b" },
    Drug: { fill: "#f8fafc", stroke: "#3b82f6", text: "#1e293b" },
    Gene: { fill: "#f8fafc", stroke: "#16a34a", text: "#1e293b" },
    Study: { fill: "#f8fafc", stroke: "#d97706", text: "#92400e" },
};

const COLUMNS = {
    Session: 90,
    Specialist: 310,
    Entity: 570,    // Disease, Drug, Gene
    Study: 830,
};

// Column assignment
function getNodeColumn(labels: string[]): string {
    const label = labels[0] || "";
    if (label === "Session") return "Session";
    if (label === "Specialist") return "Specialist";
    if (["Disease", "Drug", "Gene"].includes(label)) return "Entity";
    if (label === "Study") return "Study";
    return "Entity";
}
```

### Step 2: Implement the weighted-average-Y layout algorithm

```typescript
function layoutNodes(nodes, edges) {
    // 1. Assign columns
    // 2. For column 0 (Session), space evenly
    // 3. For each subsequent column, Y = weighted avg of connected nodes in prev column
    // 4. Overlap sweep: push apart nodes closer than 2*maxRadius + 20
    // Return positioned nodes with x, y coordinates
}
```

### Step 3: Implement the full SVG renderer

Follow the reference code pattern:
- SVG viewBox with column lanes
- Edge rendering with curved paths and arrowheads
- Node rendering with type-specific styles
- Multi-colored ring for shared hub entities
- Degree badges
- Drag interaction via SVG point transform
- Click-to-focus dimming
- Hover glow effects

### Step 4: Write tests

Create `client/webui/frontend/src/lib/components/knowledgeGraph/__tests__/GraphCanvas.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import { describe, test, expect } from "vitest";
import GraphCanvas from "../GraphCanvas";

describe("GraphCanvas", () => {
    const mockNodes = [
        { id: "1", labels: ["Session"], name: "Session A", properties: { query: "test" } },
        { id: "2", labels: ["Specialist"], name: "Literature", properties: {} },
        { id: "3", labels: ["Disease"], name: "Diabetes", properties: {} },
        { id: "4", labels: ["Study"], name: "PMID:12345", properties: { pmid: "12345" } },
    ];
    const mockEdges = [
        { id: "e1", source: "1", target: "2", label: "QUERIED" },
        { id: "e2", source: "2", target: "3", label: "FOUND" },
        { id: "e3", source: "3", target: "4", label: "EVIDENCED_BY" },
    ];

    test("renders SVG container", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        expect(document.querySelector("svg")).toBeTruthy();
    });

    test("renders column headers", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByText("SESSIONS")).toBeTruthy();
        expect(screen.getByText("SPECIALISTS")).toBeTruthy();
        expect(screen.getByText("SHARED ENTITIES")).toBeTruthy();
        expect(screen.getByText("STUDIES")).toBeTruthy();
    });

    test("renders nodes", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByText("Session A")).toBeTruthy();
        expect(screen.getByText("Diabetes")).toBeTruthy();
    });

    test("renders edge legend", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByText("QUERIED")).toBeTruthy();
        expect(screen.getByText("FOUND")).toBeTruthy();
    });
});
```

### Step 5: Run tests

Run: `cd client/webui/frontend && npm run test:unit -- --run --reporter verbose`
Expected: ALL PASS

### Step 6: Commit

```bash
git add client/webui/frontend/src/lib/components/knowledgeGraph/GraphCanvas.tsx
git add client/webui/frontend/src/lib/components/knowledgeGraph/__tests__/GraphCanvas.test.tsx
git commit --signoff -m "feat(GraphCanvas): SVG columnar renderer with pipeline DAG layout"
```

---

## Task 8: Update KnowledgeGraphPage — unified mode, wire GraphCanvas

**Files:**
- Modify: `client/webui/frontend/src/lib/components/knowledgeGraph/KnowledgeGraphPage.tsx`
- Delete: `client/webui/frontend/src/lib/components/knowledgeGraph/GraphLegend.tsx`
- Delete: `client/webui/frontend/src/lib/components/knowledgeGraph/CytoscapeGraph.tsx`

### Step 1: Rewrite KnowledgeGraphPage

- Remove session/explore tab toggle (unified mode)
- Remove Cytoscape imports
- Remove GraphLegend import
- Use `useGraphData` with simplified interface
- URL param `?session=X` pre-selects that session node
- Wire GraphCanvas with `onNodeClick`, `highlightedNodes`

```typescript
import GraphCanvas from "./GraphCanvas";
// Remove: import CytoscapeGraph from "./CytoscapeGraph";
// Remove: import GraphLegend from "./GraphLegend";
```

### Step 2: Update tests

Update `KnowledgeGraphPage.test.tsx`:
- Remove Cytoscape mocks
- Remove tab toggle tests (no tabs anymore)
- Update empty state text expectations
- Keep stats, NLQ, refresh tests

### Step 3: Run tests

Run: `cd client/webui/frontend && npm run test:unit -- --run --reporter verbose`
Expected: ALL PASS

### Step 4: Commit

```bash
git add client/webui/frontend/src/lib/components/knowledgeGraph/KnowledgeGraphPage.tsx
git add client/webui/frontend/src/lib/components/knowledgeGraph/__tests__/KnowledgeGraphPage.test.tsx
git rm client/webui/frontend/src/lib/components/knowledgeGraph/CytoscapeGraph.tsx
git rm client/webui/frontend/src/lib/components/knowledgeGraph/GraphLegend.tsx
git commit --signoff -m "feat(KnowledgeGraphPage): unified mode with GraphCanvas, remove Cytoscape"
```

---

## Task 9: Update EntityDetailPanel — PubMed links, filter empty props

**Files:**
- Modify: `client/webui/frontend/src/lib/components/knowledgeGraph/EntityDetailPanel.tsx`

### Step 1: Add external links for Study nodes

```typescript
// Add after the labels section
{nodeLabel === "Study" && (nodeData.pmid || nodeData.nct_id) && (
    <div className="border-b border-border px-4 py-3">
        {nodeData.pmid && (
            <a href={`https://pubmed.ncbi.nlm.nih.gov/${nodeData.pmid}/`}
               target="_blank" rel="noopener noreferrer"
               className="text-xs text-blue-500 hover:underline">
                View on PubMed
            </a>
        )}
        {nodeData.nct_id && (
            <a href={`https://clinicaltrials.gov/study/${nodeData.nct_id}`}
               target="_blank" rel="noopener noreferrer"
               className="ml-3 text-xs text-blue-500 hover:underline">
                View on ClinicalTrials.gov
            </a>
        )}
    </div>
)}
```

### Step 2: Filter empty string properties

```typescript
const properties = Object.entries(nodeData)
    .filter(([key]) => !HIDDEN_KEYS.has(key) && key !== "description")
    .filter(([, value]) => value !== "" && value !== null && value !== undefined);
```

### Step 3: Show partial study indicator

```typescript
{nodeData.partial && (
    <div className="border-b border-border px-4 py-2">
        <span className="text-xs text-amber-500">Stub — referenced but not directly retrieved</span>
    </div>
)}
```

### Step 4: Commit

```bash
git add client/webui/frontend/src/lib/components/knowledgeGraph/EntityDetailPanel.tsx
git commit --signoff -m "feat(EntityDetailPanel): PubMed links, filter empty props, partial indicator"
```

---

## Task 10: Remove Cytoscape dependencies

**Files:**
- Modify: `client/webui/frontend/package.json`

### Step 1: Remove deps

```bash
cd client/webui/frontend && npm uninstall cytoscape cytoscape-cose-bilkent @types/cytoscape
```

### Step 2: Verify no remaining imports

```bash
grep -r "cytoscape" client/webui/frontend/src/ --include="*.ts" --include="*.tsx" | grep -v "__tests__"
```

Expected: No matches (tests may still mock it — those should have been cleaned in Task 8).

### Step 3: Run full test suite

Run: `cd client/webui/frontend && npm run test:unit -- --run`
Expected: ALL PASS

### Step 4: Build check

Run: `cd client/webui/frontend && npm run build`
Expected: Build succeeds.

### Step 5: Commit

```bash
git add client/webui/frontend/package.json client/webui/frontend/package-lock.json
git commit --signoff -m "chore: remove cytoscape dependencies (replaced by SVG renderer)"
```

---

## Task 11: Run full backend test suite

**Files:** None (verification only)

### Step 1: Run all medexpert unit tests

Run: `cd medexpert && python -m pytest tests/unit/ -v --tb=short`
Expected: ALL PASS (no regressions from graph_writer, source_collector, router changes)

### Step 2: Fix any failures

Address any test failures caused by the edge model changes.

### Step 3: Commit fixes if any

```bash
git commit --signoff -m "fix: address test regressions from KG edge model change"
```

---

## Task 12: Run full frontend test suite

**Files:** None (verification only)

### Step 1: Run all frontend tests

Run: `cd client/webui/frontend && npm run test:unit -- --run --reporter verbose`
Expected: ALL PASS

### Step 2: Run build

Run: `cd client/webui/frontend && npm run build`
Expected: Build succeeds with no TypeScript errors.

### Step 3: Final commit

```bash
git commit --signoff -m "chore: verify all tests pass after KG visualization overhaul"
```
