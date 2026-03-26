# C2 + C6: Response Path Indicator & Advisory Audit Trail — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add response-path indicator (C2) and advisory board audit trail (C6) to MedExpert — surfacing which research depth the orchestrator chose and persisting/displaying advisory board deliberation output.

**Architecture:** Both features follow the same pattern: (1) orchestrator stores data in Redis via memory_plane, (2) memory_plane includes data in SSE progress events, (3) cold store captures data during flush, (4) frontend renders it in ResearchProtocolStepper. C2 adds a `report_mode` field; C6 adds `advisory_perspectives` JSON.

**Tech Stack:** Python (DynamicTool, SQLite, Redis), TypeScript/React (Tailwind CSS, lucide-react icons), Pydantic (data_parts.py)

---

### Task 1: Add `report_mode` and `advisory_perspectives` to cold store schema

**Files:**
- Modify: `medexpert/src/lifesci_tools/cold_store.py:443-471` (write_session_outcome)
- Modify: `medexpert/src/lifesci_tools/cold_store.py:376` (after M7 migration, add M8)
- Test: `medexpert/tests/unit/test_cold_store.py`

**Step 1: Write the failing tests**

Add to `medexpert/tests/unit/test_cold_store.py`:

```python
def test_write_session_outcome_with_report_mode(db):
    cold_store.write_session_outcome(
        db,
        session_id="s-rm",
        query_domain="drugs",
        report_mode="full_synthesis",
    )
    row = db.execute(
        "SELECT report_mode FROM session_outcomes WHERE session_id = 's-rm'"
    ).fetchone()
    assert row["report_mode"] == "full_synthesis"


def test_write_session_outcome_with_advisory_perspectives(db):
    advisory = '{"consensus_points":["point1"],"blind_spots":[]}'
    cold_store.write_session_outcome(
        db,
        session_id="s-adv",
        query_domain="literature",
        advisory_perspectives_json=advisory,
    )
    row = db.execute(
        "SELECT advisory_perspectives_json FROM session_outcomes WHERE session_id = 's-adv'"
    ).fetchone()
    assert row["advisory_perspectives_json"] == advisory


def test_write_session_outcome_defaults_null_for_new_columns(db):
    cold_store.write_session_outcome(db, session_id="s-def", query_domain="drugs")
    row = db.execute(
        "SELECT report_mode, advisory_perspectives_json FROM session_outcomes WHERE session_id = 's-def'"
    ).fetchone()
    assert row["report_mode"] is None
    assert row["advisory_perspectives_json"] is None


def test_migration_m8_adds_columns(tmp_path):
    """Verify M8 migration adds report_mode and advisory_perspectives_json to existing DBs."""
    db_path = str(tmp_path / "test.db")
    # Create DB with original schema (no new columns)
    conn = cold_store.get_connection(db_path)
    # Insert a row
    cold_store.write_session_outcome(conn, session_id="s-old", query_domain="drugs")
    conn.close()
    # Re-open — migration should have run
    conn2 = cold_store.get_connection(db_path)
    row = conn2.execute(
        "SELECT report_mode, advisory_perspectives_json FROM session_outcomes WHERE session_id = 's-old'"
    ).fetchone()
    assert row["report_mode"] is None
    assert row["advisory_perspectives_json"] is None
    conn2.close()
```

**Step 2: Run tests to verify they fail**

Run: `cd medexpert && uv run pytest tests/unit/test_cold_store.py::test_write_session_outcome_with_report_mode tests/unit/test_cold_store.py::test_write_session_outcome_with_advisory_perspectives tests/unit/test_cold_store.py::test_write_session_outcome_defaults_null_for_new_columns -v`
Expected: FAIL — `write_session_outcome` doesn't accept `report_mode` or `advisory_perspectives_json`, columns don't exist.

**Step 3: Implement cold store changes**

In `medexpert/src/lifesci_tools/cold_store.py`:

1. Add M8 migration after the M7 entry in `_MIGRATIONS`:

