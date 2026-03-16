# Knowledge Graph → RAG Citation Adapter Design

**Date:** 2026-03-13
**Status:** Draft (Rev 3 — addresses architect review findings F1-F15)
**Approach:** Dedicated `kg_search` DynamicTool + Frontend Citation Bridge

## Problem

The Knowledge Graph (Memgraph) stores biomedical entities and relationships from prior research sessions, but its query results never reach the RAG citation pipeline. Two gaps exist:

1. **Agent-side**: No tool converts KG query results into RAG sources. The orchestrator cannot cite prior knowledge graph findings in research answers.
2. **Frontend-side**: The citation regex only recognizes `s{turn}r{index}` and `research{N}` patterns. KG visualization Study nodes have no link back to the Sources panel.

## Solution Overview

### Part A — Backend: `kg_search` DynamicTool

New file: `medexpert/src/lifesci_tools/kg_search.py`

A DynamicTool that wraps the Knowledge Graph MCP server functions and converts Memgraph node results into RAG-compatible citations.

**Tool name:** `kg_search`

**Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Search query (entity name, disease, drug, etc.) |
| `entity_types` | array[string] | no | Filter by node labels: Disease, Drug, Gene, Study |
| `session_id` | string | no | Retrieve a specific prior session's graph |
| `limit` | int | no | Max results (default 10, max 50) |

**Implementation details:**
- Imports KG MCP server functions directly via Python import (same pattern as `graph_api/router.py`), not HTTP. Unwraps FastMCP `FunctionTool` via `.fn` attribute.
- Two query modes:
  - **Entity search** (default): Calls `query_knowledge_graph()` with query + entity_types
  - **Session graph**: When `session_id` provided, calls `get_session_graph()` and extracts Study/Entity nodes
- Converts each KG node into a RAG source via `create_rag_source()`:
  - **Citation IDs:** `kg0r0`, `kg0r1`, `kg0r2`, ... (distinct `kg` namespace prevents collision with `s0rN` from source_collector)
  - **searchType:** `"kb_search"` (already defined in frontend `fe.ts` types but never used — this activates it)
  - **Study nodes:** URL constructed from `properties.pmid` / `properties.nct_id` / `properties.doi` using same URL templates as source_collector
  - **Disease/Drug/Gene nodes:** `content_preview` from description + connected study count; no external URL (sourceUrl left empty)
  - **metadata** includes: `labels` (node labels), `source: "knowledge_graph"`, `evidence_grade` (Study→`node.properties.get("evidence_grade", "Moderate")`, others→empty), `node_id` (Memgraph element_id for frontend bridge)
- Bundles all sources via `create_rag_search_result(searchType="kb_search")`
- Returns same shape as source_collector: `{status, formatted_results, rag_metadata, valid_citation_ids, num_sources}`
- Graceful degradation: Memgraph unavailable → `{status: "kg_unavailable", rag_metadata: null}`, no exception raised

**Protocol integration:**
- Add `kg_search` to `ALLOWED_TOOLS_PER_STEP` at step 1 (PLAN) only. Step 0 is reserved for PHI safety + session bootstrap — keep it lean. At step 1, the session is seeded and strategies are loaded, giving the LLM full context for the KG query.
- No step advancement trigger — KG search is informational, does not advance protocol
- Orchestrator YAML (all 3 variants): Add instruction in STEP 1: "Before calling `query_decomposer`, call `kg_search` with the user's question to check for prior knowledge in the graph. If results found, include them in your planning context."

### Part B — Frontend: Citation Regex + Parser Updates

File: `client/webui/frontend/src/lib/utils/citations.ts`

**All four regex patterns updated** (adding `kg\d+r\d+` to each alternation group):

```typescript
// 1. Single citation pattern
export const CITATION_PATTERN = /\[?\[cite:(s\d+r\d+|research\d+|kg\d+r\d+)\]\]?/g;

// 2. Multi-citation pattern (comma-separated)
export const MULTI_CITATION_PATTERN = /\[?\[cite:((?:s\d+r\d+|research\d+|kg\d+r\d+)(?:\s*,\s*(?:cite:)?(?:s\d+r\d+|research\d+|kg\d+r\d+))+)\]\]?/g;

// 3. Individual citation extraction from comma list
export const INDIVIDUAL_CITATION_PATTERN = /(?:cite:)?(s\d+r\d+|research\d+|kg\d+r\d+)/g;

// 4. Cleanup regex (already generic — no change needed)
export const CLEANUP_REGEX = /\[?\[cite:[^\]]+\]\]?/g;
```

**Parser changes:**

