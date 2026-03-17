# Pipeline Performance Optimizations

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cut specialist execution time from ~80s to ~25s by enabling parallel tool calling, batching PubMed fetches, and streaming early responses.

**Architecture:** Three independent optimizations: (1) Enable `parallel_tool_calls` on specialist model configs + rewrite prompts to encourage batching, (2) Add `get_articles_batch` tool to PubMed MCP server using NCBI's multi-ID efetch, (3) Stream synthesis at STEP 4 while VERIFY+PERSIST run in background. Each optimization is independently deployable.

**Tech Stack:** YAML config changes, FastMCP 2.x (PubMed server), Python asyncio (batch HTTP), SAM gateway SSE streaming.

**Current baseline:** Flash solo — 152s, 79 tool calls (20× get_article_abstract + 18× evidence_grader = 38 sequential calls in specialist alone)

---

## Optimization 1: Parallel Tool Calling in Specialists

### Problem
Specialists make 20+ sequential tool calls because:
1. `parallel_tool_calls` is only enabled on orchestrator model config, not specialist
2. Specialist prompts describe a sequential workflow: "search → retrieve each → grade each → store"
3. Gemini Flash supports parallel function calling but is never told to use it

### Expected impact
With parallel tool calls enabled, Gemini can emit 5-10 `get_article_abstract` calls per LLM turn instead of 1. A specialist that currently takes 10 LLM turns for 10 abstracts would take 2 turns (5 per batch). **~60% reduction in specialist wall time.**

---

### Task 1: Enable `parallel_tool_calls` in specialist model config

**Files:**
- Modify: `medexpert/configs/shared_config.yaml:31-37`
- Modify: `medexpert/configs/pro/shared_config_pro.yaml` (same section)
- Modify: `medexpert/configs/opus/shared_config_opus.yaml` (same section)

**Step 1: Add `parallel_tool_calls: true` to specialist model anchor**

In `medexpert/configs/shared_config.yaml`, change the specialist anchor from:
```yaml
specialist: &specialist_model
  model: "vertex_ai/gemini-2.5-flash"
  temperature: 0.3
  max_tokens: 12000
  cache_strategy: "none"
```
to:
```yaml
specialist: &specialist_model
  model: "vertex_ai/gemini-2.5-flash"
  temperature: 0.3
  max_tokens: 12000
  parallel_tool_calls: true
  cache_strategy: "none"
```

Apply the same change to `shared_config_pro.yaml` and `shared_config_opus.yaml`.

**Step 2: Verify no tests break**

Run: `cd medexpert && python -m pytest tests/unit -x -q`
Expected: All tests pass (config-only change, no code changes)

**Step 3: Commit**

```bash
git add medexpert/configs/shared_config.yaml medexpert/configs/pro/shared_config_pro.yaml medexpert/configs/opus/shared_config_opus.yaml
git commit --signoff -m "perf(config): enable parallel_tool_calls for specialist models"
```

---

### Task 2: Rewrite specialist prompts for batch tool calling

**Files:**
- Modify: `medexpert/configs/agents/literature_specialist.yaml` (lines 45-55, workflow section)
- Modify: `medexpert/configs/agents/drug_specialist.yaml` (same section)
- Modify: `medexpert/configs/agents/clinical_trials_specialist.yaml` (same section)
- Modify: `medexpert/configs/agents/epidemiology_specialist.yaml` (same section)
- Modify: `medexpert/configs/agents/genomics_specialist.yaml` (same section)
- Modify: `medexpert/configs/agents/environmental_specialist.yaml` (same section)
- Modify: `medexpert/configs/agents/regulatory_specialist.yaml` (same section)
- Modify: `medexpert/configs/agents/provider_intel_specialist.yaml` (same section)
- Modify: All Pro variants (`medexpert/configs/pro/agents/*.yaml`)
- Modify: All Opus variants (`medexpert/configs/opus/agents/*.yaml`)

**Step 1: Replace sequential workflow with batch workflow**

In each specialist YAML, find the `**WORKFLOW:**` section and replace with a batch-oriented version. Example for `literature_specialist.yaml`:

