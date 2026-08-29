<h1 align="center">MedExpert</h1>
<h3 align="center">Multi-agent deep research platform for life sciences</h3>

<p align="center">
  <a href="https://medexpert-v2-534348290993.us-central1.run.app"><strong>Live Demo</strong></a> &middot;
  <a href="#screenshots">Screenshots</a> &middot;
  <a href="#architecture">Architecture</a> &middot;
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#research-protocol">Research Protocol</a> &middot;
  <a href="#development">Development</a>
</p>

---

MedExpert is a multi-agent AI system that answers complex medical and scientific questions by coordinating 13 specialized agents across a streamlined 7-step research protocol with parallel specialist delegation, plus a separate triage pipeline. It searches PubMed, ClinicalTrials.gov, OpenFDA, CDC, genomic databases, and 10+ other biomedical sources, then synthesizes evidence-graded answers with full citations.

Every answer goes through a Generator-Verifier-Reviser (GVR) loop: the orchestrator synthesizes a report, a verifier agent (using a stronger model at low temperature) fact-checks each claim against its cited sources, and a reviser corrects any issues before the answer reaches the user.

Built on the **Solace Agent Mesh (SAM)** framework for event-driven, broker-based agent communication using the A2A protocol.

---

## Screenshots

### Chat Interface

The main chat view with message input, agent selector, and collapsible side panel.

<p align="center">
  <img src="docs/screenshots/01-chat-welcome.png" alt="Chat interface - dark mode" width="720" />
</p>

### Side Panel Tabs

The right-side panel provides five tabs for inspecting research activity in real time.

<table>
  <tr>
    <td align="center"><strong>Files</strong><br/>Uploaded documents and generated artifacts<br/><img src="docs/screenshots/02-side-panel-files.png" alt="Files tab" width="360" /></td>
    <td align="center"><strong>Activity</strong><br/>Agent task workflow and step details<br/><img src="docs/screenshots/03-side-panel-activity.png" alt="Activity tab" width="360" /></td>
  </tr>
  <tr>
    <td align="center"><strong>Data Sources</strong><br/>MCP server health and search tool status<br/><img src="docs/screenshots/04-side-panel-data.png" alt="Data Sources tab" width="360" /></td>
    <td align="center"><strong>Memory</strong><br/>Session memory plane state<br/><img src="docs/screenshots/05-side-panel-memory.png" alt="Memory tab" width="360" /></td>
  </tr>
  <tr>
    <td align="center" colspan="2"><strong>Performance</strong><br/>Self-evolving prompt metrics<br/><img src="docs/screenshots/06-side-panel-perf.png" alt="Performance tab" width="360" /></td>
  </tr>
</table>

### Agent Configs

View discovered agents and their configuration.

<p align="center">
  <img src="docs/screenshots/07-agent-mesh.png" alt="Agent Mesh page" width="720" />
</p>

### Projects & Prompts

Manage reusable project contexts and prompt templates.

<table>
  <tr>
    <td align="center"><img src="docs/screenshots/08-projects.png" alt="Projects page" width="360" /></td>
    <td align="center"><img src="docs/screenshots/09-prompts.png" alt="Prompts page" width="360" /></td>
  </tr>
  <tr>
    <td align="center"><strong>Projects</strong></td>
    <td align="center"><strong>Prompts</strong></td>
  </tr>
</table>

### Light Mode

<p align="center">
  <img src="docs/screenshots/10-chat-light-mode.png" alt="Chat interface - light mode" width="720" />
</p>

---

## Architecture

```
User Query
    |
    +---> Triage Pipeline (symptom-based queries)
    |       |
    |       +---> Triage Intake (symptom collection, specialist panel)
    |       +---> Triage Orchestrator (evaluation, consensus, next-best-action)
    |       |
    |       v
    |     Care pathway recommendation
    |
    +---> Research Pipeline (complex medical/scientific queries)
            |
            v
        Orchestrator (7-step protocol, parallel delegation)
            |
            +---> 8 Specialist Agents (literature, clinical trials, drug,
            |     regulatory, epidemiology, genomics, environmental, provider intel)
            |
            +---> 18 MCP Servers (PubMed, ClinicalTrials.gov, OpenFDA, CDC,
            |     SEER, EPA, ClinVar, Census SDOH, NHS 111, BMA Library, ...)
            |
            v
        GVR Loop: Generate --> Verify --> Revise
            |
            v
        Evidence-graded report with citations
```

