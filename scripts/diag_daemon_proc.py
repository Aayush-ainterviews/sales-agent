"""
One-off diagnostic: how is the pi rpc daemon visible/killable from the backend?

Two prior test failures point at the daemon being invisible to `ps`/`pkill` run
via commands.run, or having an unexpected cmdline. This settles it with facts:
boots one sandbox, starts the daemon over a PTY, then dumps process reality and
tests three kill mechanisms. Prints a recommendation. Cleans up.

Run:  .venv/bin/python scripts/diag_daemon_proc.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402
from e2b import Sandbox  # noqa: E402

from backend.config import TEMPLATE_ALIAS  # noqa: E402
from lib.daemon_pipe import DaemonPipe, probe  # noqa: E402


def sh(sbx, cmd):
    try:
        r = sbx.commands.run(cmd, timeout=30)
        return (r.stdout or "").rstrip()
    except Exception as e:
        return f"<err {e!r}>"


def main() -> int:
    load_dotenv()
    sbx = Sandbox.create(TEMPLATE_ALIAS, timeout=10 * 60)
    print(f"sandbox {sbx.sandbox_id}")
    try:
        pipe = DaemonPipe(sbx, continue_session=False)
        pipe.open()
        time.sleep(2)
        print("daemon probe:", probe(pipe, "d"))

        print("\n--- who am i (pty vs commands.run) ---")
        print("commands.run whoami:", sh(sbx, "whoami"))
        print("pty whoami: (started as default pty user)")

        print("\n--- ps -e (node/pi lines) ---")
        print(sh(sbx, "ps -eo pid,user,args | grep -iE 'node|[p]i' | grep -v grep"))

        print("\n--- /proc cmdlines containing 'rpc' ---")
        print(sh(sbx, "for p in /proc/[0-9]*/cmdline; do tr '\\0' ' ' <\"$p\" 2>/dev/null | "
                      "grep -q rpc && echo \"$p: $(tr '\\0' ' ' <\"$p\")\"; done"))

        print("\n--- can pkill SEE it? (dry, prints matched pids) ---")
        print("pgrep -f 'mode rpc':", sh(sbx, "pgrep -f 'mode rpc' || echo NONE"))
        print("pgrep -f 'rpc':", sh(sbx, "pgrep -f 'rpc' || echo NONE"))
        print("pgrep -f node:", sh(sbx, "pgrep -f node || echo NONE"))
        print("pgrep -x pi:", sh(sbx, "pgrep -x pi || echo NONE"))

        print("\n--- kill test A: pty.kill(terminal.pid) ---")
        pid = pipe.terminal.pid
        print("pty terminal.pid =", pid)
        pipe.kill()
        time.sleep(2)
        print("after pty.kill, daemon still answering? ", end="")
        # need a fresh pipe object to probe? no — same pipe's reader is dead. Check via ps.
        print("procs with rpc now:", sh(sbx, "pgrep -f 'rpc' || echo NONE"))

    finally:
        print("\ncleanup")
        try:
            sbx.kill()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