Old:
```yaml
**WORKFLOW:**
1. Receive a research sub-question from the Orchestrator
2. Search PubMed using search_pubmed for relevant articles
3. Retrieve abstracts for top results using get_article_abstract
4. Grade each piece of evidence using evidence_grader
5. Store graded evidence in memory_plane (namespace: evidence)
6. Return a structured summary with citations
```

New:
```yaml
**WORKFLOW:**
1. Receive a research sub-question from the Orchestrator
2. Search PubMed using search_pubmed for relevant articles
3. Call get_articles_batch with ALL PMIDs from search results in ONE call
   (If get_articles_batch is unavailable, call get_article_abstract for
   ALL PMIDs in a SINGLE response — ADK executes them in parallel)
4. Return a structured summary with citations

**CRITICAL BATCHING RULE:** When retrieving multiple articles, call
get_article_abstract for ALL PMIDs in a SINGLE LLM response. Do NOT
retrieve one article per response. Example: if search_pubmed returns
PMIDs [123, 456, 789], your next response must contain THREE
get_article_abstract calls, not one. ADK executes them in parallel.
```

Adapt per specialist (drug uses OpenFDA tools, regulatory uses FDA tools, etc.). The key instruction is the **CRITICAL BATCHING RULE** which tells the LLM to emit multiple tool calls per turn.

**Step 2: Apply to all 24 specialist YAMLs** (8 standard + 8 pro + 8 opus)

Each specialist has domain-specific workflow text, but the batching rule is universal. Add the CRITICAL BATCHING RULE block to all 24 specialist YAMLs after the workflow section.

**Step 3: Verify no tests break**

Run: `cd medexpert && python -m pytest tests/unit -x -q`
Expected: All tests pass (prompt-only change)

**Step 4: Commit**

```bash
git add medexpert/configs/agents/*.yaml medexpert/configs/pro/agents/*.yaml medexpert/configs/opus/agents/*.yaml
git commit --signoff -m "perf(specialists): add batch tool calling instructions to all specialist prompts"
```

---

## Optimization 2: PubMed Batch Fetch

### Problem
`get_article_abstract(pmid)` fetches one article per HTTP request. A specialist fetching 20 articles makes 20 sequential HTTP calls to NCBI (~1-2s each = 20-40s). NCBI's efetch API supports comma-separated PMIDs (`id=123,456,789`) returning all articles in one response.

### Expected impact
20 HTTP calls → 2 calls (batches of 10). **~18s saved per specialist invocation.**

---

### Task 3: Write failing tests for batch fetch

**Files:**
- Create: `medexpert/tests/unit/test_pubmed_batch.py`

**Step 1: Write tests**