### How the Agents Reason

MedExpert agents don't just call APIs — they implement structured reasoning patterns that mirror how medical researchers think.

**Orchestrator: Protocol-Driven Decomposition**

The orchestrator uses a prescriptive 7-step protocol (not free-form chain-of-thought) to ensure systematic coverage. It decomposes complex questions into domain-routed sub-questions using keyword heuristics with multi-domain routing (primary + secondary specialists per sub-question), then delegates to all specialists in a single parallel batch. This is closer to a research methodology than a chatbot prompt.

Key reasoning mechanisms:
- **Multi-domain query decomposition** — Breaks multi-faceted questions into sub-questions, each routed to a primary specialist plus 1-2 secondary specialists for multi-source evidence (e.g., "What are the treatments for endometriosis?" routes to LiteratureSpecialist + ClinicalTrialsSpecialist + DrugSpecialist)
- **Parallel specialist delegation** — All specialist agents are called in a single LLM turn, executing concurrently. This reduces LLM call overhead from ~60-80 to ~20-25 calls per research session
- **Structured specialist responses** — Specialists return JSON-formatted summaries (findings, sources, confidence, gaps) instead of prose, reducing orchestrator context bloat
- **Learned routing** — Seeds each session with historical intelligence from the cold store (which specialists and sources worked well for similar queries in the past, calibrated per-domain specialist weights, source reliability scores)

**Specialists: Evidence-First Retrieval**

Each specialist follows a strict evidence-first workflow: search external sources via MCP tools, grade the evidence using LLM-augmented GRADE methodology, store graded evidence in the shared memory plane, then synthesize. Specialists never answer from LLM memory alone — every claim must trace to a retrieved source.

The Literature Specialist, for example:
1. Searches PubMed for peer-reviewed articles
2. Retrieves full abstracts for top results
3. Grades each using evidence_grader — LLM contextual assessment (blinding, sample size, funding bias) with heuristic fallback
4. Stores graded evidence in Redis memory plane
5. Returns structured summary with PMIDs

**Verifier: Independent Fact-Checking**

The verifier uses gemini-3.7-flash at very low temperature (0.1) to independently assess the draft report. Its reasoning is three-stage:

1. **Structural validation** — Checks that every `[[cite:X]]` marker points to an actual source in the citation list
2. **Entailment analysis** — For each claim, uses LLM-based entailment classification (ENTAILS / CONTRADICTS / NEUTRAL) to assess whether the cited source actually supports the claim
3. **Keyword fallback** — If LLM entailment is ambiguous, falls back to Jaccard similarity (keyword overlap >= 0.15 = supported)

The verifier also cross-references against the raw evidence in the memory plane and historical source reliability from the cold store. It never modifies the report — it only produces a pass/fail assessment.

**Memory Plane: Shared Reasoning State**

All agents share a Redis-backed memory plane scoped by session. This creates a shared "working memory" that enables reasoning across agent boundaries:
- Specialists write evidence → orchestrator reads to detect gaps
- Orchestrator writes coverage metrics → verifier reads to calibrate strictness
- Verifier writes verdict → orchestrator reads to decide revision
- All signals auto-flush to SQLite cold store for cross-session learning
- User feedback (thumbs up/down) flows via broker to cold store, feeding specialist calibration and prompt evolution

### Agents

| Agent | Role | Model |
|-------|------|-------|
| **Orchestrator** | Coordinates 7-step protocol with parallel delegation and GVR loop | gemini-3.7-flash (temp 0.2) |
| **8 Specialists** | Domain-specific research (literature, drugs, trials, etc.) | gemini-3.7-flash (temp 0.3) |
| **Verifier** | Fact-checks claims against cited sources | gemini-3.7-flash (temp 0.1) |
| **Reviser** | Surgically corrects issues flagged by verifier | gemini-3.7-flash (temp 0.3) |
| **Triage Intake** | Symptom collection, specialist panel consultation, care routing | gemini-3.7-flash (temp 0.3) |
| **Triage Orchestrator** | Coordinates triage evaluation, consensus building, next-best-action | gemini-3.7-flash (temp 0.2) |

### Data Sources (18 MCP Servers)

