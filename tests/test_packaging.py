"""
Checkpoint (Phase 5, Hissa A/B/C): the app runs exactly as Railway will start it, and
emits structured JSON logs. ~1-2 min (launches uvicorn + one real turn).

Proves:
- the app boots via `uvicorn backend.app:app` (the railway.json startCommand)
- /health works; one turn streams turn_start .. agent_end over SSE
- logs are JSON lines, and a structured `turn_complete` event with duration_s is emitted
- (soft) SSE heartbeats keep an idle stream alive

Run:  .venv/bin/python tests/test_packaging.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

ROOT = Path(__file__).parent.parent
PORT = 8097
BASE = f"http://127.0.0.1:{PORT}"
UID, TOKEN = "pkg", "tok_pkg"
LOG = ROOT / "tests" / "_uvicorn_pkg.log"
checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def main() -> int:
    load_dotenv()
    env = dict(os.environ)
    env["USER_TOKENS"] = f"{TOKEN}:{UID}"

    print("== launching uvicorn (railway.json startCommand) ==")
    logf = open(LOG, "w")
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "backend.app:app",
         "--host", "0.0.0.0", "--port", str(PORT), "--log-level", "info"],
        cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT,
    )
    try:
        up = False
        for _ in range(30):
            try:
                if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(1)
        check("boots via uvicorn + /health", up)
        if not up:
            return finish(proc, logf)

        print("== one turn over SSE ==")
        types, heartbeats = [], 0
        with httpx.stream("POST", f"{BASE}/users/{UID}/messages",
                          headers={"Authorization": f"Bearer {TOKEN}"},
                          json={"message": "Reply with exactly the word PKG_OK and nothing else."},
                          timeout=180) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    types.append(ev.get("type"))
                    if ev.get("type") == "heartbeat":
                        heartbeats += 1
                    if ev.get("type") == "agent_end":
                        break
        check("turn_start + agent_end streamed", "turn_start" in types and "agent_end" in types)
        print(f"   (heartbeats seen: {heartbeats} — soft; a fast turn may not idle 15s)")

    finally:
        finish(proc, logf)

    # inspect the captured logs
    print("== structured JSON logs ==")
    lines = LOG.read_text().splitlines()
    json_lines, turn_complete = [], None
    for ln in lines:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        json_lines.append(o)
        if o.get("event") == "turn_complete":
            turn_complete = o
    check("logs are JSON lines", len(json_lines) >= 1, f"({len(json_lines)} of {len(lines)})")
    check("turn_complete event with duration", turn_complete is not None
          and "duration_s" in turn_complete, str(turn_complete)[:160] if turn_complete else "(none)")

    LOG.unlink(missing_ok=True)
    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("PACKAGING OK — ready for Railway (Hissa D)"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


def finish(proc, logf) -> int:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
