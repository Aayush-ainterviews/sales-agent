"""
Checkpoint I (Phase 4 finale): the whole outreach path over real HTTP. ~2-4 min.
Sends ONE real email to your SEND_OVERRIDE_TO inbox (never a real lead).

Flow (a human's view):
1. a pending batch exists (inserted here — collection already proven in Checkpoint G)
2. GET /batches?status=pending  -> the human sees it
3. GET /batches/{id}            -> full detail
4. POST /batches/{id}/approve   -> Backend sends via ZeptoMail -> status 'sent'
5. GET /batches?status=sent     -> moved
6. feedback turn: the agent's session receives the send result (poll the log)

Needs: Docker Postgres up; .env with ZEPTOMAIL_* + SEND_OVERRIDE_TO; E2B key.
Run:  .venv/bin/python tests/test_phase4_e2e.py
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

from backend import db  # noqa: E402
from backend.batches import PendingBatches  # noqa: E402
from backend.registry import Registry  # noqa: E402
from backend.turn_runner import TurnRunner  # noqa: E402

ROOT = Path(__file__).parent.parent
PORT = 8098
BASE = f"http://127.0.0.1:{PORT}"
UID, TOKEN = "e2e", "tok_e2e"
checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def hdr():
    return {"Authorization": f"Bearer {TOKEN}"}


def main() -> int:
    load_dotenv()
    env = dict(os.environ)
    env["USER_TOKENS"] = f"{TOKEN}:{UID}"

    pb = PendingBatches(db.pool())
    reg = Registry(db.pool())
    runner_for_cleanup = TurnRunner(reg)
    # clean slate
    runner_for_cleanup.reset(UID)
    for st in ("pending", "approved", "sent", "rejected", "invalid", "failed"):
        for b in pb.list_by_status(st, UID):
            pb.delete(b.id)

    print("== launching uvicorn ==")
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "backend.app:app",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env,
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
        check("server healthy", up)
        if not up:
            return finish(proc, runner_for_cleanup)

        print("== 1. a pending batch exists ==")
        bid = pb.insert(UID, {
            "campaign": "e2e-test",
            "leads": [{"lead_id": "L1", "name": "Test", "email": "dummy@example.com",
                       "subject": "e2e outreach", "body": "<p>e2e test body</p>", "evidence": ["test"]}],
        })
        check("batch queued", bool(bid))

        print("== 2/3. human reviews ==")
        r = httpx.get(f"{BASE}/users/{UID}/batches?status=pending", headers=hdr(), timeout=15)
        listed = r.json().get("batches", [])
        check("GET pending lists it", any(b["id"] == bid for b in listed))
        r = httpx.get(f"{BASE}/users/{UID}/batches/{bid}", headers=hdr(), timeout=15)
        check("GET detail has leads", r.status_code == 200 and len(r.json()["batch"]["leads"]) == 1)

        print("== 4. approve -> real send ==")
        r = httpx.post(f"{BASE}/users/{UID}/batches/{bid}/approve", headers=hdr(), timeout=60)
        body = r.json()
        print(f"   approve result: {body}")
        check("approve 200 + sent", r.status_code == 200 and body.get("status") == "sent"
              and body["result"]["sent"] == 1)

        print("== 5. batch moved to sent ==")
        r = httpx.get(f"{BASE}/users/{UID}/batches?status=sent", headers=hdr(), timeout=15)
        check("now in 'sent'", any(b["id"] == bid for b in r.json().get("batches", [])))

        print("== 6. feedback turn reaches the agent's session (polling log) ==")
        got_fb = False
        for _ in range(45):  # up to ~90s: sandbox create + daemon + turn
            log_text = reg.get_log(UID)
            if log_text and "e2e-test" in log_text:
                got_fb = True
                break
            time.sleep(2)
        check("agent session got the send result", got_fb)

    finally:
        finish(proc, runner_for_cleanup)
        for b in pb.list_by_status("sent", UID) + pb.list_by_status("pending", UID):
            pb.delete(b.id)

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("PHASE 4 COMPLETE — draft → approve → send → feedback, end to end"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


def finish(proc, runner) -> int:
    print("== cleanup ==")
    try:
        runner.reset(UID)
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
