# C2 + C6 Design: Response Path Indicator & Advisory Audit Trail

**Date:** 2026-03-20
**Status:** Approved

## C2 — Response Path Indicator

### Goal
Distinguish deep research (7-step protocol) from quick answer in the UI. The orchestrator autonomously picks the report mode; the frontend reflects this choice.

### Report Modes
| Mode | Icon | Label | When Used |
|------|------|-------|-----------|
| `quick_answer` | Zap | Quick Answer | Simple factual questions |
| `research_brief` | FileSearch | Research Brief | Focused single-topic queries |
| `full_synthesis` | Microscope | Deep Research | Comprehensive multi-specialist |
| `advisory_board_report` | Users | Advisory Board | Multi-perspective synthesis |

### Backend Changes

1. **`report_generator.py`** — After generating the report, store the chosen mode in memory_plane:
   ```python
   # In _run_async_impl, after report generation:
   await self._store_report_mode(tool_context, mode)
   ```
   Uses `memory_plane(operation="store", key="report_mode", value=mode, namespace="intermediate")`.

   Since report_generator doesn't have direct access to memory_plane, the approach is: return `report_mode` in the tool's output dict. The orchestrator prompt already receives the output — we add an instruction to store it.

2. **Orchestrator YAML (3 files)** — Add instruction in STEP 4: "After calling report_generator, store the report mode: `memory_plane(operation='store', key='report_mode', value=<mode used>, namespace='intermediate')`."

3. **`protocol_step_validator.py`** — When emitting `research_protocol_progress` events at step >= 4, read `report_mode` from `session.state` or fall back to checking memory_plane. Include as `research_path` field in the progress data.

4. **`memory_plane.py` `_collect_session_signals`** — Add `report_mode` to the signals collected during flush_cold.

5. **`cold_store.py`** — Add `report_mode TEXT DEFAULT NULL` column to `session_outcomes`. Migration via `ALTER TABLE` in schema initialization.

### Frontend Changes

6. **`types.ts` or `ResearchProtocolStepper.tsx`** — Add `research_path?: string | null` to `ResearchProtocolProgressData`.

7. **`ResearchProtocolStepper.tsx`** — In the header bar, after "Research in progress — X%", show the path icon + label when `research_path` is non-null. Compact pill/badge style.

---

## C6 — Advisory Board Audit Trail

### Goal
Persist `deliberation_synthesizer` output to Redis and cold store. Show an expandable "Advisory Perspectives" section in the stepper.

### Data Shape (from deliberation_synthesizer)
```typescript
interface AdvisoryPerspectives {
  consensus_points: string[];
  contested_points: Array<{
    point: string;
    positions: Array<{
      persona: string;
      position: string;
      strength: "strong" | "moderate" | "weak";
    }>;
    resolution_note: string;
  }>;
  blind_spots: string[];
  synthesis: string;
  confidence_level: "high" | "moderate" | "low";
  key_uncertainty: string;
  perspective_count: number;
  analysis_method: "llm_deliberation" | "single_model_simulation";
}
```

### Backend Changes

1. **Orchestrator YAML (3 files)** — Add instruction in STEP 4: "After calling `deliberation_synthesizer`, store its output: `memory_plane(operation='store', key='advisory_output', value=<full JSON output>, namespace='intermediate')`."

2. **`memory_plane.py` `_collect_session_signals`** — Add `advisory_output` key to the parallel reads from the `intermediate` namespace.

3. **`cold_store.py`** — Add `advisory_perspectives_json TEXT DEFAULT NULL` column to `session_outcomes`. Store the full JSON blob.

4. **`cold_store.py` `store_session_outcome`** — Accept and write `advisory_perspectives_json` when present in the payload.

5. **`protocol_step_validator.py`** — At step 4 completion, read `advisory_output` from session state and include it in the `research_protocol_progress` event as `advisory_perspectives` field. Truncate synthesis to 500 chars for the SSE payload to avoid bloat — full data available via cold store.

### Frontend Changes

6. **`ResearchProtocolProgressData`** — Add `advisory_perspectives?: AdvisoryPerspectives | null`.

7. **`ResearchProtocolStepper.tsx`** — New collapsible section after step 4 row:
   - Toggle button: "Advisory Perspectives" with Users icon + chevron
   - Default collapsed
   - When expanded, shows:
     - **Synthesis** paragraph (main narrative)
     - **Consensus** — green bullets for consensus_points
     - **Contested** — amber cards for contested_points, each showing persona positions and resolution note
     - **Blind Spots** — red/orange bullets
     - **Confidence** badge (high=green, moderate=amber, low=red)
     - **Key Uncertainty** one-liner
   - Only renders when `advisory_perspectives` is non-null

---

## Files Changed

| File | C2 | C6 |
|------|----|----|
| `medexpert/configs/agents/orchestrator.yaml` | x | x |
| `medexpert/configs/pro/agents/orchestrator.yaml` | x | x |
| `medexpert/configs/opus/agents/orchestrator.yaml` | x | x |
| `medexpert/src/lifesci_tools/report_generator.py` | x | |
| `medexpert/src/lifesci_tools/memory_plane.py` | x | x |
| `medexpert/src/lifesci_tools/cold_store.py` | x | x |
| `medexpert/src/lifesci_tools/protocol_step_validator.py` | x | x |
| `client/webui/frontend/src/lib/types.ts` (or inline) | x | x |
| `client/webui/frontend/src/lib/components/research/ResearchProtocolStepper.tsx` | x | x |
| `client/webui/frontend/src/lib/providers/ChatProvider.tsx` | | x |

Plus unit tests for each changed backend file.

## Out of Scope
- User-selectable research depth (future enhancement)
- Advisory perspectives in cold store dashboard API (can be added later)
- Triage pipeline path indicator (triage already has its own stepper)
