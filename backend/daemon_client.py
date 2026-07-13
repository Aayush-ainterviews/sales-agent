"""
DaemonClient (Q5, Q8, Q11): the supervisor around lib/daemon_pipe.py.

DaemonPipe knows how to talk to one daemon over one PTY. DaemonClient adds the
things a long-lived multi-turn service needs:
- secrets injected at daemon start, per user (Q11) — never at create, never baked
- the backend owns the daemon lifecycle (Q8): it starts, probes, and restarts it
- restart-with-`-c` as the single recovery move (spike: the daemon does not survive
  pause/resume, so every resume lands here — this is normal, not an error)
- continue-or-fresh decided by fact, not guess: `-c` only if a session file already
  exists in the sandbox (fresh sandbox has none; spike showed fresh must start without -c)

One DaemonClient instance holds many daemons, keyed by sandbox_id — that keying is
what gives Phase 3 its per-user isolation for free.
"""

import logging
import threading

from e2b import Sandbox

from backend import config
from lib.daemon_pipe import DaemonPipe, probe

log = logging.getLogger("daemon_client")


class DaemonClient:
    def __init__(self):
        self._pipes: dict[str, DaemonPipe] = {}   # sandbox_id -> live pipe
        # different users touch different keys, but guard the dict itself so
        # concurrent inserts/pops across FastAPI threads stay consistent (Phase 3)
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def ensure_running(self, sandbox: Sandbox, user_id: str) -> DaemonPipe:
        """Return a live, probe-answering daemon for this sandbox. Restart if needed.
        The lock guards only the dict; the slow probe/start run outside it so users
        don't serialize on each other (same-user is already serialized by TurnRunner)."""
        sid = sandbox.sandbox_id
        with self._lock:
            pipe = self._pipes.get(sid)

        if pipe is not None and probe(pipe, "health", timeout=config.PROBE_TIMEOUT):
            return pipe

        # no pipe, or it went silent -> (re)start. Kill any stale daemon first so we
        # don't end up with two pi processes fighting over the same session file.
        self._kill_daemon(sandbox)
        pipe = self._start(sandbox, user_id)
        with self._lock:
            self._pipes[sid] = pipe
        return pipe

    def _start(self, sandbox: Sandbox, user_id: str) -> DaemonPipe:
        cont = self._has_session(sandbox)
        pipe = DaemonPipe(
            sandbox,
            continue_session=cont,
            envs=config.secrets_for_user(user_id),
        )
        pipe.open()
        if not probe(pipe, "start", timeout=config.PROBE_TIMEOUT):
            raise RuntimeError(f"daemon for {sandbox.sandbox_id} did not answer probe after start")
        log.info("daemon up for %s (sandbox %s, continue=%s)", user_id, sandbox.sandbox_id, cont)
        return pipe

    def _has_session(self, sandbox: Sandbox) -> bool:
        """True if the sandbox already has a pi session on disk -> safe to `-c`."""
        out = sandbox.commands.run(
            "find /home/user/.pi/agent/sessions -name '*.jsonl' 2>/dev/null | head -1",
            timeout=30,
        ).stdout.strip()
        return bool(out)

    def _kill_daemon(self, sandbox: Sandbox) -> None:
        # pi rewrites its process title to just "pi" (diag 2026-07-10: `ps` shows the
        # daemon as name "pi", not any --mode rpc cmdline), so match the exact process
        # name. `-x pi` can't self-match: the shell/pkill are named "bash"/"pkill".
        try:
            sandbox.commands.run("pkill -x pi || true", timeout=30)
        except Exception as e:
            log.debug("kill_daemon pkill note: %r", e)

    def forget(self, sandbox_id: str) -> None:
        """Drop our handle to a daemon (e.g. after the sandbox is reset/paused)."""
        with self._lock:
            pipe = self._pipes.pop(sandbox_id, None)
        if pipe is not None:
            pipe.kill()

    # -- commands (delegate to the pipe) -----------------------------------

    def prompt(self, sandbox_id: str, message: str) -> None:
        self.pipe_for(sandbox_id).send({"type": "prompt", "message": message})

    def steer(self, sandbox_id: str, message: str) -> None:
        self.pipe_for(sandbox_id).send({"type": "steer", "message": message})

    def abort(self, sandbox_id: str) -> None:
        self.pipe_for(sandbox_id).send({"type": "abort"})

    def pipe_for(self, sandbox_id: str) -> DaemonPipe:
        with self._lock:
            return self._pipes[sandbox_id]
