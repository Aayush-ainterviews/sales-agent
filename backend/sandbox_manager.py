"""
SandboxManager (Q4): one self-healing path that provisions, resumes, and recovers.

get_or_create(user_id) is the whole story:
  registry has a row -> try Sandbox.connect (paused sandbox resumes in ~1s)
  connect fails, or no row -> Sandbox.create from the template, record the row
The same code covers a first-time user, a returning user, and a user whose
sandbox E2B has since reaped. There is no separate "recovery" branch (Q3).

Secrets are NOT passed here — nothing at create time carries them (Q11). They
enter only when the daemon starts (DaemonClient, next module).
"""

import logging

from e2b import Sandbox, SandboxLifecycle

from backend import config, session_backup
from backend.registry import Registry

log = logging.getLogger("sandbox_manager")


class SandboxManager:
    def __init__(self, registry: Registry):
        self.registry = registry

    def get_or_create(self, user_id: str) -> Sandbox:
        row = self.registry.get(user_id)

        if row is not None:
            try:
                sbx = Sandbox.connect(row.sandbox_id)
                self.registry.touch(user_id)
                log.info("resumed sandbox %s for %s", row.sandbox_id, user_id)
                return sbx
            except Exception as e:
                # row was stale: sandbox killed / reaped / unknown to E2B. The old sandbox's
                # disk (and its session file) is gone -> recreate AND restore history from
                # the Postgres log so the conversation survives the disaster (Q6 restore).
                log.warning("connect to %s failed (%r); recreating + restoring for %s",
                            row.sandbox_id, e, user_id)
                return self._create_and_record(user_id, restore=True)

        # brand-new user: nothing to restore
        return self._create_and_record(user_id, restore=False)

    def _create_and_record(self, user_id: str, restore: bool) -> Sandbox:
        sbx = self._create(user_id)
        if restore:
            # write the saved log into the fresh sandbox BEFORE any daemon starts, so when
            # the daemon later starts with `-c` it continues this (only) session file.
            log_text = self.registry.get_log(user_id)
            if log_text and session_backup.restore_session(sbx, log_text):
                log.info("restored %d bytes of history into %s for %s",
                         len(log_text), sbx.sandbox_id, user_id)
        self.registry.upsert(user_id, sbx.sandbox_id, config.TEMPLATE_VERSION)
        log.info("created sandbox %s for %s", sbx.sandbox_id, user_id)
        return sbx

    def _create(self, user_id: str) -> Sandbox:
        return Sandbox.create(
            config.TEMPLATE_ALIAS,
            timeout=config.IDLE_PAUSE,
            metadata={"user_id": user_id},          # debug label only; never the lookup path (Q3)
            lifecycle=SandboxLifecycle(on_timeout="pause", auto_resume=False),
            # allow_internet_access left default for now; Phase 1 egress allowlist is a later tightening (Q13)
        )

    def reset(self, user_id: str) -> None:
        """Kill the sandbox and forget the mapping; next request provisions fresh. Backups survive."""
        row = self.registry.get(user_id)
        if row is not None:
            try:
                Sandbox.connect(row.sandbox_id).kill()
            except Exception as e:
                log.warning("reset: couldn't kill %s (%r) — deleting row anyway", row.sandbox_id, e)
            self.registry.delete(user_id)