PubMed, ClinicalTrials.gov, OpenFDA (FAERS, labels, recalls), CDC disease surveillance, SEER cancer statistics, Census ACS (social determinants), EPA air quality, ClinVar/dbSNP genomics, CMS provider/payment data, FDA regulatory pathways, medical imaging archives, pharmacovigilance, medical society guidelines, VigiBase (WHO), knowledge graph (Memgraph), NHS 111 clinical pathways (Firecrawl), BMA Library guidelines (Firecrawl), OpenEvidence AI-synthesised Q&A.

### Key Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Agent hosting | Google ADK + Solace AI Connector | LLM orchestration + broker connectivity |
| Communication | A2A protocol over Solace Event Mesh | Async agent-to-agent messaging |
| Web UI | React + Vite + SSE streaming | Chat interface with citations panel |
| MCP servers | FastMCP 3.x (SSE transport) | External API integration |
| Memory plane | Redis | Shared state across agents per session |
| Cold store | SQLite | Learning from past research sessions |
| Knowledge graph | Memgraph (bolt protocol) | Biomedical entity persistence + NLQ queries |
| Web search | Firecrawl + Brave (fallback) | Orchestrator pre-search for unknown drug/brand names |
| LLM failover | Exponential backoff + model chains | Auto-retry on 429/5xx, flash→pro fallback |
| Learning loops | Specialist calibrator + feedback bridge | Per-domain weight adjustment, user feedback integration |
| Prompt evolution | Human approval gate + auto-rollback | Safe prompt improvement with regression testing |
| LLM reasoning | 6 LLM-augmented tools | Evidence grading, coverage, synthesis, contradiction detection, narrative, uncertainty |
| LLM abstraction | LiteLLM | Provider-agnostic model access |

---

## Research Protocol

The orchestrator executes a streamlined 7-step protocol for every complex query:

| Step | Name | What Happens |
|------|------|-------------|
| 0 | SEED | Load learned routing hints from past sessions |
| 1 | PLAN | Decompose query into sub-questions with primary + secondary specialist routing |
| 2 | DELEGATE | Call ALL specialist agents in parallel (single LLM turn) |
| 3 | COLLECT + PUBLISH | Gather evidence, publish citations, validate coverage (70%+ target) |
| 4 | SYNTHESIZE | Generate evidence-graded report with citations |
| 5 | VERIFY + REVISE | GVR loop: fact-check claims, revise if critical issues (max 1 cycle) |
| 6 | PERSIST | Save session signals to cold store for learning |

Symptom-based queries are routed to the **triage pipeline** instead, which runs a specialist panel consultation, clinical evaluation, consensus building, and next-best-action determination.

### GVR Loop Detail

The Generator-Verifier-Reviser loop is the quality gate that prevents unverified medical claims from reaching users:

```
Orchestrator (SYNTHESIZE)
    |
    | Draft report with [[cite:...]] markers
    v
Verifier Agent (gemini-3.7-flash, temp 0.1)
    |
    |--- For each claim:
    |      1. Does the cited source exist? (structural)
    |      2. Does the source support the claim? (LLM entailment)
    |      3. Keyword overlap fallback (Jaccard >= 0.15)
    |
    |--- Verdict:
    |      PASS (score >= 0.7) ---------> Output report
    |      MINOR_ISSUES (0.4-0.7) -----> Output with notes
    |      CRITICAL_ISSUES (< 0.4) -----> Revise
    |                                       |
    v                                       v
                                    Reviser Agent
                                        |
                                        | Surgical corrections
                                        v
                                    Re-Verify (1 cycle max)
                                        |
                                  PASS? --> Output revised report
                                  FAIL? --> "Research Inconclusive" + failed claims list
```

If re-verification still fails after revision, MedExpert withholds the report entirely and explains which claims could not be verified. This is a deliberate safety measure — no unverifiable medical information is delivered.

### Cross-Session Learning

MedExpert improves over time through three learning loops:

| Loop | Trigger | What It Does |
|------|---------|-------------|
| **Specialist Calibration** | Every 10 session flushes | Computes per-specialist, per-domain weights from user feedback (60%) + verification outcomes (40%). Weights seed future routing decisions. |
| **Prompt Evolution** | Rolling composite score < 0.55 | Generates improved specialist prompts via LLM metaprompt, runs regression against golden dataset. Candidates require human approval before activation. |
| **Feedback Bridge** | Each user thumbs up/down | SAM broker flow captures gateway feedback, enriches with session context from cold store, persists for calibration and evolution signals. |

### Agent Resilience (OpenClaw-inspired)

MedExpert incorporates agentic resilience patterns from the OpenClaw architecture:

