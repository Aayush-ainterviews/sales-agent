"""
Phase 3 exit test: multi-user isolation + concurrency (~3-5 min, 3 real sandboxes).

Launches uvicorn with 3 internal users and tries to BREAK isolation:
A. three turns fired at once, each asked for a unique word -> every stream contains
   only its OWN word, never another user's (no cross-talk); wall-clock shows parallel
B. same-user second /messages while one streams -> HTTP 409 (Q17)
C. steer routes by user: steering an idle user -> 409, while an active user's turn
   is untouched; steering the active user actually lands (STEERED)
D. auth: unknown token -> 401; one user's token on another's path -> 403

Resets all users at the end.
Run:  .venv/bin/python tests/test_multiuser.py
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
PORT = 8098
BASE = f"http://127.0.0.1:{PORT}"

TOKENS = {"tok_a": "mu_a", "tok_b": "mu_b", "tok_c": "mu_c"}
USER_TOKENS_ENV = ",".join(f"{t}:{u}" for t, u in TOKENS.items())
# A bare `sleep` the model can't shortcut: pi's bash tool blocks on it, keeping the
# turn reliably busy. (A seq/echo loop gets optimized away by the model — it reasons
# out the answer instead of actually sleeping.)
LONG = "Run exactly this one bash command and wait for it to finish, then reply DONE: sleep 30"
BUSY_WAIT = 12  # seconds to let the long turn provision + get mid-sleep before we probe it

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def stream_turn(token: str, uid: str, message: str, out: list, timeout: float = 200,
                started: threading.Event | None = None):
    """Open the SSE turn and collect events until agent_end / turn_error.
    Sets `started` the moment turn_start arrives — i.e. once the busy slot is claimed
    and the turn is really streaming (removes provisioning-timing races in the test)."""
    try:
        with httpx.stream("POST", f"{BASE}/users/{uid}/messages",
                          headers=hdr(token), json={"message": message}, timeout=timeout) as r:
            if r.status_code != 200:
                out.append({"type": "_http", "status": r.status_code})
                return
            for line in r.iter_lines():
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    out.append(ev)
                    if ev.get("type") == "turn_start" and started is not None:
                        started.set()
                    if ev.get("type") in ("agent_end", "turn_error"):
                        return
    except Exception as e:
        out.append({"type": "_exc", "detail": repr(e)})


def main() -> int:
    env = dict(os.environ)
    env["USER_TOKENS"] = USER_TOKENS_ENV

    print("== launching uvicorn (3 users) ==")
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
            return finish(proc)

        # ---- A. concurrent isolation, no cross-talk ----
        print("== A. 3 concurrent turns, unique words ==")
        words = {"mu_a": "ALPHA", "mu_b": "BRAVO", "mu_c": "CHARLIE"}
        outs = {u: [] for u in words}
        threads = []
        t0 = time.time()
        for tok, uid in TOKENS.items():
            w = words[uid]
            msg = f"Reply with exactly the word {w} and nothing else."
            th = threading.Thread(target=stream_turn, args=(tok, uid, msg, outs[uid]), daemon=True)
            threads.append(th)
            th.start()
        for th in threads:
            th.join(timeout=200)
        wall = time.time() - t0
        print(f"   (wall-clock for 3 concurrent turns: {wall:.0f}s)")

        for uid, w in words.items():
            blob = json.dumps(outs[uid])
            others = [x for u, x in words.items() if u != uid]
            check(f"{uid} got {w}", w in blob)
            check(f"{uid} no cross-talk", not any(o in blob for o in others),
                  "" if not any(o in blob for o in others) else f"(leaked {[o for o in others if o in blob]})")

        # ---- B. same-user 409 while one streams ----
        print("== B. same-user second /messages -> 409 ==")
        time.sleep(2)  # let A's mu_a slot fully release before reusing mu_a
        busy_out: list = []
        started = threading.Event()
        bt = threading.Thread(target=stream_turn, args=("tok_a", "mu_a", LONG, busy_out),
                              kwargs={"started": started}, daemon=True)
        bt.start()
        # wait until mu_a's turn is genuinely streaming (busy slot claimed) — this makes
        # "which concurrent request wins" deterministic: the one already mid-turn holds it
        turn_live = started.wait(timeout=90)
        check("long turn started", turn_live, "" if turn_live else f"(bt saw: {busy_out[:2]})")
        r = httpx.post(f"{BASE}/users/mu_a/messages", headers=hdr("tok_a"),
                       json={"message": "second turn should be refused"}, timeout=30)
        check("concurrent same-user -> 409", r.status_code == 409, f"(got {r.status_code})")

        # ---- C. steer routes by user ----
        print("== C. steer is per-user ==")
        # mu_b is idle -> steering it should be refused (nothing to steer)
        rb = httpx.post(f"{BASE}/users/mu_b/steer", headers=hdr("tok_b"),
                        json={"message": "noop"}, timeout=30)
        check("steer idle user -> 409", rb.status_code == 409, f"(got {rb.status_code})")
        # mu_a is mid-turn (from B) -> steering it lands
        ra = httpx.post(f"{BASE}/users/mu_a/steer", headers=hdr("tok_a"),
                        json={"message": "Stop. Reply with exactly STEERED and nothing else."}, timeout=30)
        check("steer active user -> 200", ra.status_code == 200, f"(got {ra.status_code})")
        bt.join(timeout=200)
        check("steer changed mu_a's course", "STEERED" in json.dumps(busy_out))

        # ---- D. auth ----
        print("== D. auth ==")
        r401 = httpx.post(f"{BASE}/users/mu_a/abort", headers=hdr("bogus"), timeout=15)
        check("unknown token -> 401", r401.status_code == 401, f"(got {r401.status_code})")
        r403 = httpx.post(f"{BASE}/users/mu_b/abort", headers=hdr("tok_a"), timeout=15)
        check("wrong user for token -> 403", r403.status_code == 403, f"(got {r403.status_code})")

    finally:
        print("== reset users + stop uvicorn ==")
        for tok, uid in TOKENS.items():
            try:
                httpx.post(f"{BASE}/users/{uid}/reset", headers=hdr(tok), timeout=60)
            except Exception:
                pass
        finish(proc)

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("MULTIUSER OK — Phase 3 COMPLETE; next: Phase 4 (outreach path)"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


def finish(proc: subprocess.Popen) -> int:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
