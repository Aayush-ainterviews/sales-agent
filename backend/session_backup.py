"""
SessionBackup (Q6): read the pi session JSONL out of the sandbox after each turn,
and write it back into a fresh sandbox on disaster restore.

Split of duties: this module moves the JSONL in/out of a sandbox. Persisting it is
Registry.save_log (into `sessions.log` — that column IS the backup; no R2/S3). The
sandbox's file is the source of truth while it lives.
"""

import logging

from e2b import Sandbox

log = logging.getLogger("session_backup")

# pi stores sessions per-cwd; cwd /home/user -> dir name "--home-user--" (constant, Q5)
SESSION_DIR = "/home/user/.pi/agent/sessions/--home-user--"


def read_latest_session(sandbox: Sandbox) -> str | None:
    """Return the sandbox's most recent pi session JSONL as text, or None.
    Uses `cat` over commands.run (rock-solid in every test) rather than files.read,
    which was returning transient EAGAIN. One round-trip: find newest, then cat it.
    Best-effort: never let a read failure break a turn."""
    try:
        out = sandbox.commands.run(
            'f=$(ls -t /home/user/.pi/agent/sessions/**/*.jsonl '
            '/home/user/.pi/agent/sessions/*.jsonl 2>/dev/null | head -1); '
            '[ -n "$f" ] && cat "$f"',
            timeout=30,
        ).stdout
        if not out:
            log.warning("no session file found to back up")
            return None
        return out
    except Exception as e:
        log.warning("reading session file failed: %r", e)
        return None


def restore_session(sandbox: Sandbox, log_text: str) -> bool:
    """Write a saved session JSONL into a fresh sandbox so `pi -c` continues it.
    Must run BEFORE any daemon starts (so this is the only, hence most-recent, file).
    The session dir may not exist yet on a fresh sandbox, so mkdir -p first."""
    try:
        sandbox.commands.run(f"mkdir -p {SESSION_DIR}", timeout=30)
        # filename is irrelevant to `-c` (it picks the newest .jsonl); this is the only one
        sandbox.files.write(f"{SESSION_DIR}/restored.jsonl", log_text)
        return True
    except Exception as e:
        log.warning("restore write failed: %r", e)
        return False
