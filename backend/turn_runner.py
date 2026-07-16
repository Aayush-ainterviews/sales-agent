"""
TurnRunner (Q16): the full lifecycle of one turn, as a stream of events — keyed by
CONVERSATION (each conversation has its own sandbox), so different conversations (even the
same user's) run truly in parallel.

run_claimed(cid, message) is a generator that:
  1. gets/creates the conversation's sandbox and ensures the daemon is live
  2. sends the prompt, then relays every daemon event to the caller (SSE)
  3. keeps the idle-pause countdown pushed back while the turn streams (bump)
  4. aborts a turn that overruns the watchdog
  5. backs up the session (to the conversation row) once the turn ends

It owns the shared singletons (registry, sandbox_manager, daemon_client). The daemon's
own secrets stay per-USER (config.secrets_for_user), so we look up the conversation's
user_id for daemon start + batch attribution.
"""

import logging
import queue
import threading
import time
from collections import defaultdict
from typing import Iterator

from backend import batch_collector, config, session_backup
from backend.batches import PendingBatches
from backend.daemon_client import DaemonClient
from backend.logging_setup import event
from backend.registry import Registry
from backend.sandbox_manager import SandboxManager

log = logging.getLogger("turn_runner")


class TurnBusy(Exception):
    """Raised when a conversation already has a turn streaming (Q17 -> 409)."""


