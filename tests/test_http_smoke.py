"""
Phase 2 exit test: the real HTTP/SSE surface (~1-2 min).

Launches uvicorn as a subprocess, waits for /health, then drives one turn over
Server-Sent Events exactly as a frontend would — proving the whole stack end to
end: HTTP -> TurnRunner -> sandbox -> daemon -> events -> SSE -> client.

Run:  .venv/bin/python tests/test_http_smoke.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"
UID = "http_test"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def main() -> int:
    print("== launching uvicorn ==")
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "backend.app:app",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT,
    )
    try:
        # wait for /health (first boot imports the app + asserts secrets)
        up = False
        for _ in range(30):
            try:
                if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(1)
        check("server healthy", up)
        if not up:
            return finish(proc)

        print("== one turn over SSE ==")
        events: list[dict] = []
        with httpx.stream(
            "POST", f"{BASE}/users/{UID}/messages",
            json={"message": "Reply with exactly the word HTTP_OK and nothing else."},
            timeout=180,
        ) as r:
            check("200 + event-stream", r.status_code == 200
                  and "text/event-stream" in r.headers.get("content-type", ""))
            for line in r.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    events.append(ev)
                    if ev.get("type") == "agent_end":
                        break

        types = [e.get("type") for e in events]
        check("turn_start streamed", "turn_start" in types)
        check("agent_end streamed", "agent_end" in types)
        check("answer present", "HTTP_OK" in json.dumps(events))

        print("== reset endpoint ==")
        rr = httpx.post(f"{BASE}/users/{UID}/reset", timeout=60)
        check("reset ok", rr.status_code == 200 and rr.json().get("ok"))

    finally:
        finish(proc)

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("HTTP SURFACE OK — Phase 2 COMPLETE; next: Phase 3 (multi-user)"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


def finish(proc: subprocess.Popen) -> int:
    print("== stopping uvicorn ==")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
