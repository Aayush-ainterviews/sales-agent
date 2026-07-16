"""
SandboxManager (Q4): one self-healing path that provisions, resumes, and recovers —
now keyed by conversation (each conversation gets its own sandbox).

get_or_create(cid) is the whole story:
  conversation has a sandbox_id -> try Sandbox.connect (paused sandbox resumes in ~1s)
  connect fails -> recreate AND restore the session from the Postgres log (disaster, Q6)
  no sandbox_id yet -> create fresh; restore only if the conversation already has a log
                       (e.g. after a reset cleared the sandbox but kept history)

Secrets are NOT passed here — nothing at create time carries them (Q11). They enter only
when the daemon starts (DaemonClient).
"""

import logging

from e2b import Sandbox, SandboxLifecycle

from backend import config, session_backup
from backend.registry import Registry

log = logging.getLogger("sandbox_manager")


class SandboxManager:
    def __init__(self, registry: Registry):
        self.registry = registry

    def get_or_create(self, cid: str) -> Sandbox:
        row = self.registry.get(cid)
        if row is None:
            raise RuntimeError(f"unknown conversation {cid}")

        if row.sandbox_id:
            try:
                sbx = Sandbox.connect(row.sandbox_id)
                self.registry.touch(cid)
                log.info("resumed sandbox %s for conversation %s", row.sandbox_id, cid)
                return sbx
            except Exception as e:
                # row was stale: sandbox killed / reaped. Its disk (and session file) is gone
                # -> recreate AND restore history from the Postgres log so the chat survives.
                log.warning("connect to %s failed (%r); recreating + restoring for %s",
                            row.sandbox_id, e, cid)
                return self._create_and_record(cid, restore=True)

        # no sandbox yet: fresh conversation, or one whose sandbox was reset. Restore only if
        # there's history to restore (a reset keeps the log; a brand-new chat has none).
        restore = bool(self.registry.get_log(cid))
        return self._create_and_record(cid, restore=restore)

    def _create_and_record(self, cid: str, restore: bool) -> Sandbox:
        sbx = self._create(cid)
        if restore:
            # write the saved log into the fresh sandbox BEFORE any daemon starts, so when
            # the daemon later starts with `-c` it continues this (only) session file.
            log_text = self.registry.get_log(cid)
            if log_text and session_backup.restore_session(sbx, log_text):
                log.info("restored %d bytes of history into %s for %s",
                         len(log_text), sbx.sandbox_id, cid)
        self.registry.upsert_sandbox(cid, sbx.sandbox_id, config.TEMPLATE_VERSION)
        log.info("created sandbox %s for conversation %s", sbx.sandbox_id, cid)
        return sbx

    def _create(self, cid: str) -> Sandbox:
        return Sandbox.create(
            config.TEMPLATE_ALIAS,
            timeout=config.IDLE_PAUSE,
            metadata={"conversation_id": cid},       # debug label only; never the lookup path (Q3)
            lifecycle=SandboxLifecycle(on_timeout="pause", auto_resume=False),
        )

    def reset(self, cid: str) -> None:
        """Kill the conversation's sandbox but KEEP the conversation + its log; the next
        turn reprovisions and restores from the log. (Deleting the chat is a separate op.)"""
        row = self.registry.get(cid)
        if row is not None and row.sandbox_id:
            try:
                Sandbox.connect(row.sandbox_id).kill()
            except Exception as e:
                log.warning("reset: couldn't kill %s (%r) — clearing anyway", row.sandbox_id, e)
        self.registry.clear_sandbox(cid)
