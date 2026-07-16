"""
Conversation store: conversation_id -> sandbox + the persisted session log.

Postgres-backed. Each conversation is the sandbox key (per-conversation sandbox); `user_id`
is carried for listing a user's chats and for ownership checks. Each method borrows a
connection from the pool (its own per thread), so there is no manual lock.

Two concerns, deliberately separate:
- metadata (get/create/upsert_sandbox/touch/clear_sandbox/delete/set_title): small, read on
  every turn. `get` never selects the big `log` blob.
- the log (save_log/get_log): the full session JSONL. Written after each turn; read only for
  history display and disaster restore.

The class is still named Registry so the wiring (deps, SandboxManager, TurnRunner) is stable.
"""

from dataclasses import dataclass

from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class ConversationRow:
    id: str
    user_id: str
    sandbox_id: str | None
    template_version: str | None
    status: str
    title: str | None
    # note: `log` is intentionally NOT here — it's a large blob, fetched separately.


class Registry:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def create(self, user_id: str) -> str:
        """Start a new conversation (no sandbox yet). Returns its id."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id",
                (user_id,),
            ).fetchone()
        return str(row[0])

    def get(self, cid: str) -> ConversationRow | None:
        with self.pool.connection() as conn:
            r = conn.execute(
                "SELECT id, user_id, sandbox_id, template_version, status, title "
                "FROM conversations WHERE id = %s",
                (cid,),
                # never selects `log` — it's a large blob, fetched via get_log()
            ).fetchone()
        return ConversationRow(id=str(r[0]), user_id=r[1], sandbox_id=r[2], template_version=r[3],
                               status=r[4], title=r[5]) if r else None

    def upsert_sandbox(self, cid: str, sandbox_id: str, template_version: str) -> None:
        """Record which sandbox now serves this conversation (after a create/resume)."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE conversations SET sandbox_id = %s, template_version = %s, "
                "status = 'active', updated_at = now() WHERE id = %s",
                (sandbox_id, template_version, cid),
            )

    def clear_sandbox(self, cid: str) -> None:
        """Forget the sandbox but keep the conversation + its log (reset/reprovision)."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE conversations SET sandbox_id = NULL, updated_at = now() WHERE id = %s",
                (cid,),
            )

    def touch(self, cid: str) -> None:
        """Mark activity — drives sidebar ordering (most-recent first)."""
        with self.pool.connection() as conn:
            conn.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (cid,))

    def set_title(self, cid: str, title: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE conversations SET title = %s WHERE id = %s", (title, cid))

    def delete(self, cid: str) -> None:
        """Forget the conversation entirely (its log goes with it)."""
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM conversations WHERE id = %s", (cid,))

    def list_by_user(self, user_id: str) -> list[dict]:
        """A user's conversations for the sidebar, most-recently-active first."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, title, status, updated_at FROM conversations "
                "WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [{"id": str(r[0]), "title": r[1], "status": r[2],
                 "updated_at": r[3].isoformat() if r[3] else None} for r in rows]

    def list_all(self) -> list[dict]:
        """Every conversation + sandbox metadata (admin monitor). Never selects `log`."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, user_id, sandbox_id, status, title, updated_at "
                "FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [{"id": str(r[0]), "user_id": r[1], "sandbox_id": r[2], "status": r[3],
                 "title": r[4], "updated_at": r[5].isoformat() if r[5] else None} for r in rows]

    def save_log(self, cid: str, log: str) -> None:
        """Persist the full session JSONL after a turn (this IS the backup)."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE conversations SET log = %s, updated_at = now() WHERE id = %s",
                (log, cid),
            )

    def get_log(self, cid: str) -> str | None:
        """The full session JSONL — for history display and disaster restore."""
        with self.pool.connection() as conn:
            r = conn.execute("SELECT log FROM conversations WHERE id = %s", (cid,)).fetchone()
        return r[0] if r and r[0] else None
