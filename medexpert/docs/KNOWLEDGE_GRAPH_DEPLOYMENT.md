# Knowledge Graph Integration — Deployment Guide

This document provides everything an agent needs to deploy the Knowledge Graph feature into local and Cloud Run environments.

## What Was Built

The Knowledge Graph feature adds a Memgraph-backed graph persistence layer to MedExpert's research pipeline. At protocol step 6 (PERSIST), the `graph_writer` tool extracts biomedical entities (diseases, drugs, genes, studies, specialists) from session data and writes them as nodes/edges to Memgraph. A frontend page (`/knowledge-graph`) visualizes session graphs and the persistent knowledge base using Cytoscape.js.

### Component Inventory

| Component | File | Port | Purpose |
|-----------|------|------|---------|
| **MCP Server** | `medexpert/src/mcp_servers/knowledge_graph/server.py` | 9011 | 5 tools: query, relationships, session graph, stats, NLQ-to-Cypher |
| **graph_writer** | `medexpert/src/lifesci_tools/graph_writer.py` | — | DynamicTool called at PERSIST step, writes entities to Memgraph |
| **REST Router** | `medexpert/src/graph_api/router.py` | — | 6 endpoints proxying to MCP tools (mounted at `/api/v1/graph`) |
| **Frontend Page** | `client/webui/frontend/src/lib/components/knowledgeGraph/` | — | Cytoscape.js visualization, NLQ bar, entity detail panel |
| **Feature Flag** | `medexpert/configs/gateways/webui.yaml` | — | `knowledgeGraph: true` under `frontend_feature_enablement` |

### Data Flow

```
Research Session (STEP 6 PERSIST)
  → graph_writer reads Redis (medexpert:{sid}:*) for specialist data
  → Regex extracts PMIDs, NCT IDs, inline [[disease:X]] tags
  → Cypher MERGE writes Session/Specialist/Study/Disease/Drug/Gene nodes
  → Memgraph stores at bolt://localhost:7687

Frontend /knowledge-graph page
  → GET /api/v1/graph/session/{id}  → graph_api/router.py  → knowledge_graph MCP tool
  → GET /api/v1/graph/explore       → graph_api/router.py  → knowledge_graph MCP tool
  → POST /api/v1/graph/nlq          → graph_api/router.py  → litellm → Cypher → Memgraph
  → Cytoscape.js renders nodes/edges
```

---

## Current Deployment State

### What Already Exists (no changes needed)

| Resource | Standard Cloud Run | Multi-Model Cloud Run |
|----------|-------------------|----------------------|
| Memgraph sidecar | YES (port 7687, 2 CPU/4Gi) | YES (port 7687, 2 CPU/4Gi) |
| `mcp-memgraph` pip package | YES (Dockerfile line 18) | YES (Dockerfile line 18) |
| `MEMGRAPH_URL` env var | `bolt://localhost:7687` | `bolt://localhost:7687` |
| `MEMGRAPH_USER` / `PASSWORD` | `memgraph` / `medexpert-demo` | `memgraph` / `medexpert-demo` |
| Container dependency annotation | YES | YES |
| Memgraph startup probe | TCP 7687, 5s interval | TCP 7687, 5s interval |

### What Needs to Be Added

#### 1. Start the Knowledge Graph MCP Server (Port 9011)

Currently, port 9011 is **skipped** in both `start_medliaison.sh` and `start_multimodel.sh` (listed as "Tier 2 degraded — skipped" because there was no backing database). Now that Memgraph is the backing store, it must be started.

**Files to modify:**

`medexpert/infra/start_medliaison.sh` — Add to the MCP server startup block (after the 13 existing servers, before the health check):
```bash
# Knowledge Graph MCP server (Memgraph-backed, port 9011)
echo "[MedLiaison] Starting Knowledge Graph MCP server..."
python -m mcp_servers.knowledge_graph.server &
MCP_PIDS+=($!)
```

And add port 9011 to the health check loop:
```bash
REQUIRED_MCP_PORTS="9001 9002 9003 9004 9005 9006 9007 9008 9009 9010 9011 9016 9017 9018"
```

