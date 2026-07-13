"""
Checkpoint D: disaster restore. ~3-4 min (real sandboxes + turns).

Proves history survives a DEAD sandbox (not just a paused one):
1. turn 1 tells the agent a secret word -> log saved to Postgres
2. KILL the sandbox behind the manager's back (disaster: its disk + session file gone)
3. turn 2 asks for the word -> get_or_create: connect fails -> recreate a fresh
   sandbox -> restore the log from Postgres into it -> daemon starts with -c ->
   the agent remembers the word
4. assert the word came back

Contrast with normal resume (paused sandbox): there history comes from the sandbox's
own disk and the Postgres log is never touched. Restore is only for a gone sandbox.

Run:  .venv/bin/python tests/test_restore.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402
from e2b import Sandbox  # noqa: E402

from backend import db  # noqa: E402
from backend.config import assert_secrets_present  # noqa: E402
from backend.registry import Registry  # noqa: E402
from backend.turn_runner import TurnRunner  # noqa: E402

checks: list[tuple[str, bool]] = []
SECRET = "RESTORE_ME"


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def drive(runner, uid, message):
    return list(runner.run_turn(uid, message))


def main() -> int:
    load_dotenv()
    assert_secrets_present()

    runner = TurnRunner(Registry(db.pool()))
    uid = "test_restore"
    runner.reset(uid)  # clean start

    try:
        print("== 1. turn that plants a secret word ==")
        drive(runner, uid, f"Remember this exact word for later: {SECRET}. Reply with just OK.")
        saved = runner.registry.get_log(uid)
        check("log saved to Postgres", bool(saved) and SECRET in saved, f"({len(saved or '')} bytes)")

        old_sid = runner.registry.get(uid).sandbox_id
        print(f"== 2. DISASTER: kill sandbox {old_sid} ==")
        Sandbox.connect(old_sid).kill()
        runner.daemons.forget(old_sid)  # drop the now-dead pipe handle

        print("== 3. next turn: recreate + restore + -c ==")
        events = drive(
            runner, uid,
            "What exact word did I ask you to remember earlier? Reply with just that word.",
        )
        new_sid = runner.registry.get(uid).sandbox_id
        check("sandbox was recreated (new id)", new_sid != old_sid, f"({old_sid[:8]} -> {new_sid[:8]})")
        check("history restored — agent remembers", SECRET in json.dumps(events))

    finally:
        print("== cleanup ==")
        try:
            runner.reset(uid)
        except Exception:
            pass

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("RESTORE OK — Postgres migration fully closed"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
