# Knowledge Graph Visualization Overhaul

**Date:** 2026-03-09
**Status:** Approved
**Scope:** Fix source propagation bugs + full visual overhaul inspired by DEFRA graph patterns

## Problem Statement

Two functional bugs and several visualization quality issues:

1. **Explore endpoint returns zero edges** — `router.py:211` hardcodes `"edges": []`. Every node in explore mode floats in isolation.
2. **citation_map format mismatch** — `source_collector` writes `{id, title, snippet, url}` to Redis. `graph_writer` feeds this to `extract_entities_from_sources()` which expects `{pmid, nct_id, doi, title, year}`. Every entry silently fails the `if not primary_id` check. Zero studies extracted.
3. **Flat graph model** — All edges connect to Session (star topology). Loses attribution: can't tell which specialist found which entity.
4. **No visual hierarchy** — Force-directed layout treats all nodes equally. No semantic grouping.
5. **No edge differentiation** — All edges render as identical gray lines.
6. **Markov clustering useless** — Star topologies produce one cluster.

## Solution

### Backend: New Graph Model (B1-B4)

#### B1. Edge Model Change

Old (lossy star):
```
Session ──QUERIED──► Specialist
Session ──CITED────► Study
Session ──ABOUT────► Disease/Drug/Gene
```

New (pipeline DAG):
```
Session ──QUERIED──────► Specialist ──FOUND──► Disease/Drug/Gene ──EVIDENCED_BY──► Study
                                                                   Study ──CITED──► Study
```

Four edge types:
- **QUERIED** — Session → Specialist (which agents were delegated to)
- **FOUND** — Specialist → Entity (which specialist discovered which entity, using `agent_name` from source metadata)
- **EVIDENCED_BY** — Entity → Study (which studies provide evidence for that entity)
- **CITED** — Study → Study (cross-references between studies)

The `agent_name` field already exists on every source object. `graph_writer` groups sources by agent and creates `Specialist -FOUND-> Entity` edges.

#### B2. Store Raw Sources in Redis

`source_collector.py` stores the raw sources array (with pmid/nct_id/doi/title/year) to `medexpert:{sid}:evidence:published_sources_raw` alongside the existing citation_map. `graph_writer._read_session_data()` reads from this key first, falling back to citation_map.

#### B3. Explore Endpoint Returns Edges

Single-pass Cypher: collect node IDs with `WITH`, then match edges within that set.

```cypher
MATCH (n) WHERE ... RETURN n LIMIT 100
-- becomes:
MATCH (n) WHERE ...
WITH collect(n) AS nodes, collect(elementId(n)) AS ids
UNWIND nodes AS n
OPTIONAL MATCH (a)-[r]-(b)
WHERE elementId(a) IN ids AND elementId(b) IN ids
RETURN n, r, a, b LIMIT 500
```

Edge limit of 500 prevents fan-out on shared hubs.

#### B4. CITED Stub Nodes

When a `[[pmid:X]]` reference in findings text points to a PMID not in the source list, create a stub Study node with `partial: true` property. Frontend renders these as dashed-stroke hollow circles.

### Frontend: SVG Columnar Renderer (F1-F10)

#### F1. Replace Cytoscape with SVG

Drop `cytoscape` and `cytoscape-cose-bilkent` dependencies. New `GraphCanvas.tsx` using pure SVG with React state. Four-column lane layout:

```
SESSIONS → SPECIALISTS → SHARED ENTITIES → STUDIES
```

Column lane backgrounds (alternating), dashed center lines, column headers with flow arrows.

#### F2. Light Background Color Scheme

| Element | Color |
|---------|-------|
| Canvas background | `#f8fafc` (slate-50) |
| Lane background | `rgba(241,245,249,0.6)` (slate-100) |
| Lane dividers | `#e2e8f0` (slate-200, dashed) |
| Column headers | `#64748b` (slate-500) |

Node styles:

| Type | Fill | Stroke | Text |
|------|------|--------|------|
| Session | session-specific (purple/cyan/amber) | white inner ring | white |
| Specialist | `#f8fafc` | `#06b6d4` (cyan) | `#0e7490` (cyan-700) |
| Entity | `#f8fafc` | `#64748b` (slate) | `#1e293b` (slate-800) |
| Study | `#f8fafc` | `#f59e0b` (amber) | `#b45309` (amber-700) |
| Study (partial) | transparent | `#f59e0b` dashed | `#b45309` dimmed |

Shared hub entities get multi-colored ring segments showing which sessions they belong to.

#### F3. Edge Styles