| Feature | What It Does |
|---------|-------------|
| **LLM failover chains** | Auto-retry with exponential backoff on transient errors (429/500/503). Flash agents fail over to Pro; verifier has NO fallback (medical accuracy preserved). |
| **Error recovery hints** | User-actionable hints on MCP failures ("Set NCBI_API_KEY for higher rate limits") instead of raw error codes. |
| **Specialist-to-specialist help** | When a specialist's data source fails, it can ask a peer for help (e.g., DrugSpecialist → LiteratureSpecialist). Budget-capped (2 requests), loop-prevented. |
| **Context compaction** | Summarization hints injected at heavy protocol steps to reduce context overflow on long research sessions. |
| **Sync peer tool** | Blocking agent-to-agent conversations (vs fire-and-forget) for specialist help requests. |
| **Health check CLI** | `python scripts/doctor.py` checks Redis, 18 MCP servers, cold store, env vars, Memgraph. |

### LLM-Augmented Reasoning (Phase 5)

Six of the seven core research tools now use LLM reasoning instead of pure heuristics. Each tool falls back to its original heuristic when the LLM call fails — never worse than before.

| Tool | Before | After | LLM calls |
|------|--------|-------|-----------|
| **evidence_grader** | Lookup table (meta-analysis=4.5, RCT=4.0...) | LLM contextual quality assessment (blinding, sample size, funding bias) + heuristic fallback | 1 |
| **completeness_checker** | Jaccard keyword overlap at 10% | LLM semantic coverage check — understands "drug X side effects" ≠ "Is drug X effective?" | 1 |
| **deliberation_synthesizer** | Keyword consensus counting | LLM multi-perspective synthesis with contested points, blind spots, and resolution notes | 1 |
| **reflection_analyzer** | 9 hardcoded antonym pairs | LLM contradiction/gap detection — finds magnitude differences, population differences, temporal changes | 1 |
| **report_generator** | Template fill | LLM narrative synthesis — reasons about evidence, addresses contradictions, quantifies uncertainty | 1 |
| **uncertainty_quantifier** | *(new)* | Per-claim confidence profiling with basis classification and caveats | 1 |

Total additional LLM calls per session: ~6 (~8% increase over the ~50-call baseline). All use `response_mime_type="application/json"` for structured output with `extract_json_from_text` as defense-in-depth. `max_tokens` set to 8192 to accommodate Gemini Flash thinking tokens.

Manage prompt candidates via CLI:
```bash
python scripts/manage_prompts.py list                          # View pending candidates
python scripts/manage_prompts.py show DrugSpecialist 3         # Inspect candidate details
python scripts/manage_prompts.py approve DrugSpecialist 3      # Promote to active
```

---

## Quick Start

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker (for Redis)
- Node.js 20+ (for frontend)
- API key for OpenAI, Gemini, or Anthropic

### One-command setup

```bash
bash medexpert/dev.sh
```

This single command handles everything:
1. Creates Python venv and installs all dependencies
2. Generates `.env` from `.env.example` (prompts for API key if missing)
3. Starts Redis container
4. Starts 18 MCP servers on ports 9001-9018
5. Starts 13 agents + gateway on http://localhost:8000
6. Starts frontend dev server on http://localhost:3000

