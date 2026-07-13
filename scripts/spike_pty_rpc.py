"""
Spike (COMPLETED 2026-07-10 — report in docs/spike-notes.md): pi --mode rpc over an E2B PTY.

Validated architecture decision Q5b. Kept for re-running only if pi/E2B versions
change materially. The reusable plumbing it proved (DaemonPipe etc.) now lives in
lib/daemon_pipe.py — that module is what the rest of the project builds on.

Run:  .venv/bin/python scripts/spike_pty_rpc.py     (needs E2B_API_KEY, GEMINI_API_KEY in .env)
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from e2b import Sandbox

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.daemon_pipe import (  # noqa: E402
    DaemonPipe,
    end_contains,
    probe,
    quirk,
    quirks,
    run_turn,
    TURN_TIMEOUT,
)

SANDBOX_TIMEOUT = 30 * 60
REPORT_PATH = Path(__file__).parent.parent / "docs" / "spike-notes.md"

LONG_TASK = (
    "Run this exact bash command and wait for it to finish: "
    "for i in $(seq 1 30); do echo $i; sleep 1; done . "
    "Then tell me the last number printed."
)

results: list[tuple[str, str]] = []


def record(step: str, ok: bool, detail: str = "") -> bool:
    verdict = "PASS" if ok else "FAIL"
    results.append((step, f"{verdict} {detail}".strip()))
    print(f"  -> {verdict} {detail}")
    return ok


def main() -> int:
    load_dotenv()
    for var in ("E2B_API_KEY", "GEMINI_API_KEY"):
        if not os.environ.get(var):
            print(f"missing {var} in environment/.env")
            return 1

    print("== setup: creating sandbox ==")
    sandbox = Sandbox.create(timeout=SANDBOX_TIMEOUT)
    sandbox_id = sandbox.sandbox_id
    print(f"sandbox: {sandbox_id}")

    try:
        node = sandbox.commands.run("node --version", timeout=30)
        print(f"node: {node.stdout.strip()}")
        major = int(node.stdout.strip().lstrip("v").split(".")[0])
        if major < 22:
            # QUIRK #1 (2026-07-10 run): base template ships node v20.9 — too old for pi
            # (undici needs markAsUncloneable). The sales-agent template bakes node 22.
            quirk(f"base template node {node.stdout.strip()} too old for pi — upgraded to 22 via n")
            print("upgrading node to 22 (base template too old for pi)...")
            sandbox.commands.run("sudo npm install -g n && sudo n 22", timeout=300)
            node = sandbox.commands.run("node --version", timeout=30)
            print(f"node now: {node.stdout.strip()}")
    except Exception as e:
        record("setup/node", False, f"node setup failed: {e!r}")
        return finish(1, sandbox)

    print("installing pi (one-time, ~1 min)...")
    try:
        sandbox.commands.run(
            "sudo npm install -g --ignore-scripts @earendil-works/pi-coding-agent", timeout=300
        )
        ver = sandbox.commands.run("pi --version", timeout=30)
        record("setup/pi-install", True, ver.stdout.strip())
    except Exception as e:
        record("setup/pi-install", False, f"{e!r}")
        return finish(1, sandbox)

    pipe = DaemonPipe(sandbox, continue_session=False)
    pipe.open()
    time.sleep(2)

    try:
        # STEP 1 — probe
        print("== step 1: probe (get_state over PTY) ==")
        if not record("1-probe", probe(pipe, "1")):
            print("hard fail: the PTY<->JSONL joint itself does not work. Stopping.")
            return finish(1, sandbox)

        # STEP 2 — turn + streaming
        print("== step 2: full turn with streaming ==")
        end, updates = run_turn(pipe, "Reply with exactly the word SPIKE_OK and nothing else.")
        ok = end_contains(end, "SPIKE_OK")
        if not record("2-turn", ok, f"stream updates seen: {updates}"):
            print("hard fail: cannot complete a basic turn. Stopping.")
            return finish(1, sandbox)

        # STEP 3 — steer mid-turn
        print("== step 3: steer mid-turn ==")
        pipe.send({"type": "prompt", "message": LONG_TASK})
        started = pipe.wait_for(lambda m: m.get("type") == "tool_execution_start", 60)
        if started:
            time.sleep(3)
            pipe.send({"type": "steer", "message": "Stop the task. Reply with exactly STEERED and nothing else."})
            end = pipe.wait_for(lambda m: m.get("type") == "agent_end", TURN_TIMEOUT)
            record("3-steer", end_contains(end, "STEERED"), "(soft: continue even if FAIL)")
        else:
            record("3-steer", False, "never saw tool_execution_start")

        # STEP 4 — abort mid-turn
        print("== step 4: abort mid-turn ==")
        pipe.send({"type": "prompt", "message": LONG_TASK})
        pipe.wait_for(lambda m: m.get("type") == "tool_execution_start", 60)
        time.sleep(3)
        pipe.send({"type": "abort"})
        end = pipe.wait_for(lambda m: m.get("type") == "agent_end", 60)
        alive = probe(pipe, "4")
        record("4-abort", end is not None and alive, f"daemon alive after abort: {alive}")

        # STEP 5 — kill daemon, restart with -c, continuity
        print("== step 5: restart with -c (crash recovery rehearsal) ==")
        # bracket trick: '[p]i' regex-matches the daemon's cmdline but not this bash's own,
        # otherwise pkill kills its own shell (E2B runs commands via bash -c) — exit -1
        try:
            sandbox.commands.run("pkill -x pi || true", timeout=30)
        except Exception as e:
            quirk(f"pkill quirk: {e!r}")
        pipe.kill()
        time.sleep(2)
        pipe = DaemonPipe(sandbox, continue_session=True)
        pipe.open()
        time.sleep(2)
        if not record("5a-restart-probe", probe(pipe, "5")):
            return finish(1, sandbox)
        end, _ = run_turn(pipe, "Earlier in this conversation I asked you to reply with one specific uppercase word. Which word was it? Answer with just that word.")
        record("5b-continuity", end_contains(end, "SPIKE_OK"))

        # STEP 6 — pause / resume
        print("== step 6: pause -> resume (the big unknown) ==")
        old_pid = pipe.terminal.pid
        print("pausing sandbox...")
        sandbox.pause()
        time.sleep(2)
        print("resuming via Sandbox.connect...")
        sandbox = Sandbox.connect(sandbox_id)

        ps = sandbox.commands.run("pgrep -x pi || true", timeout=30)
        daemon_survived = bool(ps.stdout.strip())
        results.append(("6-info", f"INFO pi process survived pause/resume: {daemon_survived}"))
        print(f"  -> INFO daemon survived resume: {daemon_survived}")
        try:
            sandbox.pty.connect(old_pid)
            results.append(("6-info", "INFO old PTY reconnectable: True"))
        except Exception as e:
            results.append(("6-info", f"INFO old PTY reconnectable: False ({type(e).__name__})"))

        # regardless of survival: the backend's real path is restart-with--c
        try:
            sandbox.commands.run("pkill -x pi || true", timeout=30)
        except Exception as e:
            quirk(f"pkill quirk: {e!r}")
        time.sleep(1)
        pipe = DaemonPipe(sandbox, continue_session=True)
        pipe.open()
        time.sleep(2)
        ok = probe(pipe, "6")
        if ok:
            end, _ = run_turn(pipe, "One more time: which uppercase word did I first ask you to say? Just the word.")
            ok = end_contains(end, "SPIKE_OK")
        record("6-pause-resume", ok, "continuity after pause/resume via restart-with--c")

        return finish(0, sandbox)

    except KeyboardInterrupt:
        return finish(1, sandbox)


def finish(code: int, sandbox: Sandbox | None = None) -> int:
    if sandbox is not None:
        print("== step 7: cleanup ==")
        try:
            sandbox.kill()
        except Exception as e:
            quirk(f"sandbox.kill failed: {e!r}")

    lines = ["# Spike notes — pi RPC over E2B PTY", "", f"Run: {time.strftime('%Y-%m-%d %H:%M')}", "", "## Results", ""]
    lines += [f"- **{step}**: {detail}" for step, detail in results]
    lines += ["", "## Quirk log (non-JSON PTY lines & anomalies — future DaemonClient test cases)", ""]
    lines += [f"- `{q}`" for q in quirks] or ["- (clean run — no quirks)"]
    try:
        REPORT_PATH.write_text("\n".join(lines) + "\n")
    except OSError as e:
        print(f"could not write report: {e!r}")

    print("\n===== REPORT =====")
    for step, detail in results:
        print(f"{step:20} {detail}")
    print(f"\nquirks: {len(quirks)} (full log in {REPORT_PATH})")
    print("verdict: " + ("SPIKE PASSED — Q5b stands, build DaemonClient on these learnings"
                         if code == 0 and all("FAIL" not in d for s, d in results if s != "3-steer")
                         else "CHECK REPORT — if steps 1-2 failed, flip Q5 to json one-shot"))
    return code


if __name__ == "__main__":
    sys.exit(main())