```typescript
// New regex for kg citation IDs
const KG_CITATION_ID_PATTERN = /^kg(\d+)r(\d+)$/;
//                                   ^      ^
//                              group 1    group 2
//                            (turn, unused) (result index → sourceId)

// Extended Citation.type union:
type: "search" | "research" | "kg"
```

`parseCitationId()` gets a third branch after search and research:
```typescript
// Try kg format: kg{turn}r{index}
const kgMatch = citationId.match(KG_CITATION_ID_PATTERN);
if (kgMatch) {
    return {
        type: "kg",
        sourceId: parseInt(kgMatch[2], 10),  // r{index}, NOT kg{turn}
    };
}
```

**Citation display number disambiguation (F2):**

`getCitationNumber()` returns different display formats by type:
- `"search"` citations: `sourceId + 1` → displays as `[1]`, `[2]`, ...
- `"research"` citations: `sourceId + 1` → displays as `[1]`, `[2]`, ...
- `"kg"` citations: `sourceId + 1` → displays as `[K1]`, `[K2]`, ...

The "K" prefix is applied in the citation chip component (`CitationMarker` or equivalent), not in `getCitationNumber()` itself. This avoids changing the number calculation and keeps the prefix as a rendering concern:
```typescript
// In citation chip render:
const displayText = citation.type === "kg"
    ? `K${getCitationNumber(citation)}`
    : `${getCitationNumber(citation)}`;
```

**Tooltip:** `getCitationTooltip()` adds KG-specific formatting:
```typescript
// In getCitationTooltip(), add before the final fallback:
const isKg = citation.type === "kg";
if (isKg) {
    const labels = citation.source?.metadata?.labels;
    const name = citation.source?.metadata?.title || citation.source?.filename;
    const sourceUrl = citation.source?.sourceUrl || citation.source?.url;
    if (sourceUrl) {
        // Study node with URL — show title + URL (same as web search)
        return name ? `${name}\n${sourceUrl}` : sourceUrl;
    }
    // Entity node — show labels + name
    const labelStr = Array.isArray(labels) ? labels.join(", ") : "Entity";
    return `Knowledge Graph: ${labelStr} — ${name || "Unknown"}`;
}
```

### Part B.2 — Citation.tsx Private Regex Copies (Critical — F11)

**File:** `client/webui/frontend/src/lib/components/chat/Citation.tsx`

`Citation.tsx` contains **private copies** of citation parsing logic that must also be updated:

1. **`COMBINED_CITATION_PATTERN`** (line ~481): Add `|kg\d+r\d+` to both the single and repeat alternation groups:
```typescript
// Before:
const COMBINED_CITATION_PATTERN = /\[?\[cite:((?:s\d+r\d+|research\d+)(?:\s*,\s*(?:cite:)?(?:s\d+r\d+|research\d+))*)\]\]?/g;
// After:
const COMBINED_CITATION_PATTERN = /\[?\[cite:((?:s\d+r\d+|research\d+|kg\d+r\d+)(?:\s*,\s*(?:cite:)?(?:s\d+r\d+|research\d+|kg\d+r\d+))*)\]\]?/g;
```

2. **`parseCitationIdLocal`** (line ~429): Add third branch for `kg` pattern:
```typescript
const kgMatch = id.match(/^kg(\d+)r(\d+)$/);
if (kgMatch) {
    return { type: "kg" as const, sourceId: parseInt(kgMatch[2], 10) };
}
```

3. **`parseMultiCitationIds`** (line ~455): Expand return type to include `"kg"`:
```typescript
type CitationType = "search" | "research" | "kg";
```

4. **Citation chip rendering**: Apply the same "K" prefix for `kg` type citations:
```typescript
const displayText = parsed.type === "kg"
    ? `K${parsed.sourceId + 1}`
    : `${parsed.sourceId + 1}`;
```

**DRY note:** These are duplicates of logic in `citations.ts`. The ideal fix is to refactor `Citation.tsx` to import from `citations.ts`, but that is a separate refactoring concern — for this feature, update both copies.

### Part B.3 — Advisory Improvements (F12-F13)

- **F12:** `removeCitationMarkers()` at `citations.ts:192` uses `CITATION_PATTERN`. After the regex update it will correctly handle `kg` citations. For additional robustness, the implementing agent should switch it to use `CLEANUP_REGEX` (which is already a generic `[^\]]+` catch-all). This is a one-line change.
- **F13:** Tooltip code snippet now included above in Part B.

### Part C — Frontend: KG Visualization → Citation Bridge

**Prop-threading mechanism:** `EntityDetailPanel` is a pure presentation component — it receives `nodeData` as props and has no access to `ragData` or ChatContext. The bridge is implemented via callback props:

```typescript
// EntityDetailPanel.tsx — new optional props
interface EntityDetailPanelProps {
    nodeData: GraphNode;
    onViewSource?: (identifier: { type: "pmid" | "nct_id" | "doi"; value: string }) => void;
    onSearchSources?: (entityName: string) => void;
}
```

The parent `KnowledgeGraphPage` implements these callbacks using `useChatContext()`:

```typescript
// KnowledgeGraphPage.tsx
const { ragData, openSidePanelTab } = useChatContext();

const handleViewSource = useCallback(({ type, value }) => {
    // Search ragData for matching sourceUrl
    const match = ragData.flatMap(r => r.sources)
        .find(s => s.sourceUrl?.includes(value));
    if (match) {
        openSidePanelTab("rag");  // Opens Sources panel
        // Scroll to citation handled by existing Sources panel highlight logic
    } else {
        // Fallback: open direct URL
        const url = type === "pmid" ? `https://pubmed.ncbi.nlm.nih.gov/${value}/`
            : type === "nct_id" ? `https://clinicaltrials.gov/study/${value}`
            : `https://doi.org/${value}`;
        window.open(url, "_blank");
    }
}, [ragData, openSidePanelTab]);
```

**EntityDetailPanel rendering:**
- **Study nodes** with pmid/nct_id/doi: "View Source" button calls `onViewSource`
- **Disease/Drug/Gene nodes:** "Search Sources" button calls `onSearchSources` (filters ragData by entity name)

**Implementation notes (F14-F15):**
- **F14 (Props interface):** The existing `EntityDetailPanelProps` uses `nodeData: Record<string, unknown>`. This must be changed to `nodeData: GraphNode` (typed) for the callbacks to access `labels`, `properties.pmid`, etc. Update the import accordingly.
- **F15 (ChatProvider availability):** `KnowledgeGraphPage` does not currently import `useChatContext`. Before adding it, verify that `KnowledgeGraphPage` is rendered within the `ChatProvider` component tree. If the KG page is rendered outside the chat context (e.g., as a standalone route), wrap the `useChatContext()` call in a try/catch or use an optional context hook with a fallback that disables the bridge buttons.

### Part D — SSE Processing

**No changes needed** to `ChatProvider.tsx`. The existing `rag_metadata` handler (line 1471) processes any tool result containing `rag_metadata`. Since `kg_search` returns `rag_metadata` in the identical shape as `source_collector`, it flows through the same code path:
- `resultData.rag_metadata` detected → builds `RAGSearchResult`
- `searchType` is `"kb_search"` (not `"deep_research"`) → hits the else branch → appended to ragData
- Auto-opens Sources sidebar tab on first RAG data for the task

**Degradation safety note:** When `kg_search` returns `{rag_metadata: null}` (Memgraph unavailable / empty results), the ChatProvider's truthy check on line 1471 (`resultData.rag_metadata`) evaluates to falsy and naturally skips RAG processing. No error path is triggered, no Sources panel auto-open. The orchestrator proceeds with `query_decomposer` as if no prior KG knowledge exists.

### Part E — YAML Config + Error Recovery Changes

| File | Change |
|------|--------|
| `configs/agents/orchestrator.yaml` | Add `kg_search` to tools list. Add STEP 1 instruction: "Before calling `query_decomposer`, call `kg_search` to check prior knowledge." |
| `configs/pro/agents/orchestrator.yaml` | Same |
| `configs/opus/agents/orchestrator.yaml` | Same |
| `protocol_step_validator.py` | Add `kg_search` to step 1 (PLAN) allowed tools only |
| `error_recovery_hints.py` | Add `knowledge_graph` server entries |

**Error recovery hints** to add:
```python
("knowledge_graph", "service_unavailable"): "Knowledge graph (Memgraph) is not running. KG search results will be skipped — research continues with live sources."
("knowledge_graph", "circuit_open"): "Knowledge graph circuit breaker open. KG search skipped — research continues with live sources."
```

No specialist YAML changes — only the orchestrator queries the KG.

### Part F — Error Handling

| Scenario | Behavior |
|----------|----------|
| Memgraph unavailable | Return `{status: "kg_unavailable", rag_metadata: null}`, orchestrator skips KG enrichment |
| Empty results | Return `{status: "no_kg_results", rag_metadata: null, num_sources: 0}` |
| neo4j driver not installed | Same as Memgraph unavailable |
| Query sanitization rejects input | Return `{status: "invalid_query", error: "..."}` |

Add `knowledge_graph` recovery hint to `error_recovery_hints.py` (see Part E for exact entries).

### Part G — RAGInfoPanel Rendering for `kb_search`

The Sources panel (`RAGInfoPanel`) currently renders `web_search` and `deep_research` entries. A new section handles `kb_search`:

- **Section header:** "Knowledge Graph" with a graph/network icon (existing Lucide `Network` or `GitBranch` icon)
- **Source cards:** Same layout as web_search sources — title, content preview, evidence grade badge, URL link
- **Visual distinction:** KG source cards get a subtle left border accent (e.g., purple/indigo) to differentiate from web-search sources (no border)
- **Ordering:** KG sources appear BEFORE web-search sources in the panel (prior knowledge first, then live findings)
- **Empty state:** If `kb_search` returned 0 sources, no KG section is rendered (same as current behavior for empty web_search)

### Part H — Citation ID Collision Limitation

If `kg_search` is called multiple times per session (e.g., once at PLAN, once after partial results), all citations share the `kg0` prefix. The `r{N}` index disambiguates within a single call, but a second call's `kg0r0` would collide with the first's.

**For MVP this is acceptable** since the design restricts `kg_search` to step 1 (PLAN) — one call per session. Future work: increment the turn counter per call (e.g., `kg0rN`, `kg1rN`).

## Testing Plan

| Category | Count | Description |
|----------|-------|-------------|
| Backend `test_kg_search.py` | ~20 | Node-to-RAG conversion for all 4 node types; citation ID generation; graceful degradation; session graph mode; empty results; query sanitization |
| Frontend citation tests | ~8 | `kg0r0` regex matching; multi-citation with mixed kg/s IDs; parseCitationId for kg type; tooltip formatting |
| Frontend KG bridge tests | ~4 | Study "View Source" with/without ragData match; Disease/Drug/Gene "Search Sources" |
| Protocol validator tests | ~4 | `kg_search` allowed at step 1; rejected at steps 0, 2-6 |
| RAGInfoPanel rendering tests | ~3 | `kb_search` section header, KG source card styling, ordering before web_search |

## Data Flow

```
STEP 0 (SEED):
  Orchestrator → seed_session, phi_redactor (session bootstrap)