All agents run in a **single `sam run` process** so they share one in-memory
DevBroker. Running agents as separate processes creates isolated brokers where
agents cannot discover each other — this was identified and fixed by
[@M-Elsaied](https://github.com/M-Elsaied) in [PR #13](https://github.com/2096955/MedLiason/pull/13).

Press `Ctrl+C` to stop all processes. Redis container is preserved for next run.

```bash
# Options
bash medexpert/dev.sh --no-frontend   # Skip frontend dev server
bash medexpert/dev.sh --skip-mcp      # Skip MCP server startup
bash medexpert/dev.sh --reset         # Clean slate (delete venv, .env, stop Redis)
```

### Docker alternative

```bash
make medexpert-docker
```

### Run components individually

```bash
./scripts/start_mcp_servers.sh    # MCP servers only
./scripts/start_agents.sh         # Agents + gateway (assumes MCP servers running)
```

---

## Development

### MedExpert

```bash
make medexpert-dev           # One-command local dev setup
make medexpert-docker        # Run via Docker Compose
```

### SAM Framework (root)

```bash
make dev-setup              # Create venv, sync deps, install Playwright
make test                   # Run tests (excluding stress)
make test-unit              # Unit tests only
make test-cov               # Tests with 70% coverage threshold
uv run ruff check .         # Lint
uv run ruff format .        # Format
```

### MedExpert Tests (medexpert/)

```bash
cd medexpert
pytest tests/unit -v        # Unit tests (tools + MCP servers)
pytest tests/contract -v    # Contract tests (cassette-based HTTP replay)
pytest tests/integration -v # GVR loop + orchestration pipeline
pytest -m contract          # By marker

# Evaluation
python tests/eval_runner.py --mode golden      # Golden dataset
python tests/eval_runner.py --mode red-team    # Adversarial prompts
```

### Frontend (client/webui/frontend/)

```bash
cd client/webui/frontend
npm install
npm run dev                 # Vite dev server on http://localhost:3000
npm run lint                # ESLint
npm run build-package       # Production build
```

### Regenerating README Screenshots

The screenshots in `docs/screenshots/` are captured by a Playwright script. Open three separate terminals:

```bash
# Terminal 1 — Gateway (port 8000)
cd medexpert
sam run configs/gateways/webui.yaml

# Terminal 2 — Vite dev server (port 3000)
cd client/webui/frontend
npm install          # first time only
npm run dev

# Terminal 3 — Capture (after both services are accepting connections)
npx playwright install chromium   # first time only
node docs/take_screenshots.mjs
```

The script uses stable selectors (`data-testid`, `aria-label`) mapped to the actual component source. Each section is independently wrapped in try/catch so a single selector change won't abort the entire run. See the header comment in the script for details.

---

## Security

MedExpert includes security hardening for deployments handling medical data:

- **CORS**: Configurable origins, methods, headers. Wildcard + credentials rejected at startup.
- **Session secrets**: Fail-fast validation rejects default placeholder values when auth is enabled.
- **Rate limiting**: Per-session token bucket on task creation endpoints (configurable, off by default).
- **Redis auth**: Startup warning when Redis has no password outside development mode.
- **PHI redaction**: Presidio-based (ML) or regex-based detection and redaction of protected health information.
- **Input sanitization**: SQL/SoQL injection prevention on all MCP server queries.
- **Evidence verification**: Every synthesized answer is fact-checked by a dedicated verifier agent before delivery.

See the [ARB remediation commit](https://github.com/2096955/MedLiason/commit/a93f9b37) for the full security hardening changelog.

---

## Project Structure

```
solace-agent-mesh/              # SAM framework (agent hosting, gateway, A2A)
  src/solace_agent_mesh/
    agent/                      # Agent hosting, ADK integration, tools
    gateway/http_sse/           # FastAPI + SSE web UI gateway
    common/                     # A2A helpers, registries, services
    workflow/                   # DAG workflow execution

medexpert/                      # Life sciences research application
  configs/
    agents/                     # YAML configs for all 13 agents
    gateways/webui.yaml         # Web UI gateway config
    feedback_bridge.yaml        # Feedback→cold store broker subscriber
    shared_config.yaml          # Model anchors, broker, services
  src/
    lifesci_tools/              # 39 custom tool/module implementations
    lifesci_common/             # Constants, config validator, utilities
    mcp_servers/                # 18 FastMCP SSE servers
  tests/                        # Unit, contract, integration, eval runner
  scripts/                      # Startup + management scripts
  infra/                        # Terraform, K8s, Docker

client/webui/frontend/          # React + Vite chat UI
config_portal/                  # Agent/gateway configuration wizard
```

---

## Environment Variables

Copy `medexpert/.env.example` and fill in:

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key (get one at [openrouter.ai/keys](https://openrouter.ai/keys)) |
| `REDIS_URL` | Yes | Memory plane backend |
| `SESSION_SECRET_KEY` | Yes (production) | Session cookie signing |
| `NCBI_API_KEY` | Recommended | PubMed rate limit (3 -> 10 req/s) |
| `CENSUS_API_KEY` | Optional | Census ACS SDOH data |
| `EPA_AQS_EMAIL` / `EPA_AQS_KEY` | Optional | EPA air quality data |
| `FIRECRAWL_API_KEY` | Recommended | Web search (orchestrator) + NHS/BMA MCP servers |
| `BRAVE_SEARCH_API_KEY` | Optional | Web search fallback (if Firecrawl unavailable) |
| `MEMGRAPH_URL` | Optional | Knowledge graph (bolt://localhost:7687) |

---

## License

Apache 2.0. See [LICENSE](LICENSE).
