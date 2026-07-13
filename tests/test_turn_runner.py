"""
Phase 2, step 4 test: TurnRunner against real E2B (~2-4 min).

Drives TurnRunner directly (no HTTP) to prove the turn lifecycle:
A. a normal turn streams turn_start .. agent_end, and a session backup lands
B. steer mid-turn: a message injected while a long turn runs changes its course
C. watchdog: with a tiny cap, an overrunning turn is aborted with turn_error

One sandbox/user reused across A-C (shared daemon); reset at the end.
Run:  .venv/bin/python tests/test_turn_runner.py
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend import config, db  # noqa: E402
from backend.config import assert_secrets_present  # noqa: E402
from backend.registry import Registry  # noqa: E402
from backend.turn_runner import TurnRunner  # noqa: E402

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def text_of(events: list[dict]) -> str:
    import json
    return json.dumps(events)


def main() -> int:
    load_dotenv()
    assert_secrets_present()

    runner = TurnRunner(Registry(db.pool()))
    uid = "test_turn"
    runner.registry.delete(uid)  # idempotent start (shared Postgres)

    try:
        print("== A. normal turn + backup ==")
        events = list(runner.run_turn(uid, "Reply with exactly the word TURN_OK and nothing else."))
        types = [e.get("type") for e in events]
        check("turn_start first", types[:1] == ["turn_start"])
        check("agent_end last", types[-1:] == ["agent_end"])
        check("answer present", "TURN_OK" in text_of(events))
        saved = runner.registry.get_log(uid)  # log now persisted in Postgres, not a file
        check("session log saved to Postgres", bool(saved) and "TURN_OK" in saved,
              f"({len(saved or '')} bytes)")

        print("== B. steer mid-turn ==")
        collected: list[dict] = []

        def drive():
            for ev in runner.run_turn(
                uid,
                "Run bash: for i in $(seq 1 30); do echo $i; sleep 1; done. Then report the last number.",
            ):
                collected.append(ev)

        t = threading.Thread(target=drive, daemon=True)
        t.start()
        time.sleep(6)  # let the long turn get going
        steered = runner.steer(uid, "Stop the task now. Reply with exactly STEERED and nothing else.")
        check("steer accepted", steered)
        t.join(timeout=config.TURN_WATCHDOG + 30)
        check("steer changed course", "STEERED" in text_of(collected))

        print("== C. watchdog aborts an overrun ==")
        saved = config.TURN_WATCHDOG
        config.TURN_WATCHDOG = 5  # force a fast watchdog
        try:
            evs = list(runner.run_turn(
                uid,
                "Run bash: for i in $(seq 1 60); do echo $i; sleep 1; done. Then report the last number.",
            ))
        finally:
            config.TURN_WATCHDOG = saved
        watchdog_hit = any(e.get("reason") == "watchdog_timeout" for e in evs)
        check("watchdog fired", watchdog_hit)

    finally:
        print("== cleanup ==")
        try:
            runner.reset(uid)
        except Exception:
            pass

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("TURN RUNNER OK — Phase 2 core done; next: FastAPI HTTP smoke test"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
