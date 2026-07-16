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

from psycopg_pool import ConnectionPool

from backend.config import DATABASE_URL

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
    return _pool
