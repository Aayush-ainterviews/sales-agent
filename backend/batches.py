"""
PendingBatches (Phase 4, Q22): the approval queue.

One row per Draft Batch awaiting a human decision. The whole batch (campaign + leads[])
is a single `jsonb` value — no per-lead rows (that tracking is deferred, Q10b). Pool per
call, no manual lock (same as Registry).

Lifecycle: pending → approved → sent (with `result`), or → rejected, or → invalid.
"""

from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


@dataclass(frozen=True)
class PendingBatch:
    id: str
    user_id: str
    batch_json: dict
    status: str
    result: dict | None
    created_at: Any


class PendingBatches:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def insert(self, user_id: str, batch_json: dict, status: str = "pending") -> str:
        """Queue a batch; returns its id."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO pending_batches (user_id, batch_json, status) "
                "VALUES (%s, %s, %s) RETURNING id",
                (user_id, Jsonb(batch_json), status),
            ).fetchone()
        return str(row[0])

    def get(self, batch_id: str) -> PendingBatch | None:
        with self.pool.connection() as conn:
            r = conn.execute(
                "SELECT id, user_id, batch_json, status, result, created_at "
                "FROM pending_batches WHERE id = %s",
                (batch_id,),
            ).fetchone()
        return self._row(r) if r else None

    def list_by_status(self, status: str, user_id: str | None = None) -> list[PendingBatch]:
        q = ("SELECT id, user_id, batch_json, status, result, created_at "
             "FROM pending_batches WHERE status = %s")
        params: tuple = (status,)
        if user_id is not None:
            q += " AND user_id = %s"
            params = (status, user_id)
        q += " ORDER BY created_at"
        with self.pool.connection() as conn:
            rows = conn.execute(q, params).fetchall()
        return [self._row(r) for r in rows]

    def set_status(self, batch_id: str, status: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE pending_batches SET status = %s, updated_at = now() WHERE id = %s",
                (status, batch_id),
            )

    def set_result(self, batch_id: str, status: str, result: dict) -> None:
        """Record the send outcome and final status in one write."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE pending_batches SET status = %s, result = %s, updated_at = now() "
                "WHERE id = %s",
                (status, Jsonb(result), batch_id),
            )

    def delete(self, batch_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM pending_batches WHERE id = %s", (batch_id,))

    @staticmethod
    def _row(r) -> PendingBatch:
        return PendingBatch(
            id=str(r[0]), user_id=r[1], batch_json=r[2], status=r[3],
            result=r[4], created_at=r[5],
        )
