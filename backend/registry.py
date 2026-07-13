"""
Sandbox Registry (Q3): user_id -> sandbox_id, and the persisted session log.

Postgres-backed. Each method borrows a connection from the pool (its own per thread),
so there is no manual lock — the pool makes concurrent access safe (the Phase-3
single-connection hazard is gone).

Two concerns, deliberately separate methods:
- metadata (get/upsert/touch/delete): small, read on every turn. `get` never selects
  the big `log` blob.
- the log (save_log/get_log): the full session JSONL. Written after each turn; read
  only for disaster restore (when the sandbox itself is gone).
"""

from dataclasses import dataclass

from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class SandboxRow:
    user_id: str
    sandbox_id: str
    template_version: str
    status: str
    # note: `log` is intentionally NOT here — it's a large blob, fetched separately.


class Registry:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def get(self, user_id: str) -> SandboxRow | None:
        with self.pool.connection() as conn:
            r = conn.execute(
                "SELECT user_id, sandbox_id, template_version, status "
                "FROM sessions WHERE user_id = %s",
                (user_id,),
                # never selects `log` — it's a large blob, fetched via get_log()
            ).fetchone()
        return SandboxRow(user_id=r[0], sandbox_id=r[1], template_version=r[2], status=r[3]) if r else None

    def upsert(self, user_id: str, sandbox_id: str, template_version: str) -> SandboxRow:
        """Record (or replace) which sandbox a user owns. Called after every create."""
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO sessions (user_id, sandbox_id, template_version)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    sandbox_id       = EXCLUDED.sandbox_id,
                    template_version = EXCLUDED.template_version,
                    status           = 'active',
                    updated_at       = now()
                """,
                (user_id, sandbox_id, template_version),
            )
        row = self.get(user_id)
        assert row is not None
        return row

    def touch(self, user_id: str) -> None:
        """Mark activity — drives idle reporting and, later, cleanup of dormant users."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = now() WHERE user_id = %s",
                (user_id,),
            )

    def delete(self, user_id: str) -> None:
        """Forget the mapping. Used by reset and offboarding; the log row goes with it."""
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))

    def save_log(self, user_id: str, log: str) -> None:
        """Persist the full session JSONL after a turn (this IS the backup)."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE sessions SET log = %s, updated_at = now() WHERE user_id = %s",
                (log, user_id),
            )

    def get_log(self, user_id: str) -> str | None:
        """The full session JSONL, for disaster restore (sandbox gone)."""
        with self.pool.connection() as conn:
            r = conn.execute(
                "SELECT log FROM sessions WHERE user_id = %s", (user_id,)
            ).fetchone()
        return r[0] if r and r[0] else None
