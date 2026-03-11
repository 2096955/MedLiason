# Pipeline Quality Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 6 pipeline quality issues discovered in HITL trace analysis — bad question splitting, missing specialist routing, dropped peer calls, weak conclusions, verifier crash handling, and session state leak.

**Architecture:** 4 backend Python changes (query_decomposer, constants, report_generator, memory_plane) + 3 orchestrator YAML prompt changes (standard/pro/opus). All changes are backwards-compatible. TDD throughout.

**Tech Stack:** Python 3.10+, pytest, YAML configs.

---

### Task 1: Fix comma-clause question splitting

The `_split_question` function splits on `, which/what/how...` when the question exceeds 100 chars. This creates malformed fragments — e.g., "When considering an ERPC for a patient with haemophilia, which plan for anesthesia would likely be safest?" splits into "When considering an ERPC for a patient with haemophilia?" (not a real question) + "plan for anesthesia would likely be safest?" (lost all clinical context).

**Files:**
- Modify: `medexpert/src/lifesci_tools/query_decomposer.py:155-159`
- Test: `medexpert/tests/unit/test_query_decomposer.py`

**Step 1: Write the failing tests**

Add to the `_split_question` test section (after line ~175 in test file):

```python
def test_split_preserves_relative_clause_with_which():
    """Comma + 'which' in a relative clause should NOT split the question."""
    q = "When considering an ERPC for a patient with haemophilia, which plan for anesthesia would likely be safest?"
    parts = _split_question(q, 5)
    assert len(parts) == 1
    assert "haemophilia" in parts[0]
    assert "anesthesia" in parts[0]


def test_split_preserves_relative_clause_with_what():
    """Comma + 'what' in a dependent clause should NOT split."""
    q = "For patients with chronic kidney disease, what medication adjustments are recommended for metformin?"
    parts = _split_question(q, 5)
    assert len(parts) == 1
    assert "kidney" in parts[0]
    assert "metformin" in parts[0]


def test_split_does_split_genuine_multi_topic():
    """Genuine multi-topic questions with comma+question word SHOULD split."""
    q = "What are the side effects of metformin for diabetes management, and what alternatives exist for patients with renal impairment?"
    parts = _split_question(q, 5)
    # This splits on "and" conjunction, not comma — should still produce 2 parts
    assert len(parts) >= 2
```

**Step 2: Run tests to verify they fail**

Run: `cd medexpert && python -m pytest tests/unit/test_query_decomposer.py -k "relative_clause" -v`
Expected: FAIL — `test_split_preserves_relative_clause_with_which` produces 2 parts instead of 1.

**Step 3: Implement the fix**

In `medexpert/src/lifesci_tools/query_decomposer.py`, replace lines 155-159:

```python
                # Split on commas between clauses (only if question is long
                # AND both fragments are substantial — avoid splitting
                # relative clauses like "X, which Y?" where the comma
                # introduces a dependent clause about the same topic)
                if len(question) > 150:
                    parts = re.split(
                        r",\s+(?:what|how|why|which|where|when|who)\s+",
                        question,
                        flags=re.IGNORECASE,
                    )
                    # Only accept split if BOTH fragments are substantial
                    if len(parts) > 1 and all(len(p.strip()) >= 40 for p in parts):
                        sub_questions = [p.strip() for p in parts if p.strip()]
```

The key changes:
- Threshold raised from 100 → 150 chars (relative clauses are usually < 150 chars total)
- Both fragments must be >= 40 chars (prevents splitting dependent clauses)

**Step 4: Run tests to verify they pass**

Run: `cd medexpert && python -m pytest tests/unit/test_query_decomposer.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add medexpert/src/lifesci_tools/query_decomposer.py medexpert/tests/unit/test_query_decomposer.py
git commit --signoff -m "fix(query_decomposer): prevent comma-clause splits on relative clauses"
```

---

### Task 2: Add haemophilia/anaesthesia routing keywords