`medexpert/infra/start_multimodel.sh` — Same changes.

#### 2. Add `neo4j` Python Driver to Dependencies

The `knowledge_graph/server.py` uses `from neo4j import GraphDatabase` (Memgraph is bolt-compatible). This is currently lazy-imported and not in `pyproject.toml`.

**File to modify:** `medexpert/pyproject.toml`
```toml
dependencies = [
    # ... existing deps ...
    "neo4j>=5.0,<6.0",
]
```

**Also add to Dockerfiles** (both `Dockerfile.medliaison` and `Dockerfile.multimodel`):
```dockerfile
RUN pip install --no-cache-dir neo4j mcp-memgraph
```
(mcp-memgraph is already there; just add neo4j alongside it)

#### 3. Startup Probe Budget

Adding port 9011 increases startup time by ~5-10 seconds. The current budget:
- Standard: 60s initial + 40×15s = 660s total — **plenty of room**
- Multi-model: 90s initial + 60×15s = 990s total — **plenty of room**

No startup probe changes needed.

#### 4. Frontend Build

The Knowledge Graph page uses `cytoscape` and `cytoscape-cose-bilkent` npm packages (already added to `package.json`). The Cloud Build step that builds the frontend (`cd client/webui/frontend && npm install && npm run build-package`) will automatically pick these up.

No Dockerfile or Cloud Build changes needed for the frontend.

#### 5. Feature Flag (Already Set)

`medexpert/configs/gateways/webui.yaml` already has `knowledgeGraph: true`. The Knowledge Graph nav item will appear automatically.

---

## Local Development Setup

### Option A: Without Memgraph (graceful degradation)

No changes needed. The knowledge_graph MCP server starts on port 9011 via `start_mcp_servers.sh` and returns `{success: false, error_category: "service_unavailable"}` for all queries. The frontend shows "Knowledge graph not available" messaging.

### Option B: With Memgraph (full functionality)

**Add Memgraph to docker-compose.yaml:**
```yaml
memgraph:
  image: memgraph/memgraph:latest
  ports:
    - "7687:7687"
  environment:
    MEMGRAPH_USER: memgraph
    MEMGRAPH_PASSWORD: medexpert-demo
  command: ["--also-log-to-stderr", "--log-level=WARNING"]
```

**Add env vars to .env:**
```bash
MEMGRAPH_URL=bolt://localhost:7687
MEMGRAPH_USER=memgraph
MEMGRAPH_PASSWORD=medexpert-demo
```

**Install neo4j driver:**
```bash
cd medexpert && pip install neo4j
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MEMGRAPH_URL` | Yes (for graph features) | `""` (falls back to `NEO4J_URI`) | Bolt connection string |
| `MEMGRAPH_USER` | No | `memgraph` | Memgraph username |
| `MEMGRAPH_PASSWORD` | No | `""` | Memgraph password |
| `MEMGRAPH_RO_USER` | No | Falls back to `MEMGRAPH_USER` | Read-only user (defense-in-depth) |
| `MEMGRAPH_RO_PASSWORD` | No | Falls back to `MEMGRAPH_PASSWORD` | Read-only password |
| `NLQ_MODEL` | No | `vertex_ai/gemini-2.5-flash` | LLM for NLQ-to-Cypher translation |
| `VERTEX_PROJECT` | No | `""` | Vertex AI project for NLQ (falls back to `VERTEXAI_PROJECT`) |
| `VERTEX_LOCATION` | No | `us-central1` | Vertex AI region for NLQ |
| `REDIS_URL` | Yes | `redis://localhost:6379` | Redis for graph_writer session data reads |
| `KNOWLEDGE_GRAPH_MCP_URL` | No | `http://localhost:9011` | (unused after F1 fix — router uses direct imports) |

---

## Security Notes

