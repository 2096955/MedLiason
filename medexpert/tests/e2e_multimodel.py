import asyncio, json, time, uuid
import httpx

SAM_URL = "https://medexpert-v3-534348290993.us-central1.run.app"
TIMEOUT = 360

TESTS = [
    ("What is the first-line treatment for type 2 diabetes?", "OrchestratorAgent", "Flash"),
    ("What is the first-line treatment for type 2 diabetes?", "OrchestratorAgentPro", "Pro"),
    ("What is the first-line treatment for type 2 diabetes?", "OrchestratorAgentOpus", "Opus"),
]

async def send_query(client, question, agent_name, label):
    session_id = f"eval-{label.lower()}-{uuid.uuid4().hex[:8]}"
    payload = {
        "jsonrpc": "2.0",
        "id": f"req-{uuid.uuid4()}",
        "method": "message/stream",
        "params": {
            "message": {
                "kind": "message", "role": "user",
                "messageId": uuid.uuid4().hex,
                "contextId": session_id,
                "parts": [{"kind": "text", "text": question}],
                "metadata": {
                    "agent_name": agent_name,
                    "backgroundExecutionEnabled": True,
                    "maxExecutionTimeMs": TIMEOUT * 1000,
                }
            }
        }
    }

    start = time.time()
    try:
        resp = await client.post(f"{SAM_URL}/api/v1/message:stream", json=payload)
        resp.raise_for_status()
        task_id = resp.json().get("result", {}).get("id", "")
        if not task_id:
            return {"label": label, "error": "No task_id", "time": time.time() - start}

        full_text = ""
        tools = []
        citations = 0
        agents = set()
        last_state = ""
        errors = []

        async with client.stream("GET", f"{SAM_URL}/api/v1/sse/subscribe/{task_id}") as stream:
            async for line in stream.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except Exception:
                    continue

                result = event.get("result", {})
                state = result.get("state", "")
                last_state = state or last_state
                msg = result.get("status", {}).get("message", {})

                agent = result.get("metadata", {}).get("agent_name", "")
                if agent:
                    agents.add(agent)

                for part in msg.get("parts", []):
                    if part.get("kind") == "text":
                        txt = part.get("text", "")
                        full_text += txt
                        if "unexpected error" in txt.lower():
                            errors.append("unexpected_error_in_text")
                    data = part.get("data", {})
                    dtype = data.get("type", "")
                    if dtype == "tool_result":
                        tn = data.get("tool_name", "")
                        tools.append(tn)
                        rd = data.get("result_data", {})
                        if isinstance(rd, dict):
                            if rd.get("rag_metadata"):
                                sources = rd["rag_metadata"].get("sources", [])
                                citations += len(sources)

                if state in ("completed", "failed"):
                    if state == "failed":
                        errors.append("task_failed")
                    break

        elapsed = time.time() - start

        steps_reached = set()
        for t in tools:
            if t == "kg_search": steps_reached.add("PLAN")
            if t == "query_decomposer": steps_reached.add("PLAN")
            if t.startswith("peer_") and "Verifier" not in t and "Reviser" not in t:
                steps_reached.add("DELEGATE")
            if t == "publish_sources": steps_reached.add("COLLECT")
            if t == "report_generator": steps_reached.add("SYNTHESIZE")
            if t.startswith("peer_Verifier"): steps_reached.add("VERIFY")
            if t.startswith("peer_Reviser"): steps_reached.add("REVISE")
            if t == "graph_writer": steps_reached.add("PERSIST")

        return {
            "label": label,
            "time": elapsed,
            "text_len": len(full_text),
            "tool_count": len(tools),
            "citations": citations,
            "agents": sorted(agents),
            "steps": sorted(steps_reached),
            "last_state": last_state,
            "errors": errors,
            "tail": full_text[-300:].replace("\n", " ") if full_text else "(empty)",
        }
    except Exception as e:
        return {"label": label, "error": f"{type(e).__name__}: {e}", "time": time.time() - start}

async def main():
    print("Running E2E tests across 3 models (same question)...")
    print("Question: What is the first-line treatment for type 2 diabetes?\n")

    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT)) as client:
        tasks = [send_query(client, q, a, l) for q, a, l in TESTS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            print(f"  EXCEPTION: {r}")
            continue
        label = r.get("label", "?")
        print(f"\n{'='*60}")
        print(f"  Model: {label}")
        print(f"{'='*60}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            print(f"  Time: {r['time']:.1f}s")
        else:
            status = "PASS" if r["text_len"] > 500 and not r.get("errors") else "PARTIAL" if r["text_len"] > 0 else "FAIL"
            print(f"  Status: {status}")
            print(f"  Time: {r['time']:.1f}s")
            print(f"  Text: {r['text_len']} chars")
            print(f"  Tools: {r['tool_count']}")
            print(f"  Citations: {r['citations']}")
            print(f"  Steps: {r['steps']}")
            print(f"  Final state: {r['last_state']}")
            if r.get("errors"):
                print(f"  Errors: {r['errors']}")
            print(f"  Tail: ...{r['tail']}")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        if isinstance(r, Exception):
            print(f"  ???: EXCEPTION")
        elif "error" in r:
            print(f"  {r['label']:6s}: FAIL ({r['time']:.0f}s) - {r['error'][:60]}")
        else:
            status = "PASS" if r["text_len"] > 500 and not r.get("errors") else "PARTIAL" if r["text_len"] > 0 else "FAIL"
            steps = ",".join(r["steps"])
            print(f"  {r['label']:6s}: {status:8s} ({r['time']:.0f}s) | {r['text_len']:5d} chars | {r['citations']:2d} cites | steps: {steps}")

asyncio.run(main())
