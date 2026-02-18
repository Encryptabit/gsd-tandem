"""Submit a review every second for 60 seconds to test broker throughput."""
import asyncio
import json
import httpx
import time
import sys

BROKER_URL = "http://localhost:8321/mcp"
TOTAL_REVIEWS = 60
INTERVAL = 1.0


def parse_sse_response(text: str) -> dict | None:
    """Parse SSE response to extract JSON-RPC result."""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data_str = line[6:]
            try:
                return json.loads(data_str)
            except json.JSONDecodeError:
                continue
    # Try plain JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def main():
    sys.stdout.reconfigure(line_buffering=True)
    print("Starting throughput test...", flush=True)

    async with httpx.AsyncClient(timeout=30) as client:
        # Initialize session
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "throughput-test", "version": "1.0"},
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        print("Sending initialize...", flush=True)
        resp = await client.post(BROKER_URL, json=init_payload, headers=headers)
        print(f"Init status: {resp.status_code}", flush=True)
        print(f"Init headers: {dict(resp.headers)}", flush=True)
        print(f"Init body (first 500): {resp.text[:500]}", flush=True)

        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            headers["mcp-session-id"] = session_id
            print(f"Session: {session_id[:20]}...", flush=True)
        else:
            print("WARNING: No session ID returned", flush=True)

        # Send initialized notification
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        await client.post(BROKER_URL, json=notif, headers=headers)
        print("Initialized notification sent", flush=True)

        submitted = 0
        ids = []
        t0 = time.monotonic()

        for i in range(TOTAL_REVIEWS):
            loop_start = time.monotonic()
            payload = {
                "jsonrpc": "2.0",
                "id": i + 10,
                "method": "tools/call",
                "params": {
                    "name": "create_review",
                    "arguments": {
                        "intent": f"Throughput test review #{i+1:03d}",
                        "agent_type": "throughput-test",
                        "agent_role": "proposer",
                        "phase": "load-test",
                        "description": f"Automated review {i+1} of {TOTAL_REVIEWS} for throughput testing.",
                        "category": "throughput_test",
                    },
                },
            }
            try:
                resp = await client.post(BROKER_URL, json=payload, headers=headers)
                data = parse_sse_response(resp.text)
                if data:
                    result = data.get("result", {})
                    content = result.get("content", [{}])
                    if content:
                        text = content[0].get("text", "{}")
                        parsed = json.loads(text)
                        rid = parsed.get("review_id", "???")
                        ids.append(rid)
                    submitted += 1
                    elapsed = time.monotonic() - t0
                    print(f"[{elapsed:6.1f}s] Submitted #{i+1:03d} -> {rid[:12]}...", flush=True)
                else:
                    print(f"[{time.monotonic()-t0:6.1f}s] PARSE FAIL #{i+1:03d}: {resp.text[:200]}", flush=True)
            except Exception as e:
                print(f"[{time.monotonic()-t0:6.1f}s] FAILED #{i+1:03d}: {e}", flush=True)

            # Wait remaining interval
            elapsed_loop = time.monotonic() - loop_start
            if elapsed_loop < INTERVAL and i < TOTAL_REVIEWS - 1:
                await asyncio.sleep(INTERVAL - elapsed_loop)

        total_time = time.monotonic() - t0
        print(f"\n=== DONE ===", flush=True)
        print(f"Submitted: {submitted}/{TOTAL_REVIEWS}", flush=True)
        print(f"Total time: {total_time:.1f}s", flush=True)
        print(f"Rate: {submitted/total_time:.2f} reviews/sec", flush=True)
        print(f"Review IDs: {len(ids)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