- **Cypher injection prevention**: NLQ-generated Cypher is validated against a read-only allowlist (`MATCH`, `RETURN`, `OPTIONAL MATCH`, `WITH`, `UNWIND`, `CALL`). Mutation keywords (`CREATE`, `MERGE`, `SET`, `DELETE`, `DROP`, `ALTER`, `GRANT`, `REVOKE`, `LOAD`, `FOREACH`) are rejected.
- **NLQ rate limiting**: 5 requests per session per minute (in-memory, resets on restart).
- **graph_writer never raises**: Always returns structured error on failure — PERSIST step is never blocked.
- **Input sanitization**: All queries go through `sanitize_query()` from `_security.py`, plus `_sanitize_cypher_param()` for defense-in-depth on parameterized values.
- **Memgraph credentials**: Currently hardcoded as `memgraph`/`medexpert-demo` in service YAMLs. For production, use Secret Manager references.

---

## Indexes

The MCP server creates these indexes at startup (idempotent):
```cypher
CREATE INDEX ON :Session(session_id);
CREATE INDEX ON :Disease(name);
CREATE INDEX ON :Drug(name);
CREATE INDEX ON :Gene(name);
CREATE INDEX ON :Study(pmid);
CREATE INDEX ON :Specialist(name);
```

---

## Graph Schema

### Node Types
| Label | Key Property | Other Properties |
|-------|-------------|-----------------|
| Session | `session_id` | query, domain, created_at |
| Specialist | `name` | — |
| Study | `pmid` or `nct_id` or `doi` | title, year, created_at |
| Disease | `name` | created_at |
| Drug | `name` | created_at |
| Gene | `name` | created_at |

### Edge Types
| Type | From | To | Meaning |
|------|------|-----|---------|
| QUERIED | Session | Specialist | Session used this specialist |
| CITED | Session | Study | Session cited this study |
| ABOUT | Session | Disease/Drug/Gene | Session was about this entity |

---

## Test Coverage

| Suite | Count | Covers |
|-------|-------|--------|
| `test_graph_writer.py` | 24 | Entity extraction, Cypher generation, MERGE key selection, Redis prefix, failure handling |
| `test_knowledge_graph_server.py` | 31 | Cypher validation (12 mutation keywords), tool degradation, wildcard browse, sanitization |
| `test_degraded_servers.py` | 5 | Structured error format when Memgraph unavailable |
| Frontend vitest | 7 | Page rendering, stats, tabs, filters, NLQ bar |
| **Total** | **67** | |

Run backend tests: `cd medexpert && .venv/Scripts/python.exe -m pytest tests/unit/test_graph_writer.py tests/unit/test_knowledge_graph_server.py -v`

---

## Verification Checklist

After deployment, verify:

- [ ] Port 9011 responds: `curl http://localhost:9011/sse` (SSE stream opens)
- [ ] Memgraph connected: Check logs for `"Connected to Memgraph at bolt://localhost:7687"`
- [ ] Indexes created: Check logs for `"Ensured 6 indexes on Memgraph"`
- [ ] Stats endpoint: `curl http://localhost:8080/api/v1/graph/stats` returns `{success: true, total_nodes: 0, ...}`
- [ ] Run a research session → check `curl http://localhost:8080/api/v1/graph/session/{session_id}` returns nodes
- [ ] Frontend: Navigate to `/#/knowledge-graph` → "Knowledge" nav item visible, page loads
- [ ] NLQ: Type "What studies exist?" → Cypher generated and results shown
- [ ] Feature flag off: Set `knowledgeGraph: false` in webui.yaml → nav item hidden

---

## Known Limitations (Phase 3 follow-ups)

1. **No LLM-assisted entity extraction** — regex only. Entities not tagged with `[[disease:X]]` inline markers are missed.
2. **No cross-session specialist querying** — specialists don't have knowledge_graph MCP tools in their YAML configs yet.
3. **No entity deduplication** — "aspirin" and "Aspirin" create separate Drug nodes.
4. **No graph pruning** — old sessions accumulate indefinitely. Align with cold store's 90-day retention policy.
5. **Ephemeral Memgraph** — Cloud Run sidecar has no persistent volume. Graph resets on container restart. For persistence, use Memgraph Cloud or a mounted volume.
