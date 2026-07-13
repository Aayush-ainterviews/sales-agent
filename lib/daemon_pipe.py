"""
Proven PTY -> `pi --mode rpc` plumbing.

Validated end-to-end by the spike (scripts/spike_pty_rpc.py, run 2026-07-10 —
report in docs/spike-notes.md). This module is the seed of the backend's
DaemonClient (Phase 2 in docs/implementation-plan.md).

Known facts this code embodies:
- the first `stty -echo` line echoes once before echo turns off -> parser skips non-JSON
- daemon does NOT survive sandbox pause/resume -> callers always restart with `-c`
- never use pty.connect(): it connects to dead PTYs without error
- reader thread gets a CommandExitException when the daemon dies -> that IS the death signal
- the running daemon shows up as process name "pi" (pi rewrites its argv title), NOT as
  any "--mode rpc" cmdline -> to kill it use `pkill -x pi`, and to find its pid `pgrep -x pi`
  (diag 2026-07-10 scripts/diag_daemon_proc.py)
"""

import json
import os
import queue
import re
import threading
import time

from e2b import PtySize, Sandbox

PI_MODEL = "google/gemini-3.5-flash"  # matches template pi-config settings.json
CWD = "/home/user"                    # constant: pi stores sessions per-cwd; -c depends on it
TURN_TIMEOUT = 180

ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")

quirks: list[str] = []


def quirk(msg: str) -> None:
    if len(quirks) < 300:
        quirks.append(msg)


class DaemonPipe:
    """One PTY connection to one pi rpc daemon inside one sandbox."""

    def __init__(self, sandbox: Sandbox, continue_session: bool, envs: dict | None = None):
        self.sandbox = sandbox
        self.continue_session = continue_session
        self.envs = envs if envs is not None else {"GEMINI_API_KEY": os.environ["GEMINI_API_KEY"]}
        self.buf = b""
        self.buf_lock = threading.Lock()
        self.messages: "queue.Queue[dict]" = queue.Queue()
        self.terminal = None
        self.reader = None
        self.exited = threading.Event()

    def open(self) -> None:
        self.terminal = self.sandbox.pty.create(
            size=PtySize(rows=24, cols=200),
            cwd=CWD,
            envs=self.envs,
            timeout=0,
        )
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()
        # silence the shell, then replace it with pi so PTY stdin goes straight to pi
        self.raw_send("stty -echo; export PS1=''\n")
        time.sleep(0.5)
        flag = " -c" if self.continue_session else ""
        self.raw_send(f"exec pi --model {PI_MODEL} --mode rpc{flag}\n")

    def _pump(self) -> None:
        try:
            self.terminal.wait(on_pty=self._on_data)
        except Exception as e:
            quirk(f"reader thread exception: {e!r}")
        self.exited.set()

    def _on_data(self, data: bytes) -> None:
        with self.buf_lock:
            self.buf += data
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                self._handle_line(line)

    def _handle_line(self, raw: bytes) -> None:
        line = ANSI_RE.sub(b"", raw).strip()
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            quirk(f"non-JSON line: {line[:200]!r}")
            return
        if isinstance(msg, dict):
            self.messages.put(msg)

    def raw_send(self, text: str) -> None:
        self.sandbox.pty.send_stdin(self.terminal.pid, text.encode())

    def send(self, obj: dict) -> None:
        self.raw_send(json.dumps(obj) + "\n")

    def wait_for(self, pred, timeout: float):
        """Return the first parsed message matching pred, else None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.messages.get(timeout=0.5)
            except queue.Empty:
                if self.exited.is_set():
                    quirk("PTY exited while waiting for a message")
                    break
                continue
            if pred(msg):
                return msg
        return None

    def kill(self) -> None:
        try:
            self.sandbox.pty.kill(self.terminal.pid)
        except Exception as e:
            quirk(f"pty.kill failed: {e!r}")


def probe(pipe: DaemonPipe, req_id: str, timeout: float = 20) -> bool:
    """Is the daemon answering? A dead daemon means send_stdin hits a gone PID and
    raises — that's a False, not a crash. (Death-by-send is more immediate than
    waiting for the reader thread to notice the PTY closed.)"""
    if pipe.exited.is_set():
        return False
    try:
        pipe.send({"id": req_id, "type": "get_state"})
    except Exception:
        return False
    resp = pipe.wait_for(lambda m: m.get("type") == "response" and m.get("id") == req_id, timeout)
    return resp is not None and resp.get("success", False)


def run_turn(pipe: DaemonPipe, message: str, timeout: float = TURN_TIMEOUT):
    """Send a prompt, wait for agent_end. Returns (agent_end_msg_or_None, n_stream_updates)."""
    pipe.send({"type": "prompt", "message": message})
    updates = 0

    def is_end(m):
        nonlocal updates
        if m.get("type") == "message_update":
            updates += 1
        return m.get("type") == "agent_end"

    end = pipe.wait_for(is_end, timeout)
    return end, updates


def end_contains(end_msg, token: str) -> bool:
    return end_msg is not None and token in json.dumps(end_msg)
