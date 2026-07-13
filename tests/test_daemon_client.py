"""
Phase 2, step 3 test: DaemonClient against real E2B. ~2-3 min (real sandbox + turns).

Proves the supervisor contract:
1. ensure_running on a FRESH sandbox starts the daemon WITHOUT -c and probes OK
2. a prompt runs a real turn (secrets reached pi: it answered) and events stream
3. ensure_running again returns the SAME live pipe (no needless restart)
4. crash: kill the daemon behind our back -> ensure_running detects the silence,
   restarts WITH -c, and the new daemon remembers the earlier turn (continuity)
5. secrets really landed: the sandbox env under pi carries ORIGAMI/APOLLO/APIFY too

Cleans up the sandbox it makes.
Run:  .venv/bin/python tests/test_daemon_client.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402
from e2b import Sandbox  # noqa: E402

from backend import db  # noqa: E402
from backend.config import assert_secrets_present  # noqa: E402
from backend.daemon_client import DaemonClient  # noqa: E402
from backend.registry import Registry  # noqa: E402
from backend.sandbox_manager import SandboxManager  # noqa: E402
from lib.daemon_pipe import end_contains, probe  # noqa: E402

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def drain_turn(pipe, timeout=120):
    """Wait for agent_end, counting stream updates."""
    updates = 0

    def is_end(m):
        nonlocal updates
        if m.get("type") == "message_update":
            updates += 1
        return m.get("type") == "agent_end"

    end = pipe.wait_for(is_end, timeout)
    return end, updates


def main() -> int:
    load_dotenv()
    assert_secrets_present()

    mgr = SandboxManager(Registry(db.pool()))
    dc = DaemonClient()
    uid = "test_daemon"
    mgr.registry.delete(uid)  # idempotent start (shared Postgres)
    sbx = mgr.get_or_create(uid)
    sid = sbx.sandbox_id

    try:
        print("== 1. fresh sandbox: start without -c ==")
        pipe = dc.ensure_running(sbx, uid)
        check("daemon probes OK", probe(pipe, "t1"))

        print("== 2. real turn (proves GEMINI_API_KEY reached pi) ==")
        dc.prompt(sid, "Reply with exactly the word DAEMON_OK and nothing else.")
        end, updates = drain_turn(dc.pipe_for(sid))
        check("turn completed", end_contains(end, "DAEMON_OK"), f"(updates {updates})")

        print("== 3. ensure_running reuses live pipe ==")
        pipe2 = dc.ensure_running(sbx, uid)
        check("same pipe, no restart", pipe2 is pipe)

        print("== 4. crash -> restart with -c -> continuity ==")
        pid_before = sbx.commands.run("pgrep -x pi | head -1", timeout=30).stdout.strip()
        print(f"   (daemon pid before kill: {pid_before})")
        sbx.commands.run("pkill -x pi || true", timeout=30)
        time.sleep(2)
        gone = sbx.commands.run("pgrep -x pi || echo NONE", timeout=30).stdout.strip()
        print(f"   (pgrep -x pi after kill: {gone})")
        pipe3 = dc.ensure_running(sbx, uid)   # should detect silence, restart with -c
        check("restarted (new pipe)", pipe3 is not pipe)
        dc.prompt(sid, "Which uppercase word did I first ask you to say? Just the word.")
        end, _ = drain_turn(dc.pipe_for(sid))
        check("continuity after restart", end_contains(end, "DAEMON_OK"))

        print("== 5. all skill secrets present in pi's env ==")
        env = sbx.commands.run(
            "cat /proc/$(pgrep -x pi | head -1)/environ | tr '\\0' '\\n'",
            timeout=30,
        ).stdout
        for name in ("GEMINI_API_KEY", "ORIGAMI_API_KEY", "APOLLO_API_KEY", "APIFY_TOKEN"):
            check(f"env has {name}", f"{name}=" in env)
        check("env has NO zeptomail (Q9)", "ZEPTOMAIL" not in env)

    finally:
        print("== cleanup ==")
        try:
            Sandbox.connect(sid).kill()
        except Exception:
            pass
        mgr.registry.delete(uid)

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("DAEMON CLIENT OK — next: turn_runner + FastAPI"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
