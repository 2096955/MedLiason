"""Sequential E2E test across all 3 models — one at a time to avoid contention."""
import asyncio, json, time, uuid
import httpx

SAM_URL = "https://medexpert-v3-534348290993.us-central1.run.app"
TIMEOUT = 360
QUESTION = "What is the first-line treatment for type 2 diabetes?"

MODELS = [
    ("OrchestratorAgent", "Flash"),
    ("OrchestratorAgentPro", "Pro"),
    ("OrchestratorAgentOpus", "Opus"),
]

async def test_model(client, agent_name, label):
    session_id = f"eval-{label.lower()}-seq-{uuid.uuid4().hex[:8]}"
    payload = {
        "jsonrpc": "2.0",
        "id": f"req-{uuid.uuid4()}",
        "method": "message/stream",
        "params": {
            "message": {
                "kind": "message", "role": "user",
                "messageId": uuid.uuid4().hex,
                "contextId": session_id,
                "parts": [{"kind": "text", "text": QUESTION}],
                "metadata": {
                    "agent_name": agent_name,
                    "backgroundExecutionEnabled": True,
                    "maxExecutionTimeMs": TIMEOUT * 1000,
                },
            }
        },
    }

    start = time.time()
    try:
        resp = await client.post(f"{SAM_URL}/api/v1/message:stream", json=payload)
        resp.raise_for_status()
        task_id = resp.json().get("result", {}).get("id", "")
        if not task_id:
            return {"label": label, "status": "FAIL", "error": "No task_id", "time": 0}

        text = ""
        tools = []
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
                msg = result.get("status", {}).get("message", {})

                for part in msg.get("parts", []):
                    if part.get("kind") == "text":
                        t = part.get("text", "")
                        text += t
                        if "unexpected error" in t.lower():
                            errors.append("unexpected_error")
                    data = part.get("data", {})
                    if data.get("type") == "tool_result":
                        tools.append(data.get("tool_name", ""))

                if state in ("completed", "failed"):
                    break

        elapsed = time.time() - start
        status = "PASS" if len(text) > 500 and not errors else "PARTIAL" if text and not errors else "FAIL"

        return {
            "label": label,
            "status": status,
            "time": elapsed,
            "text_len": len(text),
            "tool_count": len(tools),
            "errors": errors,
            "first_tools": tools[:5],
            "last_tools": tools[-5:],
            "tail": text[-200:].replace("\n", " ") if text else "(empty)",
        }
    except Exception as e:
        return {"label": label, "status": "FAIL", "error": str(e), "time": time.time() - start}


async def main():
    print(f"Sequential E2E test: {QUESTION}")
    print(f"Running Flash -> Pro -> Opus (one at a time)\n")

    results = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT)) as client:
        for agent_name, label in MODELS:
            print(f"--- {label} ({agent_name}) ---")
            r = await test_model(client, agent_name, label)
            results.append(r)

            print(f"  Status: {r['status']}")
            print(f"  Time: {r.get('time', 0):.1f}s")
            print(f"  Text: {r.get('text_len', 0)} chars")
            print(f"  Tools: {r.get('tool_count', 0)}")
            if r.get("errors"):
                print(f"  Errors: {r['errors']}")
            if r.get("error"):
                print(f"  Error: {r['error']}")
            if r.get("first_tools"):
                print(f"  First: {r['first_tools']}")
                print(f"  Last:  {r['last_tools']}")
            if r.get("tail") and r["tail"] != "(empty)":
                print(f"  Tail: ...{r['tail']}")
            print()

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        t = f"{r.get('time', 0):.0f}s"
        txt = f"{r.get('text_len', 0)} chars"
        tools = f"{r.get('tool_count', 0)} tools"
        print(f"  {r['label']:6s}: {r['status']:8s} | {t:>5s} | {txt:>10s} | {tools}")


asyncio.run(main())