```python
    # M8: Add report_mode and advisory_perspectives_json to session_outcomes.
    (
        "SELECT 1 FROM pragma_table_info('session_outcomes') WHERE name='report_mode'",
        """
        ALTER TABLE session_outcomes ADD COLUMN report_mode TEXT DEFAULT NULL;
        ALTER TABLE session_outcomes ADD COLUMN advisory_perspectives_json TEXT DEFAULT NULL;
        """,
    ),
```

**Important:** SQLite `executescript` can handle multiple `ALTER TABLE` statements separated by semicolons.

2. Update `write_session_outcome` signature and SQL:

```python
def write_session_outcome(
    conn: sqlite3.Connection,
    session_id: str,
    query_domain: str,
    query_text: str = "",
    coverage_pct: float = 0.0,
    verification_verdict: str = "UNKNOWN",
    verification_score: float = 0.0,
    revision_triggered: bool = False,
    specialists_used: list[str] | None = None,
    report_mode: str | None = None,
    advisory_perspectives_json: str | None = None,
) -> None:
    """Insert or replace a session outcome row."""
    conn.execute(
        """INSERT OR REPLACE INTO session_outcomes
           (session_id, query_domain, query_text, coverage_pct,
            verification_verdict, verification_score, revision_triggered,
            specialists_used_json, report_mode, advisory_perspectives_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            query_domain,
            query_text,
            coverage_pct,
            verification_verdict,
            verification_score,
            int(revision_triggered),
            json.dumps(specialists_used or []),
            report_mode,
            advisory_perspectives_json,
        ),
    )
```

**Step 4: Run tests to verify they pass**

Run: `cd medexpert && uv run pytest tests/unit/test_cold_store.py -v -k "report_mode or advisory_perspectives or migration_m8"`
Expected: PASS

**Step 5: Commit**

```bash
git add medexpert/src/lifesci_tools/cold_store.py medexpert/tests/unit/test_cold_store.py
git commit --signoff -m "feat(cold-store): add report_mode and advisory_perspectives_json columns (C2+C6)"
```

---

### Task 2: Add `report_mode` to report_generator output

**Files:**
- Modify: `medexpert/src/lifesci_tools/report_generator.py:497-504` (return dict)
- Test: `medexpert/tests/unit/test_report_generator.py`

**Step 1: Write the failing test**

Add to `medexpert/tests/unit/test_report_generator.py`:

```python
async def test_report_mode_in_output(tool, ctx, sample_evidence):
    """report_generator must include report_mode in its output dict."""
    for mode in ["quick_answer", "research_brief", "full_synthesis", "advisory_board_report"]:
        result = await tool._run_async_impl(
            {
                "mode": mode,
                "question": "Test?",
                "evidence_json": json.dumps(sample_evidence),
            },
            ctx,
        )
        assert result["report_mode"] == mode
```

**Step 2: Run test to verify it fails**

Run: `cd medexpert && uv run pytest tests/unit/test_report_generator.py::test_report_mode_in_output -v`
Expected: FAIL — `report_mode` key not in result dict.

**Step 3: Implement — add `report_mode` to return dict**

In `medexpert/src/lifesci_tools/report_generator.py`, change the return dict at line ~497:

```python
        return {
            "report": report,
            "mode": mode,
            "report_mode": mode,  # <-- Add this line (C2: frontend reads this)
            "evidence_count": len(evidence),
            "has_verification": verification is not None,
            "has_advisory": advisory is not None,
            "method": method,
        }
```

**Step 4: Run test to verify it passes**

Run: `cd medexpert && uv run pytest tests/unit/test_report_generator.py::test_report_mode_in_output -v`
Expected: PASS

**Step 5: Run full report_generator test suite**