class TurnRunner:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.batches = PendingBatches(registry.pool)   # Phase 4 approval queue
        self.sandboxes = SandboxManager(registry)
        self.daemons = DaemonClient()
        # cid -> sandbox_id of the sandbox currently serving it (for steer/abort)
        self._active: dict[str, str] = {}
        # concurrency (Q17): one turn per conversation; different conversations run in parallel
        self._guard = threading.Lock()             # protects _active and _busy
        # cid -> monotonic claim time. A stuck claim (client disconnected mid-turn so the
        # streaming generator was suspended and its finally never ran) would otherwise wedge
        # the conversation forever; a claim older than STALE_CLAIM is treated as dead + cleared.
        self._busy: dict[str, float] = {}
        self._clocks: dict[str, threading.Lock] = defaultdict(threading.Lock)  # serialize a conversation's provisioning

    def try_claim(self, cid: str) -> bool:
        """Claim the conversation's single turn slot synchronously — fast, no I/O. Returns
        False if a turn is genuinely active (Q17 -> 409). A stale claim (older than a full
        turn's max life) is auto-cleared and re-granted, so a mid-turn disconnect can't wedge
        the conversation permanently."""
        now = time.monotonic()
        with self._guard:
            claimed_at = self._busy.get(cid)
            if claimed_at is not None and (now - claimed_at) < config.STALE_CLAIM:
                return False
            if claimed_at is not None:
                log.warning("clearing stale turn claim for %s (age %.0fs)", cid, now - claimed_at)
                self._active.pop(cid, None)
            self._busy[cid] = now
        return True

    def run_claimed(self, cid: str, message: str) -> Iterator[dict]:
        """Run a turn whose slot is already claimed (see try_claim). Releases it at the end."""
        try:
            yield from self._run_turn_locked(cid, message)
        finally:
            self._release(cid)   # idempotent; normal path already released at agent_end

    def _release(self, cid: str) -> None:
        with self._guard:
            self._busy.pop(cid, None)
            self._active.pop(cid, None)

    def run_turn(self, cid: str, message: str) -> Iterator[dict]:
        """Convenience for non-HTTP callers (direct drive): claim-or-raise, then run."""
        if not self.try_claim(cid):
            raise TurnBusy(cid)
        yield from self.run_claimed(cid, message)

    def _run_turn_locked(self, cid: str, message: str) -> Iterator[dict]:
        row = self.registry.get(cid)
        user_id = row.user_id if row else None      # daemon secrets + batch attribution stay per-user

        with self._clocks[cid]:                     # serialize provisioning for this conversation
            sbx = self.sandboxes.get_or_create(cid)
            pipe = self.daemons.ensure_running(sbx, user_id or cid)
            sid = sbx.sandbox_id
            with self._guard:
                self._active[cid] = sid
        self.registry.touch(cid)
        if row is not None and not row.title:       # name the chat from its first message
            self.registry.set_title(cid, message.strip()[:60] or "New chat")

        self.daemons.prompt(sid, message)
        yield {"type": "turn_start", "sandbox_id": sid}

        t_start = time.monotonic()
        deadline = t_start + config.TURN_WATCHDOG
        last_bump = t_start
        last_hb = t_start
        updates = 0
        usage = None
        stop_reason = None
        outcome = "ok"

        while True:
            now = time.monotonic()

            if now > deadline:
                self.daemons.abort(sid)
                outcome = "watchdog_timeout"
                event(log, "watchdog_abort", conversation_id=cid, sandbox_id=sid,
                      elapsed_s=round(now - t_start))
                yield {"type": "turn_error", "reason": "watchdog_timeout",
                       "detail": f"aborted after {config.TURN_WATCHDOG}s; work so far is saved"}
                break

            if now - last_bump > config.BUMP_INTERVAL:
                try:
                    sbx.set_timeout(config.IDLE_PAUSE)  # push the idle-pause countdown back
                except Exception as e:
                    log.warning("set_timeout bump failed for %s: %r", cid, e)
                last_bump = now

            try:
                ev = pipe.messages.get(timeout=1.0)
            except queue.Empty:
                if pipe.exited.is_set():
                    outcome = "daemon_died"
                    yield {"type": "turn_error", "reason": "daemon_died",
                           "detail": "the agent process stopped; retry to continue"}
                    break
                # keep the SSE stream alive so proxies (Railway) don't cut an idle turn
                if now - last_hb > config.HEARTBEAT_INTERVAL:
                    last_hb = now
                    yield {"type": "heartbeat"}
                continue

            # capture per-turn metrics for the structured turn_complete log
            etype = ev.get("type")
            if etype == "message_update":
                updates += 1
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else None
            if msg:
                if msg.get("usage"):
                    usage = msg["usage"]
                if msg.get("stopReason"):
                    stop_reason = msg["stopReason"]

            yield ev
            if etype == "agent_end":
                break

        event(log, "turn_complete", conversation_id=cid, sandbox_id=sid,
              duration_s=round(time.monotonic() - t_start, 1), updates=updates,
              outcome=outcome, stop_reason=stop_reason, usage=usage)

        # free the conversation's slot the moment the turn ends, BEFORE the (slower) backup —
        # the next turn can start immediately; backup doesn't need the slot held.
        self._release(cid)
        # persist the full session JSONL into the conversation row (this IS the backup, Q6)
        log_text = session_backup.read_latest_session(sbx)
        if log_text:
            try:
                self.registry.save_log(cid, log_text)
            except Exception as e:
                log.warning("save_log failed for %s: %r", cid, e)
        # collect any Draft Batches the agent queued in the outbox (Phase 4)
        if user_id:
            batch_collector.collect(sbx, user_id, cid, self.batches)

    # -- mid-turn controls (Q7) -------------------------------------------

    def steer(self, cid: str, message: str) -> bool:
        with self._guard:
            sid = self._active.get(cid)
        if not sid:
            return False   # nothing streaming -> 409
        self.daemons.steer(sid, message)
        return True

    def abort(self, cid: str) -> bool:
        with self._guard:
            sid = self._active.get(cid)
        if not sid:
            return False
        self.daemons.abort(sid)
        return True

    def send_feedback(self, cid: str, text: str) -> None:
        """Run a turn that hands send-results back to the agent so it can plan follow-ups
        (Phase 4). Best-effort, meant to be fired in a background thread from approve."""
        try:
            for _ in self.run_turn(cid, text):
                pass
            log.info("feedback turn done for %s", cid)
        except TurnBusy:
            log.warning("feedback skipped for %s: conversation is mid-turn", cid)
        except Exception as e:
            log.warning("feedback turn failed for %s: %r", cid, e)

    def busy_snapshot(self) -> dict[str, dict]:
        """Per-conversation live turn state (admin monitor): who holds a slot, for how long."""
        now = time.monotonic()
        with self._guard:
            return {
                cid: {"busy_age_s": round(now - claimed_at),
                      "active_sandbox": self._active.get(cid)}
                for cid, claimed_at in self._busy.items()
            }

    def reset(self, cid: str) -> None:
        """Clear the turn slot + kill/reprovision the sandbox (keeps the chat + history)."""
        with self._guard:
            sid = self._active.pop(cid, None)
            self._busy.pop(cid, None)   # also clears a stuck turn slot (manual unstick)
        if sid:
            self.daemons.forget(sid)
        self.sandboxes.reset(cid)

    def delete_conversation(self, cid: str) -> None:
        """Kill the sandbox and forget the conversation entirely (history included)."""
        with self._guard:
            sid = self._active.pop(cid, None)
            self._busy.pop(cid, None)
        if sid:
            self.daemons.forget(sid)
        self.sandboxes.reset(cid)
        self.registry.delete(cid)