The ERPC+haemophilia question failed to route to DrugSpecialist because `haemophilia`, `hemophilia`, `coagulopathy` are not in any keyword list. Anaesthesia planning questions need both clinical_trials (for procedure evidence) AND drugs (for factor replacement).

**Files:**
- Modify: `medexpert/src/lifesci_common/constants.py:129-155` (clinical_trials keywords) and `157-206` (drugs keywords)
- Test: `medexpert/tests/unit/test_query_decomposer.py`

**Step 1: Write the failing tests**

```python
def test_route_haemophilia_erpc_gets_multiple_specialists():
    """ERPC + haemophilia should route to Literature + ClinicalTrials + Drug."""
    routes = _route_question(
        "When considering an ERPC for a patient with haemophilia, "
        "which plan for anesthesia would likely be safest?"
    )
    agents = {r["agent"] for r in routes}
    # Must include at least Literature and one of ClinicalTrials/Drug
    assert "LiteratureSpecialist" in agents
    assert len(agents) >= 2, f"Expected >= 2 specialists, got {agents}"
    # Should route to either ClinicalTrials (anesthesia keyword) or Drug (haemophilia/factor)
    assert "ClinicalTrialsSpecialist" in agents or "DrugSpecialist" in agents


def test_route_coagulation_disorder_gets_drug_specialist():
    """Coagulation/bleeding disorder questions need DrugSpecialist for factor replacement."""
    routes = _route_question(
        "What factor replacement protocol is recommended for hemophilia A patients undergoing surgery?"
    )
    agents = {r["agent"] for r in routes}
    assert "DrugSpecialist" in agents
```

**Step 2: Run tests to verify they fail**

Run: `cd medexpert && python -m pytest tests/unit/test_query_decomposer.py -k "haemophilia or coagulation" -v`
Expected: FAIL — `haemophilia` not in any keyword list, so routing is LiteratureSpecialist only.

**Step 3: Add keywords to constants.py**

In `medexpert/src/lifesci_common/constants.py`:

Add to `clinical_trials` keywords (after line 154 `"protocol"`):
```python
            "haemophilia",
            "hemophilia",
            "coagulopathy",
            "bleeding disorder",
            "ERPC",
            "perioperative management",
```

Add to `drugs` keywords (after line 180 `"sedation"`):
```python
            "haemophilia",
            "hemophilia",
            "factor replacement",
            "factor VIII",
            "factor IX",
            "desmopressin",
            "tranexamic acid",
            "coagulopathy",
            "anticoagulation",
```

Add to `literature` keywords (after line 126 `"management"`):
```python
            "anaesthetic plan",
            "anesthetic plan",
            "perioperative",
```

**Step 4: Run tests to verify they pass**

Run: `cd medexpert && python -m pytest tests/unit/test_query_decomposer.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add medexpert/src/lifesci_common/constants.py medexpert/tests/unit/test_query_decomposer.py
git commit --signoff -m "feat(routing): add haemophilia/coagulation/perioperative keywords for specialist routing"
```

---

### Task 3: Strengthen orchestrator STEP 2 batching prompt

Flash sometimes makes only 1 of N peer calls despite "Call ALL in a SINGLE turn." Strengthen the prompt with an explicit count-check instruction.

**Files:**
- Modify: `medexpert/configs/agents/orchestrator.yaml:72-77`
- Modify: `medexpert/configs/pro/agents/orchestrator.yaml:72-77`
- Modify: `medexpert/configs/opus/agents/orchestrator.yaml:78-83`

**Step 1: Update standard orchestrator YAML**

Replace the STEP 2 block in `medexpert/configs/agents/orchestrator.yaml` (lines 72-77):