Run: `cd medexpert && uv run pytest tests/unit/test_report_generator.py -v`
Expected: All PASS (existing tests unaffected — they don't assert on absence of extra keys)

**Step 6: Commit**

```bash
git add medexpert/src/lifesci_tools/report_generator.py medexpert/tests/unit/test_report_generator.py
git commit --signoff -m "feat(report-generator): include report_mode in output dict (C2)"
```

---

### Task 3: Collect `report_mode` and `advisory_output` in memory_plane flush + emit in progress events

**Files:**
- Modify: `medexpert/src/lifesci_tools/memory_plane.py:349-408` (_collect_session_signals)
- Modify: `medexpert/src/lifesci_tools/memory_plane.py:284-330` (_emit_protocol_progress)
- Modify: `src/solace_agent_mesh/common/data_parts.py:479-504` (ResearchProtocolProgressData)
- Test: `medexpert/tests/unit/test_memory_plane.py`

**Step 1: Write the failing tests**

Add to `medexpert/tests/unit/test_memory_plane.py` (adapt to existing fixture patterns):

```python
async def test_collect_session_signals_includes_report_mode(tool):
    """flush_cold should auto-collect report_mode from hot store."""
    # Store report_mode in Redis
    await tool._run_async_impl(
        {"operation": "store", "key": "report_mode", "value": "full_synthesis", "namespace": "intermediate"},
        _make_ctx(),
    )
    signals = await tool._collect_session_signals("default")
    assert signals.get("report_mode") == "full_synthesis"


async def test_collect_session_signals_includes_advisory_output(tool):
    """flush_cold should auto-collect advisory_output from hot store."""
    advisory = json.dumps({"consensus_points": ["point1"], "blind_spots": ["spot1"]})
    await tool._run_async_impl(
        {"operation": "store", "key": "advisory_output", "value": advisory, "namespace": "intermediate"},
        _make_ctx(),
    )
    signals = await tool._collect_session_signals("default")
    assert signals.get("advisory_perspectives_json") == advisory
```

> **Note to implementer:** Adapt `_make_ctx()` and `tool` fixture to match existing test patterns in this file. Look at the existing test fixtures (likely `@pytest.fixture` for a MemoryPlaneTool with in-memory backend).

**Step 2: Run tests to verify they fail**

Run: `cd medexpert && uv run pytest tests/unit/test_memory_plane.py::test_collect_session_signals_includes_report_mode tests/unit/test_memory_plane.py::test_collect_session_signals_includes_advisory_output -v`
Expected: FAIL — signals dict doesn't include these keys.

**Step 3: Implement signal collection**

In `medexpert/src/lifesci_tools/memory_plane.py`, in `_collect_session_signals`:

1. Add coroutines for the two new keys (alongside existing ones around line 372-374):

```python
        rm_coro = self._backend.get(self._make_key(session_id, "intermediate", "report_mode"))
        adv_coro = self._backend.get(self._make_key(session_id, "intermediate", "advisory_output"))
```

2. Add them to the `asyncio.gather` call and unpack.

3. After unpacking, add:

```python
        # ── report mode (C2) ──
        if raw_rm:
            signals["report_mode"] = raw_rm

        # ── advisory perspectives (C6) ──
        if raw_adv:
            signals["advisory_perspectives_json"] = raw_adv
```

**Step 4: Add `research_path` field to `ResearchProtocolProgressData`**

In `src/solace_agent_mesh/common/data_parts.py`, add to the `ResearchProtocolProgressData` class (after `verification_verdict`):

```python
    research_path: Optional[str] = Field(
        None,
        description="Report mode chosen by orchestrator: quick_answer, research_brief, full_synthesis, advisory_board_report",
    )
    advisory_perspectives: Optional[dict] = Field(
        None,
        description="Deliberation synthesizer output: consensus_points, contested_points, blind_spots, synthesis, confidence_level",
    )
```

**Step 5: Update progress emission to include new fields**

In `medexpert/src/lifesci_tools/memory_plane.py`, in `_emit_protocol_progress` (around line 300-328):

After reading `coverage_pct` and `verdict` from Redis, add reads for `report_mode` and `advisory_output`:

```python
            report_mode = None
            advisory_perspectives = None
            try:
                raw_rm = await self._backend.get(
                    self._make_key(session_id, "intermediate", "report_mode")
                )
                if raw_rm:
                    report_mode = raw_rm
                raw_adv = await self._backend.get(
                    self._make_key(session_id, "intermediate", "advisory_output")
                )
                if raw_adv:
                    try:
                        parsed = json.loads(raw_adv)
                        # Truncate synthesis to 500 chars for SSE payload
                        if isinstance(parsed.get("synthesis"), str) and len(parsed["synthesis"]) > 500:
                            parsed["synthesis"] = parsed["synthesis"][:500] + "..."
                        advisory_perspectives = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass
            except Exception:
                pass
```

Then update the `ResearchProtocolProgressData` construction:

```python
            progress = ResearchProtocolProgressData(
                step=step,
                step_name=step_name,
                total_steps=7,
                detail=detail,
                coverage_pct=coverage_pct,
                gvr_cycle=0,
                verification_verdict=verdict,
                research_path=report_mode,
                advisory_perspectives=advisory_perspectives,
            )
```

**Step 6: Run tests to verify they pass**

Run: `cd medexpert && uv run pytest tests/unit/test_memory_plane.py -v -k "report_mode or advisory_output"`
Expected: PASS

**Step 7: Run full memory_plane test suite**

Run: `cd medexpert && uv run pytest tests/unit/test_memory_plane.py -v`
Expected: All PASS

**Step 8: Commit**

```bash
git add medexpert/src/lifesci_tools/memory_plane.py src/solace_agent_mesh/common/data_parts.py medexpert/tests/unit/test_memory_plane.py
git commit --signoff -m "feat(memory-plane): collect report_mode + advisory_output in flush, emit in SSE progress (C2+C6)"
```

---

### Task 4: Update cold_worker to pass new fields to write_session_outcome

**Files:**
- Modify: `medexpert/src/lifesci_tools/cold_worker.py:369-386` (_persist_one)
- Test: `medexpert/tests/unit/test_cold_store.py` (already covered by Task 1 tests)

**Step 1: Write the failing test**

Add to `medexpert/tests/unit/test_cold_store.py` (or a new test file if preferred):

```python
def test_persist_one_passes_report_mode_and_advisory(tmp_path):
    """Integration: _persist_one should pass report_mode and advisory to write_session_outcome."""
    from lifesci_tools import cold_worker

    db_path = str(tmp_path / "test.db")
    item = {
        "session_id": "s-persist",
        "cold_db_path": db_path,
        "query_domain": "drugs",
        "query_text": "test query",
        "report_mode": "research_brief",
        "advisory_perspectives_json": '{"consensus_points":["p1"]}',
    }
    cold_worker._persist_one(db_path, item)

    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Ensure schema
    from lifesci_tools import cold_store
    cold_store.get_connection(db_path).close()

    conn2 = cold_store.get_connection(db_path)
    row = conn2.execute(
        "SELECT report_mode, advisory_perspectives_json FROM session_outcomes WHERE session_id = 's-persist'"
    ).fetchone()
    assert row["report_mode"] == "research_brief"
    assert row["advisory_perspectives_json"] == '{"consensus_points":["p1"]}'
    conn2.close()
```

**Step 2: Run test to verify it fails**

Run: `cd medexpert && uv run pytest tests/unit/test_cold_store.py::test_persist_one_passes_report_mode_and_advisory -v`
Expected: FAIL — _persist_one doesn't extract or pass these fields.

**Step 3: Implement**

In `medexpert/src/lifesci_tools/cold_worker.py`, in `_persist_one` around line 373, after `specialists_used`:

```python
        report_mode = item.get("report_mode")
        advisory_perspectives_json = item.get("advisory_perspectives_json")
```

Update the `cold_store.write_session_outcome` call to include:

```python
        cold_store.write_session_outcome(
            conn,
            session_id=session_id,
            query_domain=query_domain,
            query_text=query_text,
            coverage_pct=coverage_pct,
            verification_verdict=verification_verdict,
            verification_score=verification_score,
            revision_triggered=revision_triggered,
            specialists_used=specialists_used,
            report_mode=report_mode,
            advisory_perspectives_json=advisory_perspectives_json,
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd medexpert && uv run pytest tests/unit/test_cold_store.py::test_persist_one_passes_report_mode_and_advisory -v`
Expected: PASS

**Step 5: Commit**

```bash
git add medexpert/src/lifesci_tools/cold_worker.py medexpert/tests/unit/test_cold_store.py
git commit --signoff -m "feat(cold-worker): pass report_mode + advisory_perspectives to cold store (C2+C6)"
```

---

### Task 5: Update orchestrator YAML prompts (all 3 variants)

**Files:**
- Modify: `medexpert/configs/agents/orchestrator.yaml`
- Modify: `medexpert/configs/pro/agents/orchestrator.yaml`
- Modify: `medexpert/configs/opus/agents/orchestrator.yaml`

**Step 1: Add instructions to STEP 4 in all 3 files**

In each orchestrator YAML, find the STEP 4 section. After the line about calling `report_generator`, add these two instruction blocks:

**After the `deliberation_synthesizer` call instruction** (around "Call `deliberation_synthesizer` with advisory perspectives"):

```yaml
        - After `deliberation_synthesizer` returns, IMMEDIATELY store its
          output: memory_plane(operation="store", key="advisory_output",
          value=<full JSON output as string>, namespace="intermediate").
          This persists the advisory board analysis for the audit trail.
```

**After the `report_generator` call instruction** (around "Then call `phi_redactor`... call `report_generator`"):

```yaml
        After calling report_generator, store the report mode:
        memory_plane(operation="store", key="report_mode",
        value=<the mode you used: quick_answer/research_brief/full_synthesis/advisory_board_report>,
        namespace="intermediate").
```

Make the same change in all 3 YAML files. The text is identical — only the model anchors differ.

**Step 2: Verify YAML syntax**

Run: `cd medexpert && python -c "import yaml; yaml.safe_load(open('configs/agents/orchestrator.yaml'))" && echo "standard OK"`
Run: `cd medexpert && python -c "import yaml; yaml.safe_load(open('configs/pro/agents/orchestrator.yaml'))" && echo "pro OK"`
Run: `cd medexpert && python -c "import yaml; yaml.safe_load(open('configs/opus/agents/orchestrator.yaml'))" && echo "opus OK"`
Expected: All print "OK" with no errors.

**Step 3: Commit**

```bash
git add medexpert/configs/agents/orchestrator.yaml medexpert/configs/pro/agents/orchestrator.yaml medexpert/configs/opus/agents/orchestrator.yaml
git commit --signoff -m "feat(orchestrator): store report_mode + advisory_output in memory_plane at STEP 4 (C2+C6)"
```

---

### Task 6: Frontend — add types and render response path indicator (C2)

**Files:**
- Modify: `client/webui/frontend/src/lib/components/research/ResearchProtocolStepper.tsx:29-38` (ResearchProtocolProgressData)
- Modify: `client/webui/frontend/src/lib/components/research/ResearchProtocolStepper.tsx:136-204` (header render)

**Step 1: Add `research_path` to `ResearchProtocolProgressData`**

In `ResearchProtocolStepper.tsx`, update the interface (around line 29-38):

```typescript
export interface ResearchProtocolProgressData {
  type: "research_protocol_progress";
  step: number;
  step_name: string;
  total_steps: number;
  detail: string;
  coverage_pct?: number | null;
  gvr_cycle?: number;
  verification_verdict?: string | null;
  research_path?: string | null;  // C2: quick_answer | research_brief | full_synthesis | advisory_board_report
  advisory_perspectives?: AdvisoryPerspectives | null;  // C6: deliberation synthesizer output
}
```

**Step 2: Add `AdvisoryPerspectives` type and path config**

Add above the interface (or in the same file, since it's self-contained):

```typescript
/** C6: Advisory board deliberation output */
export interface AdvisoryPerspectives {
  consensus_points?: string[];
  contested_points?: Array<{
    point: string;
    positions?: Array<{
      persona: string;
      position: string;
      strength: "strong" | "moderate" | "weak";
    }>;
    resolution_note?: string;
  }>;
  blind_spots?: string[];
  synthesis?: string;
  confidence_level?: "high" | "moderate" | "low";
  key_uncertainty?: string;
  perspective_count?: number;
  analysis_method?: string;
}

/** C2: Research path display config */
const RESEARCH_PATH_CONFIG: Record<string, { icon: typeof Sparkles; label: string; color: string }> = {
  quick_answer: { icon: Zap, label: "Quick Answer", color: "bg-sky-100 text-sky-700 dark:bg-sky-900/50 dark:text-sky-300" },
  research_brief: { icon: FileSearch, label: "Research Brief", color: "bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300" },
  full_synthesis: { icon: Microscope, label: "Deep Research", color: "bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-300" },
  advisory_board_report: { icon: Users, label: "Advisory Board", color: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-300" },
};
```

**Step 3: Add new icon imports**

Update the lucide-react import at the top:

```typescript
import {
  Sparkles,
  GitBranch,
  Send,
  Inbox,
  FileText,
  Shield,
  Database,
  ChevronDown,
  ChevronUp,
  CheckCircle,
  AlertTriangle,
  Loader2,
  RefreshCw,
  Clock,
  Timer,
  Zap,          // C2
  FileSearch,   // C2
  Microscope,   // C2
  Users,        // C2 + C6
} from "lucide-react";
```

**Step 4: Render the path badge in the header**

In the header section (around line 185-204), after the progress text span and timer, add the path badge:

```tsx
          {/* C2: Research path indicator */}
          {progress.research_path && RESEARCH_PATH_CONFIG[progress.research_path] && (() => {
            const cfg = RESEARCH_PATH_CONFIG[progress.research_path!];
            const PathIcon = cfg.icon;
            return (
              <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded ${cfg.color}`}>
                <PathIcon className="w-3 h-3" />
                {cfg.label}
              </span>
            );
          })()}