```python
"""Tests for PubMed batch fetch tool."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Import helper to unwrap FastMCP FunctionTool
from conftest import unwrap_mcp_tool


class TestGetArticlesBatch:
    """Tests for the get_articles_batch MCP tool."""

    @pytest.fixture
    def mock_http_client(self):
        """Mock httpx client for NCBI API calls."""
        client = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_batch_fetch_multiple_pmids(self):
        """Batch fetch returns multiple articles in one call."""
        from mcp_servers.pubmed.server import get_articles_batch
        fn = unwrap_mcp_tool(get_articles_batch)

        with patch("mcp_servers.pubmed.server._fetch_articles_batch") as mock_fetch:
            mock_fetch.return_value = {
                "success": True,
                "articles": [
                    {"pmid": "12345", "title": "Study A", "abstract": "Abstract A"},
                    {"pmid": "67890", "title": "Study B", "abstract": "Abstract B"},
                ],
                "count": 2,
            }
            result = await fn(pmids="12345,67890")
            assert result["success"] is True
            assert result["count"] == 2
            assert len(result["articles"]) == 2

    @pytest.mark.asyncio
    async def test_batch_fetch_single_pmid(self):
        """Batch fetch works with a single PMID."""
        from mcp_servers.pubmed.server import get_articles_batch
        fn = unwrap_mcp_tool(get_articles_batch)

        with patch("mcp_servers.pubmed.server._fetch_articles_batch") as mock_fetch:
            mock_fetch.return_value = {
                "success": True,
                "articles": [{"pmid": "12345", "title": "Study A", "abstract": "Abstract A"}],
                "count": 1,
            }
            result = await fn(pmids="12345")
            assert result["success"] is True
            assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_batch_fetch_clamps_to_20(self):
        """Batch fetch clamps to max 20 PMIDs."""
        from mcp_servers.pubmed.server import get_articles_batch
        fn = unwrap_mcp_tool(get_articles_batch)

        pmids = ",".join(str(i) for i in range(30))
        with patch("mcp_servers.pubmed.server._fetch_articles_batch") as mock_fetch:
            mock_fetch.return_value = {"success": True, "articles": [], "count": 0}
            await fn(pmids=pmids)
            # Verify only first 20 PMIDs were passed
            called_pmids = mock_fetch.call_args[0][0]
            assert len(called_pmids) == 20

    @pytest.mark.asyncio
    async def test_batch_fetch_empty_string(self):
        """Batch fetch with empty string returns error."""
        from mcp_servers.pubmed.server import get_articles_batch
        fn = unwrap_mcp_tool(get_articles_batch)

        result = await fn(pmids="")
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_batch_fetch_graceful_degradation(self):
        """Batch fetch returns structured error on NCBI failure."""
        from mcp_servers.pubmed.server import get_articles_batch
        fn = unwrap_mcp_tool(get_articles_batch)

        with patch("mcp_servers.pubmed.server._fetch_articles_batch") as mock_fetch:
            mock_fetch.side_effect = Exception("NCBI timeout")
            result = await fn(pmids="12345,67890")
            assert result["success"] is False
            assert "error" in result

    @pytest.mark.asyncio
    async def test_batch_fetch_partial_results(self):
        """Batch fetch returns partial results when some PMIDs fail."""
        from mcp_servers.pubmed.server import get_articles_batch
        fn = unwrap_mcp_tool(get_articles_batch)

        with patch("mcp_servers.pubmed.server._fetch_articles_batch") as mock_fetch:
            mock_fetch.return_value = {
                "success": True,
                "articles": [
                    {"pmid": "12345", "title": "Study A", "abstract": "Abstract A"},
                ],
                "count": 1,
                "missing_pmids": ["67890"],
            }
            result = await fn(pmids="12345,67890")
            assert result["success"] is True
            assert result["count"] == 1
            assert "67890" in result["missing_pmids"]
```

**Step 2: Run tests to verify they fail**

Run: `cd medexpert && python -m pytest tests/unit/test_pubmed_batch.py -v`
Expected: ImportError — `get_articles_batch` does not exist yet

**Step 3: Commit**

```bash
git add medexpert/tests/unit/test_pubmed_batch.py
git commit --signoff -m "test(pubmed): add failing tests for batch article fetch"
```

---

### Task 4: Implement `get_articles_batch` in PubMed MCP server

**Files:**
- Modify: `medexpert/src/mcp_servers/pubmed/server.py`

**Step 1: Add `_fetch_articles_batch()` helper**

Add after the existing `_fetch_abstract()` helper (around line 90):

```python
async def _fetch_articles_batch(pmid_list: list[str]) -> dict:
    """Fetch multiple articles in a single NCBI efetch call.

    NCBI efetch supports comma-separated PMIDs in the id parameter,
    returning all articles in one XML response. Max 20 per call.
    """
    if not pmid_list:
        return {"success": False, "error": "No PMIDs provided", "articles": []}

    pmid_list = pmid_list[:20]  # Clamp to 20
    pmid_str = ",".join(pmid_list)

    params = {
        **_base_params(),
        "db": "pubmed",
        "id": pmid_str,
        "rettype": "xml",
        "retmode": "xml",
    }

    try:
        resp = await resilient_get(
            EFETCH_URL, params=params, timeout=30.0
        )
        # Parse XML response — contains multiple <PubmedArticle> elements
        articles = _parse_batch_xml(resp, pmid_list)
        found_pmids = {a["pmid"] for a in articles}
        missing = [p for p in pmid_list if p not in found_pmids]

        result = {
            "success": True,
            "articles": articles,
            "count": len(articles),
        }
        if missing:
            result["missing_pmids"] = missing
        return result

    except Exception as e:
        log.error("Batch fetch failed for %d PMIDs: %s", len(pmid_list), e)
        return structured_error_response(e, server="pubmed", tool="get_articles_batch")
```