```yaml
        **STEP 2 — DELEGATE (PARALLEL)**
        After query_decomposer returns, look at the `all_agents` list in its output.
        Call ONLY the peer tools listed in `all_agents` — do NOT call peer tools
        not in that list. This typically means 2-4 specialists, not all 8.

        CRITICAL BATCHING RULE: You MUST call ALL agents from `all_agents` in
        THIS SINGLE response. Count the agents in `all_agents` and verify your
        response contains exactly that many peer_* function calls. If `all_agents`
        has 2 agents, you need 2 peer_* calls. If it has 3, you need 3.
        ADK executes them in parallel. Do NOT make separate turns for each.
```

**Step 2: Replicate to pro and opus orchestrator YAMLs**

Apply the same change to:
- `medexpert/configs/pro/agents/orchestrator.yaml`
- `medexpert/configs/opus/agents/orchestrator.yaml`

**Step 3: Commit**

```bash
git add medexpert/configs/agents/orchestrator.yaml medexpert/configs/pro/agents/orchestrator.yaml medexpert/configs/opus/agents/orchestrator.yaml
git commit --signoff -m "fix(orchestrator): strengthen STEP 2 peer batching with count-check instruction"
```

---

### Task 4: Improve research_brief conclusion template

The `_format_research_brief` template always ends with "preliminary findings are presented above. Further investigation may be warranted." — a non-answer. Add a synthesis conclusion that commits to a clinical recommendation when evidence supports one.

**Files:**
- Modify: `medexpert/src/lifesci_tools/report_generator.py:86-96`
- Test: `medexpert/tests/unit/test_report_generator.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_research_brief_conclusion_not_generic(tool, ctx, sample_evidence):
    """research_brief conclusion should NOT be the generic 'Further investigation' text."""
    result = await tool._run_async_impl(
        {
            "question": "What is the safest anesthesia for hemophilia patients?",
            "evidence": json.dumps(sample_evidence),
            "mode": "research_brief",
        },
        ctx,
    )
    report = result["report"]
    # The conclusion should NOT contain the old generic text
    assert "preliminary findings are presented above" not in report.lower()
    # Should contain an actual synthesis statement
    assert "## Conclusion" in report
```

**Step 2: Run test to verify it fails**

Run: `cd medexpert && python -m pytest tests/unit/test_report_generator.py -k "conclusion_not_generic" -v`
Expected: FAIL — the old template still says "preliminary findings are presented above."

**Step 3: Implement the fix**

Replace `_format_research_brief` conclusion section (lines 86-96):

```python
    # Conclusion — synthesize instead of generic boilerplate
    lines.append("## Conclusion")
    if not evidence:
        lines.append("Insufficient evidence to draw conclusions.")
    elif len(evidence) == 1:
        title = evidence[0].get("title", "the available source")
        cite_id = evidence[0].get("cite_id", "s0r0")
        lines.append(
            f"Based on a single source ({title} [[cite:{cite_id}]]), "
            "findings should be interpreted with caution. Consult current "
            "clinical guidelines for definitive recommendations."
        )
    else:
        # Summarize the evidence themes
        titles = [ev.get("title", "") for ev in evidence[:3]]
        title_summary = ", ".join(t for t in titles if t)
        cite_ids = " ".join(
            f"[[cite:{ev.get('cite_id', f's0r{i}')}]]"
            for i, ev in enumerate(evidence[:3])
        )
        lines.append(
            f"Based on {len(evidence)} sources — including {title_summary} — "
            f"the evidence supports the key findings described above. {cite_ids} "
            "Clinical decisions should integrate these findings with patient-specific "
            "factors and current practice guidelines."
        )

    return "\n".join(lines)
```

**Step 4: Run tests**

Run: `cd medexpert && python -m pytest tests/unit/test_report_generator.py -v`
Expected: ALL PASS (update any existing tests that assert on the old "preliminary findings" text)

**Step 5: Commit**

```bash
git add medexpert/src/lifesci_tools/report_generator.py medexpert/tests/unit/test_report_generator.py
git commit --signoff -m "fix(report_generator): replace generic conclusion with evidence synthesis"
```

---

### Task 5: Verify STEP 5 hardening is deployed + session leak investigation