| Type | Color | Dash | Width | Meaning |
|------|-------|------|-------|---------|
| QUERIED | `#22d3ee` cyan | solid | 2px | Session → Specialist |
| FOUND | `#a78bfa` violet | solid | 2px | Specialist → Entity |
| EVIDENCED_BY | `#94a3b8` slate | `6,3` | 1.5px | Entity → Study |
| CITED | `#f59e0b` amber | `4,4` | 1.8px | Study → Study |

Curved edges with arrowhead markers. Glow bloom on hover/selection.

#### F4. Node Sizing

- Session: fixed 36px (largest, anchor)
- Specialist: fixed 24px
- Entity: `20 + min(degree * 3, 16)` — shared hubs grow
- Study: `16 + min(degree * 1.5, 6)` — smaller
- Degree badge (top-right circle) on entities with degree > 3

#### F5. Smart Labels

- Session: "Session A\n{truncated query}" (multiline)
- Specialist: agent name without "Specialist" suffix
- Entity: full name
- Study: "PMID:XXXXX" or "NCTXXXXXXXX"

#### F6. Interactions

- **Drag** any node to rearrange (SVG point transform)
- **Click** node to focus (dim unrelated nodes/edges, highlight direct connections)
- **Click** canvas background to deselect
- **Hover** legend edge type to filter (dim all other edge types)
- **Node hover** glow effect

#### F7. Legend

Edge type legend in header row (interactive — hover to filter). Session color key below. "Node size = connections" hint. Part of GraphCanvas, not a separate component.

#### F8. Layout Algorithm

Column-based weighted-average-Y:
1. Place Session nodes evenly spaced in column 0
2. For each subsequent column, each node's Y = weighted average of connected nodes in the previous column
3. Overlap resolution sweep: sort by Y, push apart any pair closer than `2 * maxRadius + 20px`
4. Optional second pass to re-center within canvas bounds

#### F9. Unified Mode

No session/explore tab toggle. Single explore mode is the only mode.
- URL param `?session=X` pre-selects that session on load (triggers click-to-focus dimming from F6)
- "View in Graph" button on chat messages navigates to `/knowledge-graph?session=X`

#### F10. Detail Panel + NLQ

Keep `EntityDetailPanel` (right sidebar on node click) and `GraphQueryBar` (NLQ at bottom).
- Study nodes: clickable PubMed/ClinicalTrials.gov links when pmid/nct_id present
- Session nodes: show query text and domain
- Filter out empty string properties

## Scale Considerations

SVG sufficient for <150 nodes (typical: 3-5 sessions = 30-60 nodes). Canvas fallback or viewport culling is a v2 concern.

## Files Changed

| File | Change |
|------|--------|
| `medexpert/src/lifesci_tools/source_collector.py` | Store raw sources in Redis (B2) |
| `medexpert/src/lifesci_tools/graph_writer.py` | New edge model: FOUND, EVIDENCED_BY, CITED stubs (B1, B4) |
| `medexpert/src/mcp_servers/knowledge_graph/server.py` | Update `get_session_graph` Cypher for new edge types |
| `medexpert/src/graph_api/router.py` | Explore endpoint fetches edges (B3) |
| `client/webui/frontend/src/lib/components/knowledgeGraph/CytoscapeGraph.tsx` | DELETE — replaced by GraphCanvas |
| `client/webui/frontend/src/lib/components/knowledgeGraph/GraphCanvas.tsx` | NEW — SVG columnar renderer (F1-F9) |
| `client/webui/frontend/src/lib/components/knowledgeGraph/KnowledgeGraphPage.tsx` | Wire GraphCanvas, remove Cytoscape imports, unified mode |
| `client/webui/frontend/src/lib/components/knowledgeGraph/GraphLegend.tsx` | DELETE — legend moves into GraphCanvas header |
| `client/webui/frontend/src/lib/hooks/useGraphData.ts` | Adapt data transform for new edge types, remove Cytoscape element conversion |
| `client/webui/frontend/src/lib/components/knowledgeGraph/EntityDetailPanel.tsx` | PubMed/CT.gov links, filter empty props |
| `client/webui/frontend/src/lib/types/fe.ts` | Add FOUND, EVIDENCED_BY edge types |
| `client/webui/frontend/package.json` | Remove cytoscape + cytoscape-cose-bilkent deps |
| `medexpert/tests/unit/test_graph_writer.py` | Update tests for new edge model |
| `medexpert/tests/unit/test_knowledge_graph_server.py` | Update tests for new Cypher |

## Test Plan

- Unit tests for new `build_session_cypher()` edge model (FOUND, EVIDENCED_BY, CITED)
- Unit tests for stub Study node creation (`partial: true`)
- Unit test for explore endpoint edge query (no more empty edges)
- Unit test for raw sources Redis storage
- Frontend vitest for GraphCanvas rendering, layout algorithm, interactions
- Manual smoke test: run research query, verify graph shows pipeline flow