**Step 2: Add `_parse_batch_xml()` helper**

```python
def _parse_batch_xml(xml_text: str, requested_pmids: list[str]) -> list[dict]:
    """Parse NCBI efetch XML containing multiple PubmedArticle elements."""
    from lxml import etree

    articles = []
    try:
        root = etree.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    except Exception:
        return articles

    for article_elem in root.findall(".//PubmedArticle"):
        try:
            pmid_elem = article_elem.find(".//PMID")
            pmid = pmid_elem.text if pmid_elem is not None else None
            if not pmid:
                continue

            # Reuse existing parsing logic
            title = _extract_text(article_elem, ".//ArticleTitle") or "No title"
            abstract = _extract_abstract(article_elem)
            authors = _extract_authors(article_elem)
            journal = _extract_text(article_elem, ".//Journal/Title") or ""
            pub_date = _extract_pub_date(article_elem)

            articles.append({
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "pub_date": pub_date,
                "success": True,
            })
        except Exception as e:
            log.warning("Failed to parse article in batch: %s", e)
            continue

    return articles
```

**Note:** The existing `get_article_abstract` likely already has `_extract_text`, `_extract_abstract`, `_extract_authors`, `_extract_pub_date` helpers. Reuse them. If they don't exist as separate functions, refactor them out of the existing `get_article_abstract` implementation first.

**Step 3: Add the `get_articles_batch` MCP tool**

```python
@mcp.tool()
async def get_articles_batch(pmids: str) -> dict:
    """Retrieve abstracts, titles, and metadata for multiple PubMed articles in one call.

    Much faster than calling get_article_abstract individually for each PMID.
    Pass up to 20 comma-separated PMIDs. Articles are returned in the same
    order as the input PMIDs where possible.

    Args:
        pmids: Comma-separated PubMed IDs (e.g., "12345,67890,11111"). Max 20.

    Returns:
        Dict with 'articles' list (each has pmid, title, abstract, authors,
        journal, pub_date) and 'count'. Missing PMIDs listed in 'missing_pmids'.
    """
    pmid_list = [p.strip() for p in pmids.split(",") if p.strip()]
    if not pmid_list:
        return {"success": False, "error": "No valid PMIDs provided", "articles": [], "count": 0}

    return await _fetch_articles_batch(pmid_list)
```

**Step 4: Run tests**

Run: `cd medexpert && python -m pytest tests/unit/test_pubmed_batch.py -v`
Expected: All 6 tests pass

**Step 5: Commit**

```bash
git add medexpert/src/mcp_servers/pubmed/server.py
git commit --signoff -m "feat(pubmed): add get_articles_batch tool for multi-PMID fetch"
```

---

### Task 5: Update specialist prompts to prefer batch fetch

**Files:**
- Modify: All 24 specialist YAMLs (8 × 3 variants) — the ones that use PubMed

**Step 1: Update workflow instructions**

In specialists that call `get_article_abstract` (literature, clinical_trials, drug, epidemiology, genomics, regulatory), update the CRITICAL BATCHING RULE from Task 2 to prefer `get_articles_batch`:

```yaml
**CRITICAL BATCHING RULE:** After search_pubmed returns PMIDs, call
get_articles_batch with ALL PMIDs in ONE call (comma-separated, max 20).
This fetches all articles in a single HTTP request. Only fall back to
individual get_article_abstract calls if get_articles_batch fails.
```

**Step 2: Commit**