STEP 1 (PLAN):
  Orchestrator → kg_search(query="BRCA1 breast cancer")
    → import query_knowledge_graph from KG MCP server
    → Execute Cypher query against Memgraph
    → Convert nodes to RAG sources (kg0r0, kg0r1, ...)
    → Return {rag_metadata: RAGSearchResult(searchType="kb_search")}
    → SSE: ChatProvider detects rag_metadata → appends to ragData
    → Frontend: Sources panel shows KG-sourced citations (KG section)
  Orchestrator → query_decomposer (informed by KG results)
    → LLM: Uses [[cite:kg0r0]] markers in final answer alongside [[cite:s0r0]] markers

KG Visualization:
  User clicks Study node in graph
    → EntityDetailPanel shows "View Source" button
    → Searches ragData for matching pmid/nct_id
    → Found: opens Sources panel and highlights citation
    → Not found: opens PubMed/ClinicalTrials.gov URL directly
```

## Files Changed

| File | Action | LOC estimate |
|------|--------|-------------|
| `medexpert/src/lifesci_tools/kg_search.py` | NEW | ~180 |
| `medexpert/tests/unit/test_kg_search.py` | NEW | ~300 |
| `medexpert/src/lifesci_tools/protocol_step_validator.py` | EDIT | ~2 lines (add `kg_search` to step 1) |
| `medexpert/configs/agents/orchestrator.yaml` | EDIT | ~10 lines |
| `medexpert/configs/pro/agents/orchestrator.yaml` | EDIT | ~10 lines |
| `medexpert/configs/opus/agents/orchestrator.yaml` | EDIT | ~10 lines |
| `client/webui/frontend/src/lib/utils/citations.ts` | EDIT | ~30 lines (4 regex + parser + tooltip + display) |
| `client/webui/frontend/src/lib/components/chat/Citation.tsx` | EDIT | ~20 lines (private regex copies + chip render) |
| `client/webui/frontend/src/lib/utils/__tests__/citations.test.ts` | EDIT | ~60 lines |
| `client/webui/frontend/src/components/KnowledgeGraph/EntityDetailPanel.tsx` | EDIT | ~40 lines (callback props) |
| `client/webui/frontend/src/components/KnowledgeGraph/KnowledgeGraphPage.tsx` | EDIT | ~20 lines (implement callbacks) |
| `client/webui/frontend/src/components/RAGInfoPanel.tsx` (or equivalent) | EDIT | ~30 lines (kb_search section) |
| `medexpert/src/lifesci_tools/error_recovery_hints.py` | EDIT | ~5 lines |

## Non-Goals

- Not adding KG search to specialists (only orchestrator)
- Not changing the graph_writer (PERSIST step) — it continues writing to KG as before
- Not adding a new SSE event type — reuses existing rag_metadata flow
- Not changing source_collector — KG sources live in separate `kg` citation namespace
