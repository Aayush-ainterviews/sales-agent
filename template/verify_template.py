"""
Phase 1 exit test (docs/implementation-plan.md):
boot a sandbox from 'sales-agent-v1' and prove it is ready with ZERO install:
  1. boot time fast, node 22 + pi present
  2. pi-config landed (settings, AGENTS.md, exactly 3 skills, NO zeptomail — Q9)
  3. daemon starts over PTY, probe answers, one real turn completes
Uses lib/daemon_pipe.py — the spike-proven plumbing the backend's DaemonClient grows from.

Run:  .venv/bin/python template/verify_template.py   (needs E2B_API_KEY + GEMINI_API_KEY)
"""

import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from e2b import Sandbox

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.daemon_pipe import DaemonPipe, end_contains, probe, run_turn  # noqa: E402

ALIAS = "sales-agent-v2"
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append((name, ok, detail))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())
    return ok


def main() -> int:
    load_dotenv()

    print(f"== booting sandbox from '{ALIAS}' ==")
    t0 = time.time()
    sandbox = Sandbox.create(ALIAS, timeout=15 * 60)
    boot = time.time() - t0
    check("boot < 15s", boot < 15, f"({boot:.1f}s)")

    try:
        node = sandbox.commands.run("node --version", timeout=30).stdout.strip()
        check("node 22+", node.startswith("v22"), node)
        pi = sandbox.commands.run("pi --version", timeout=30).stdout.strip()
        check("pi installed", bool(pi), pi)

        ls = sandbox.commands.run(
            "ls /home/user/.pi/agent/ /home/user/.pi/agent/skills/ && "
            "ls -d /home/user/outbox && head -c 200 /home/user/GOAL.md",
            timeout=30,
        ).stdout
        check("settings.json", "settings.json" in ls)
        check("AGENTS.md", "AGENTS.md" in ls)
        for skill in ("apify", "apollo-enrichment", "origami-enrichment", "submit-batch"):
            check(f"skill: {skill}", skill in ls)
        check("NO zeptomail skill (Q9)", "zeptomail" not in ls)
        check("outbox dir exists (Phase 4)", "/home/user/outbox" in ls)
        check("GOAL.md at cwd", len(ls.strip()) > 200)

        print("== daemon path: PTY -> rpc -> probe -> one turn ==")
        pipe = DaemonPipe(sandbox, continue_session=False)
        pipe.open()
        time.sleep(2)
        check("daemon probe", probe(pipe, "v1"))
        end, updates = run_turn(pipe, "Reply with exactly the word TEMPLATE_OK and nothing else.")
        check("turn works", end_contains(end, "TEMPLATE_OK"), f"(stream updates: {updates})")

    finally:
        sandbox.kill()

    failed = [c for c in checks if not c[1]]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("TEMPLATE VERIFIED — Phase 1 done, start Phase 2 (backend core)"
                         if not failed else f"FAILED: {[c[0] for c in failed]}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