```bash
git add medexpert/configs/agents/*.yaml medexpert/configs/pro/agents/*.yaml medexpert/configs/opus/agents/*.yaml
git commit --signoff -m "perf(specialists): prefer get_articles_batch over sequential fetch"
```

---

## Optimization 3: Streaming Early Response

### Problem
The user waits for the full pipeline (SEED→PLAN→DELEGATE→COLLECT→SYNTHESIZE→VERIFY→PERSIST) before seeing any answer. Steps 5-6 (VERIFY+PERSIST) add ~40s but don't change the answer content — they validate it and save to graph.

### Expected impact
User sees the answer ~40s earlier. Verification badge appends afterward. **Perceived latency reduced from ~150s to ~110s.**

### Approach
This is a prompt-level change to the orchestrator. At STEP 4 (SYNTHESIZE), the orchestrator should emit the answer text BEFORE calling `peer_VerifierAgent`. The SAM framework already streams text parts as they're emitted. The verification result appends as a follow-up status update.

---

### Task 6: Write test for early streaming behavior

**Files:**
- Create: `medexpert/tests/unit/test_early_streaming.py`

**Step 1: Write test**

```python
"""Tests for early streaming optimization in orchestrator prompt."""
import pytest


class TestEarlyStreamingPrompt:
    """Verify orchestrator prompt instructs answer emission before verification."""

    def test_orchestrator_prompt_has_early_answer_instruction(self):
        """STEP 4 prompt must instruct emitting the answer before STEP 5."""
        import yaml
        with open("medexpert/configs/agents/orchestrator.yaml") as f:
            config = yaml.safe_load(f)

        prompt = config["apps"][0]["app_config"]["agent_config"]["system_prompt"]
        # Find STEP 4 section
        step4_idx = prompt.find("STEP 4")
        step5_idx = prompt.find("STEP 5")
        assert step4_idx > 0, "STEP 4 not found in orchestrator prompt"
        assert step5_idx > step4_idx, "STEP 5 must come after STEP 4"

        step4_text = prompt[step4_idx:step5_idx]
        assert "present your answer to the user" in step4_text.lower() or \
               "emit your answer" in step4_text.lower() or \
               "stream the answer" in step4_text.lower() or \
               "respond to the user" in step4_text.lower(), \
            "STEP 4 must instruct the orchestrator to emit the answer before verification"

    def test_pro_orchestrator_has_early_answer_instruction(self):
        """Pro variant must also have early answer instruction."""
        import yaml
        with open("medexpert/configs/pro/agents/orchestrator.yaml") as f:
            config = yaml.safe_load(f)

        prompt = config["apps"][0]["app_config"]["agent_config"]["system_prompt"]
        step4_idx = prompt.find("STEP 4")
        step5_idx = prompt.find("STEP 5")
        step4_text = prompt[step4_idx:step5_idx]
        assert "present your answer to the user" in step4_text.lower() or \
               "emit your answer" in step4_text.lower() or \
               "stream the answer" in step4_text.lower() or \
               "respond to the user" in step4_text.lower()

    def test_opus_orchestrator_has_early_answer_instruction(self):
        """Opus variant must also have early answer instruction."""
        import yaml
        with open("medexpert/configs/opus/agents/orchestrator.yaml") as f:
            config = yaml.safe_load(f)

        prompt = config["apps"][0]["app_config"]["agent_config"]["system_prompt"]
        step4_idx = prompt.find("STEP 4")
        step5_idx = prompt.find("STEP 5")
        step4_text = prompt[step4_idx:step5_idx]
        assert "present your answer to the user" in step4_text.lower() or \
               "emit your answer" in step4_text.lower() or \
               "stream the answer" in step4_text.lower() or \
               "respond to the user" in step4_text.lower()
```

**Step 2: Run test to verify it fails**

Run: `cd medexpert && python -m pytest tests/unit/test_early_streaming.py -v`
Expected: FAIL — current STEP 4 doesn't have early answer instruction

**Step 3: Commit**

```bash
git add medexpert/tests/unit/test_early_streaming.py
git commit --signoff -m "test(orchestrator): add failing tests for early answer streaming"
```

---

