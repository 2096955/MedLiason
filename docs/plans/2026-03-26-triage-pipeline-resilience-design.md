# Triage Pipeline Resilience — Design

**Date:** 2026-03-26
**Phase:** 9
**Status:** Approved

## Problem

Two issues observed in `.stim` traces from Cloud Run (medexpert-v4, 2026-03-23):

1. **Specialist panel total failure** (stim `b709`): All 4 specialist LLM calls in `triage_specialist_panel` returned "Call failed or timed out" simultaneously, producing INCONCLUSIVE consensus and a generic "seek urgent medical attention" response for a clear ME/CFS presentation.

2. **Intake agent premature completion** (stim `b286`): After `triage_intake` returned `status: "complete"`, the LLM generated text saying "I'm routing your case" but never called `peer_TriageOrchestratorAgent`. The ADK runner saw a text-only response and finalized the task. The patient never received triage results.

## Root Cause Analysis

### Issue 1: Specialist Panel

- **Timeout too aggressive:** `TRIAGE_SPECIALIST_TIMEOUT_S = 30` seconds. Four parallel `asyncio.to_thread(litellm.completion, ...)` calls compete for CPU on Cloud Run. Vertex AI cold starts or transient 503s push all 4 past the timeout.
- **No API-level retry:** The code retries JSON parse failures but NOT network/timeout/rate-limit errors. A single transient failure kills the specialist.
- **`num_retries` not forwarded:** The `*specialist_model` anchor has `num_retries: 4` but the panel doesn't pass it to `litellm.completion()`.
- **Lost diagnostics:** The verdict `thinking` field is always the generic string "Call failed or timed out" — the actual exception is logged but not surfaced to the orchestrator.

### Issue 2: Intake Agent

- **LLM instruction-following gap:** Gemini 2.5 Flash generated the "Thank you" text but omitted the `peer_TriageOrchestratorAgent` tool call. The handoff contract prompt describes both actions but doesn't enforce same-turn co-occurrence.
- **No programmatic guard:** The ADK runner correctly finalized (no pending tool calls), but there's no callback to catch this specific failure mode.
- **Not a limit issue:** `max_llm_calls_per_task: 15` was not exhausted (only 2 LLM calls in the task).

## Design

### Fix 1: Specialist Panel Resilience

**Files:**
- `medexpert/src/lifesci_common/constants.py`
- `medexpert/src/lifesci_tools/triage_specialist_panel.py`

**Changes:**

1. **Timeout 30→60s** in `constants.py`.

2. **API-level retry** in `_consult_specialist()`: Wrap the existing attempt loop in an outer retry (max 2 API attempts). On `asyncio.TimeoutError` or transient litellm errors (503, 429, connection errors), sleep 3s then retry once. JSON parse failures keep their existing inner retry.

3. **Forward `num_retries`** from model config dict to `litellm.completion()`.

4. **Diagnostic thinking field:** Replace `"Call failed or timed out"` with `f"{type(exc).__name__}: {str(exc)[:100]}"`. Truncated, no patient data (exception is from litellm, not LLM response).

**Error handling structure:**
```
for api_attempt in range(2):          # 1 retry on transient API errors
    for parse_attempt in range(2):    # 1 retry on JSON parse failure (existing)
        try:
            response = await asyncio.wait_for(litellm.completion(..., num_retries=N), timeout=60)
            result = extract_json_from_text(response)
            if result is None: raise JSONDecodeError
            return verdict
        except JSONDecodeError:
            if parse_attempt == 0: continue
        except (TimeoutError, transient errors):
            break  → retry outer
        except Exception:
            break  → retry outer
    else:
        break  # parse retries exhausted, don't retry API
    await asyncio.sleep(3)  # backoff before API retry
return fallback_verdict(thinking=diagnostic_message)
```

### Fix 2: Intake Agent Handoff Guard

**Files:**
- `medexpert/configs/agents/triage_intake.yaml` (+ pro/opus variants)
- `medexpert/src/lifesci_tools/triage_handoff_guard.py` (NEW)

**Prompt hardening:** Add to HANDOFF CONTRACT:
```
CRITICAL: When status="complete", you MUST call peer_TriageOrchestratorAgent
in the SAME response turn as the "Thank you" text. Do NOT generate a text-only
response — the task will be marked complete and the patient will never receive
their triage results.
```

**`after_model_callback`:**
- Registered on TriageIntakeAgent only via YAML `after_model_callbacks`.
- Inspects the LLM response: if it contains text indicating handoff intent (e.g., "routing", "specialist panel") AND has no `peer_TriageOrchestratorAgent` function call → checks session state for a completed `triage_intake` tool with `status: "complete"` → if found, soft-rejects with guidance to include the peer tool call.
- ~40 lines, no LLM calls, no external imports.

### Config Synchronization

All 3 variants (standard, pro, opus) receive identical changes.

### Testing

- Specialist panel: unit tests for retry on TimeoutError, retry on litellm transient error, no retry on parse failure (existing behavior preserved), diagnostic thinking field.
- Handoff guard: unit tests for text-only response rejection, text+tool-call acceptance, no rejection when triage_intake not yet called.

### Not Changing

- SAM framework / ADK runner
- Other triage tools (consensus, evaluation, nba, routing)
- No staggered specialist launch
- No auto-delegation from Python (preserves LLM control over edge cases)
