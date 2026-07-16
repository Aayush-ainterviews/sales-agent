"""
Postgres connection pool + schema. Nothing else lives here.

The pool hands each thread its own connection, so the callers (Registry) need no
manual lock — this is what retired the Phase-3 single-connection lock.

Store:
- `conversations` — one row per chat (many per user). Holds which sandbox serves it plus
  the full pi session JSONL in `log` (that column IS the backup; no R2/S3). A conversation
  IS the sandbox key now (per-conversation sandbox); `user_id` is for listing + ownership.
- `pending_batches` (Phase 4) — the approval queue; one row per Draft Batch. `conversation_id`
  ties a batch to the chat it was drafted in, so approve's feedback turn routes back there.
"""

import atexit
import json
import logging

from psycopg_pool import ConnectionPool

from backend.config import DATABASE_URL

log = logging.getLogger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          text NOT NULL,
    sandbox_id       text,                              -- NULL until the first turn provisions
    template_version text,
    status           text NOT NULL DEFAULT 'active',
    title            text,                              -- set from the first user message
    log              text,                              -- full session JSONL (the backup)
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_user_idx ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS pending_batches (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         text NOT NULL,
    conversation_id text,                               -- the chat this batch was drafted in
    batch_json      jsonb NOT NULL,                     -- whole batch (campaign + leads[])
    status          text NOT NULL DEFAULT 'pending',    -- pending|approved|rejected|sent|invalid|failed
    result          jsonb,                              -- send summary once sent: {sent, failed, errors}
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
-- existing deploys: add the column if the table predates it
ALTER TABLE pending_batches ADD COLUMN IF NOT EXISTS conversation_id text;
"""

_pool: ConnectionPool | None = None


def _first_user_line(jsonl: str) -> str | None:
    """First user message text from a pi session JSONL — used as an imported chat's title."""
    for line in jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("type") != "message":
            continue
        m = o.get("message") or {}
        if m.get("role") != "user":
            continue
        c = m.get("content")
        text = "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text") \
            if isinstance(c, list) else (c if isinstance(c, str) else "")
        text = text.strip()
        if text:
            return text[:60]
    return None


def _migrate_old_sessions(conn) -> None:
    """One-time: pre-conversations chats lived in the old single-session `sessions` table
    (one row per user). Import each such history into `conversations` so it shows in the
    sidebar (and can be continued). Idempotent — marks a session 'imported' once done, and
    is best-effort so it never blocks boot."""
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'sessions'"
    ).fetchone()
    if not exists:
        return
    rows = conn.execute(
        "SELECT user_id, template_version, log, created_at, updated_at FROM sessions "
        "WHERE log IS NOT NULL AND status IS DISTINCT FROM 'imported'"
    ).fetchall()
    for user_id, tv, log_text, created_at, updated_at in rows:
        title = _first_user_line(log_text) or "Imported chat"
        conn.execute(
            "INSERT INTO conversations (user_id, template_version, status, title, log, created_at, updated_at) "
            "VALUES (%s, %s, 'active', %s, %s, %s, %s)",
            (user_id, tv, title, log_text, created_at, updated_at),
        )
    conn.execute("UPDATE sessions SET status = 'imported' WHERE log IS NOT NULL AND status IS DISTINCT FROM 'imported'")
    if rows:
        log.info("migrated %d old session(s) into conversations", len(rows))


def pool() -> ConnectionPool:
    """Return the process-wide pool, creating (and migrating) it on first call."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=True)
        # close the pool's background threads cleanly at exit (else short scripts warn
        # "cannot join thread at interpreter shutdown" from the pool's __del__)
        atexit.register(_pool.close)
        with _pool.connection() as conn:
            conn.execute(SCHEMA)
        try:
            with _pool.connection() as conn:
                _migrate_old_sessions(conn)   # own transaction; rolls back cleanly on error
        except Exception as e:
            log.warning("old-session migration skipped: %r", e)
    return _pool