Two issues from trace 2:
- Verifier crash → "Research Inconclusive" (should be fixed by commit b0934d2d)
- `flush_cold` used previous session's query (session state leak)

**Files:**
- Verify: `medexpert/configs/agents/orchestrator.yaml` (STEP 5 section)
- Investigate: `medexpert/src/lifesci_tools/memory_plane.py` (flush_cold operation)

**Step 1: Verify STEP 5 hardening exists in all 3 orchestrator YAMLs**

Check that all 3 YAMLs contain the VERDICT INTERPRETATION block with:
- "MINOR_ISSUES" → proceed
- "skipped" → proceed
- Only "CRITICAL_ISSUES" → revise

Run: `grep -c "MINOR_ISSUES.*proceed\|skipped.*proceed" medexpert/configs/agents/orchestrator.yaml medexpert/configs/pro/agents/orchestrator.yaml medexpert/configs/opus/agents/orchestrator.yaml`
Expected: Each file shows count >= 1.

**Step 2: Investigate session state leak**

The flush_cold call in trace 2 passed `query="A woman in her 20s hasn't had a period"` instead of `query="What are the symptoms of ADHD?"`. This means the orchestrator's LLM re-used context from a previous conversation turn within the same DevBroker session.

Check if this is a known DevBroker behavior — when multiple queries are sent in the same browser session, the A2A context accumulates. The fix is to ensure `flush_cold` reads the query from the CURRENT task's initial request, not from the LLM's accumulated context.

In `memory_plane.py`, the `flush_cold` operation accepts `query` as a parameter — it's the LLM that passes the wrong value. The fix is prompt-side: update STEP 6 to say "Use the EXACT original user question from THIS task (the first user message), not any question from a previous task in this session."

**Step 3: Add prompt fix to STEP 6 in all 3 orchestrator YAMLs**

After the existing STEP 6 text about flush_cold, add:
```
   IMPORTANT: The `query` parameter MUST be the original user question
   from THIS task — the text that started THIS research session.
   Do NOT reuse a query from a previous conversation in this session.
```

**Step 4: Commit**

```bash
git add medexpert/configs/agents/orchestrator.yaml medexpert/configs/pro/agents/orchestrator.yaml medexpert/configs/opus/agents/orchestrator.yaml
git commit --signoff -m "fix(orchestrator): STEP 6 query parameter clarity to prevent session leak"
```

---

### Task 6: Run full test suite (preflight)

**Step 1: Backend tests**

Run: `cd medexpert && python -m pytest tests/unit/ -v --tb=short`
Expected: ALL PASS

**Step 2: Frontend tests**

Run: `cd client/webui/frontend && npx vitest run --reporter verbose`
Expected: 175+ passed, 1 pre-existing storybook failure only.

**Step 3: Commit any test fixes**

```bash
git commit --signoff -m "fix: address test regressions from pipeline quality fixes"
```

---

### Task 7: Deploy and end-to-end audit

**Step 1: Push and build**

```bash
git push origin feature/triage-pipeline
gcloud builds submit --config cloudbuild-v2.yaml --project gbg-neuro --substitutions=_TAG=$(git rev-parse --short HEAD) .
```

**Step 2: Deploy to medexpert-v3**

Update the service YAML with new image tag and `gcloud run services replace`.

**Step 3: Run Playwright audit with both test questions**

Update `docs/audit_kg_rendering.mjs` to also submit these 2 test queries (or create a separate audit script):

1. "When considering an ERPC for a patient with haemophilia, which plan for anesthesia would likely be safest?"
2. "What are the symptoms of ADHD?"

Verify:
- Query 1: Multiple specialists called (not just Literature), conclusion drawn (not "Further investigation"), mentions general anaesthesia as standard approach
- Query 2: NOT "Research Inconclusive", actual ADHD symptoms listed with citations
- Both: Sources appear in RAG panel, no session state leak between queries
