"""
Postgres connection pool + schema. Nothing else lives here.

The pool hands each thread its own connection, so the callers (Registry) need no
manual lock — this is what retired the Phase-3 single-connection lock.

The `sessions` table is the store: one row per user (user_id UNIQUE), holding which
sandbox is theirs plus the full pi session JSONL in `log` (that column IS the backup;
no R2/S3). `pending_batches` (Phase 4) is the approval queue — one row per Draft Batch
awaiting a human decision; the whole batch is one `jsonb` value (Q22), no per-lead rows.
"""

import atexit

from psycopg_pool import ConnectionPool

from backend.config import DATABASE_URL

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          text UNIQUE NOT NULL,
    sandbox_id       text NOT NULL,
    pi_session_id    text,
    template_version text NOT NULL,
    status           text NOT NULL DEFAULT 'active',
    log              text,                              -- full session JSONL (the backup)
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pending_batches (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     text NOT NULL,
    batch_json  jsonb NOT NULL,                         -- whole batch (campaign + leads[])
    status      text NOT NULL DEFAULT 'pending',        -- pending|approved|rejected|sent|invalid|failed
    result      jsonb,                                  -- send summary once sent: {sent, failed, errors}
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
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