```

Place this inside the header `<div className="flex items-center gap-2">` section, after the timer spans but before the closing `</div>`.

**Step 5: Verify build**

Run: `cd client/webui/frontend && npm run lint && npm run build-package`
Expected: No errors.

**Step 6: Commit**

```bash
git add client/webui/frontend/src/lib/components/research/ResearchProtocolStepper.tsx
git commit --signoff -m "feat(frontend): add response path indicator badge in stepper header (C2)"
```

---

### Task 7: Frontend — add advisory perspectives collapsible section (C6)

**Files:**
- Modify: `client/webui/frontend/src/lib/components/research/ResearchProtocolStepper.tsx`

**Step 1: Add the `AdvisoryPerspectivesPanel` component**

Add a new component inside the same file (or extract if preferred), before the main `ResearchProtocolStepper` export:

```tsx
/** C6: Expandable advisory board perspectives section */
function AdvisoryPerspectivesPanel({ data }: { data: AdvisoryPerspectives }) {
  const [expanded, setExpanded] = useState(false);

  const confidenceColor = {
    high: "bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-300",
    moderate: "bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300",
    low: "bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-300",
  }[data.confidence_level ?? "moderate"] ?? "bg-gray-100 text-gray-700";

  return (
    <div className="mt-1 ml-6 rounded border border-gray-200 dark:border-gray-700 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-2 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors"
        aria-expanded={expanded}
      >
        <span className="flex items-center gap-1.5 font-medium text-gray-600 dark:text-gray-400">
          <Users className="w-3 h-3" />
          Advisory Perspectives
          {data.confidence_level && (
            <span className={`px-1 py-0.5 rounded text-[10px] ${confidenceColor}`}>
              {data.confidence_level}
            </span>
          )}
        </span>
        {expanded ? <ChevronUp className="w-3 h-3 text-gray-400" /> : <ChevronDown className="w-3 h-3 text-gray-400" />}
      </button>

      {expanded && (
        <div className="px-2 pb-2 space-y-2 text-xs text-gray-600 dark:text-gray-400">
          {/* Synthesis */}
          {data.synthesis && (
            <p className="leading-relaxed">{data.synthesis}</p>
          )}

          {/* Consensus */}
          {data.consensus_points && data.consensus_points.length > 0 && (
            <div>
              <span className="font-medium text-green-600 dark:text-green-400">Consensus</span>
              <ul className="mt-0.5 space-y-0.5">
                {data.consensus_points.map((p, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="mt-1.5 h-1 w-1 rounded-full bg-green-500 flex-shrink-0" />
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Contested */}
          {data.contested_points && data.contested_points.length > 0 && (
            <div>
              <span className="font-medium text-amber-600 dark:text-amber-400">Contested Points</span>
              <div className="mt-0.5 space-y-1.5">
                {data.contested_points.map((cp, i) => (
                  <div key={i} className="pl-2 border-l-2 border-amber-300 dark:border-amber-700">
                    <p className="font-medium">{cp.point}</p>
                    {cp.positions && cp.positions.map((pos, j) => (
                      <p key={j} className="text-gray-500 dark:text-gray-500">
                        <span className="font-medium">{pos.persona}</span>: {pos.position}
                        <span className="opacity-60"> ({pos.strength})</span>
                      </p>
                    ))}
                    {cp.resolution_note && (
                      <p className="text-gray-500 dark:text-gray-500 italic mt-0.5">{cp.resolution_note}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Blind spots */}
          {data.blind_spots && data.blind_spots.length > 0 && (
            <div>
              <span className="font-medium text-orange-600 dark:text-orange-400">Blind Spots</span>
              <ul className="mt-0.5 space-y-0.5">
                {data.blind_spots.map((b, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <AlertTriangle className="w-3 h-3 mt-0.5 text-orange-500 flex-shrink-0" />
                    {b}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Key uncertainty */}
          {data.key_uncertainty && (
            <p className="text-gray-500 dark:text-gray-500">
              <span className="font-medium">Key uncertainty:</span> {data.key_uncertainty}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
```

**Step 2: Render the panel after step 4**

In the step map rendering loop (around line 254-322), after the step label and detail section for step 4, add:

```tsx
                  {/* C6: Advisory perspectives panel after SYNTHESIZE step */}
                  {idx === 4 && (status === "complete" || status === "active") &&
                    progress.advisory_perspectives && (
                    <AdvisoryPerspectivesPanel data={progress.advisory_perspectives} />
                  )}
```

Place this inside the `<div className="flex-1 pb-1 ...">` block for each step, after the existing detail/MCP failure rendering.

**Step 3: Verify build**

Run: `cd client/webui/frontend && npm run lint && npm run build-package`
Expected: No errors.

**Step 4: Commit**

```bash
git add client/webui/frontend/src/lib/components/research/ResearchProtocolStepper.tsx
git commit --signoff -m "feat(frontend): add collapsible advisory perspectives panel in stepper (C6)"
```

---

### Task 8: Run full test suites

**Step 1: Backend tests**

Run: `cd medexpert && uv run pytest tests/unit/ -v --tb=short`
Expected: All PASS. No regressions.

**Step 2: Frontend tests**

Run: `cd client/webui/frontend && npm run lint`
Expected: PASS.

**Step 3: Verify no ruff issues**

Run: `cd medexpert && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: PASS.

**Step 4: Final commit (if any formatting fixes needed)**

```bash
git add -A && git commit --signoff -m "chore: lint fixes"
```

---

## Summary of Changes

| Task | Component | Feature |
|------|-----------|---------|
| 1 | cold_store.py | M8 migration + write_session_outcome new params |
| 2 | report_generator.py | `report_mode` in output dict |
| 3 | memory_plane.py + data_parts.py | Collect + emit report_mode/advisory in signals/SSE |
| 4 | cold_worker.py | Pass new fields through to cold_store |
| 5 | 3x orchestrator.yaml | Prompt instructions to store in memory_plane |
| 6 | ResearchProtocolStepper.tsx | Path badge (C2) |
| 7 | ResearchProtocolStepper.tsx | Advisory panel (C6) |
| 8 | Full test run | Regression check |