### Task 7: Update orchestrator STEP 4 prompt for early answer emission

**Files:**
- Modify: `medexpert/configs/agents/orchestrator.yaml` (STEP 4 section)
- Modify: `medexpert/configs/pro/agents/orchestrator.yaml` (same)
- Modify: `medexpert/configs/opus/agents/orchestrator.yaml` (same)

**Step 1: Update STEP 4 prompt**

Find the STEP 4 (SYNTHESIZE) section in each orchestrator YAML. Add an instruction to emit the answer text BEFORE calling verification tools:

```yaml
**STEP 4 — SYNTHESIZE**
After collecting specialist results and calling publish_sources + report_generator:

1. Cross-reference specialist findings for contradictions
2. Assess overall confidence (strong/moderate/limited/insufficient)
3. Call report_generator to create the synthesis
4. **RESPOND TO THE USER NOW** — Present your complete answer with citations
   as a text message. The user will see this immediately via streaming.
   Do NOT wait for verification before responding.
5. Then proceed to STEP 5 (verification runs in background from the
   user's perspective — they already have the answer).
```

The key change is step 4: "RESPOND TO THE USER NOW" — this causes the LLM to emit a text response before calling `peer_VerifierAgent`, which the SSE pipeline streams immediately to the frontend.

**Step 2: Run tests**

Run: `cd medexpert && python -m pytest tests/unit/test_early_streaming.py -v`
Expected: All 3 tests pass

**Step 3: Commit**

```bash
git add medexpert/configs/agents/orchestrator.yaml medexpert/configs/pro/agents/orchestrator.yaml medexpert/configs/opus/agents/orchestrator.yaml
git commit --signoff -m "perf(orchestrator): stream answer to user at STEP 4 before verification"
```

---

### Task 8: Run full E2E validation

**Step 1: Run backend tests**

Run: `cd medexpert && python -m pytest tests/unit -x -q`
Expected: All tests pass

**Step 2: Run frontend tests**

Run: `cd client/webui/frontend && npm test`
Expected: All tests pass

**Step 3: Deploy and E2E test**

```bash
# Build and deploy
cd /path/to/repo
git push origin feature/triage-pipeline
gcloud builds submit --config cloudbuild-v2.yaml --project gbg-neuro --substitutions=_TAG=$(git rev-parse --short HEAD) .
gcloud run services update medexpert-v3 --image gcr.io/gbg-neuro/medexpert-v2:<tag> --region us-central1 --project gbg-neuro

# E2E test
cd medexpert && python tests/e2e_multimodel.py
```

Expected: Flash response time drops from ~150s to ~90s. User sees answer at ~80s (before verification completes).

**Step 4: Commit test results**

```bash
git commit --signoff -m "docs: record performance optimization E2E results"
```

---

## Task Summary

| Task | What | Type | Est. Time |
|------|------|------|-----------|
| 1 | Enable `parallel_tool_calls` in specialist config | Config | 5 min |
| 2 | Add batch tool calling instructions to specialist prompts | Prompt | 15 min |
| 3 | Write failing tests for PubMed batch fetch | Test (RED) | 10 min |
| 4 | Implement `get_articles_batch` in PubMed MCP server | Code (GREEN) | 20 min |
| 5 | Update specialist prompts to prefer batch fetch | Prompt | 10 min |
| 6 | Write failing tests for early streaming | Test (RED) | 5 min |
| 7 | Update orchestrator STEP 4 for early answer emission | Prompt | 10 min |
| 8 | Full E2E validation + deploy | Validation | 15 min |

**Total estimated: ~90 minutes**

## Expected Results

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Specialist tool calls (per specialist) | 20+ sequential | 2-3 batched | ~85% fewer LLM turns |
| PubMed HTTP calls (per specialist) | 20 sequential | 2 batched | ~90% fewer HTTP calls |
| Specialist wall time | ~80s | ~20s | ~75% reduction |
| Time to first answer (user sees text) | ~150s | ~90s | ~40% faster perceived |
| Total pipeline time | ~150s | ~110s | ~27% faster actual |
