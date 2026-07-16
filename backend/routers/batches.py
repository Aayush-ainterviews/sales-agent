"""
Draft Batch approval queue (Phase 4): list a user's pending batches, inspect one, and
approve (backend sends via ZeptoMail, then hands the result back to the agent) or reject.

  GET  /users/{id}/batches
  GET  /users/{id}/batches/{batch_id}
  POST /users/{id}/batches/{batch_id}/approve
  POST /users/{id}/batches/{batch_id}/reject
"""

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException

from backend import send_executor
from backend.auth import require_user
from backend.deps import runner
from backend.logging_setup import event

log = logging.getLogger("app")
router = APIRouter()


def _own_batch(batch_id: str, user: str):
    b = runner.batches.get(batch_id)
    if b is None or b.user_id != user:
        raise HTTPException(status_code=404, detail="batch not found")
    return b


def _summary(b) -> dict:
    leads = b.batch_json.get("leads", []) if isinstance(b.batch_json, dict) else []
    return {"id": b.id, "campaign": b.batch_json.get("campaign") if isinstance(b.batch_json, dict) else None,
            "leads": len(leads), "status": b.status, "result": b.result}


@router.get("/users/{user_id}/batches")
def list_batches(user_id: str, status: str = "pending", user: str = Depends(require_user)):
    return {"batches": [_summary(b) for b in runner.batches.list_by_status(status, user)]}


@router.get("/users/{user_id}/batches/{batch_id}")
def get_batch(user_id: str, batch_id: str, user: str = Depends(require_user)):
    b = _own_batch(batch_id, user)
    return {"id": b.id, "status": b.status, "batch": b.batch_json, "result": b.result}


@router.post("/users/{user_id}/batches/{batch_id}/approve")
def approve_batch(user_id: str, batch_id: str, user: str = Depends(require_user)):
    b = _own_batch(batch_id, user)
    if b.status != "pending":
        raise HTTPException(status_code=409, detail=f"batch is '{b.status}', not pending")
    runner.batches.set_status(batch_id, "approved")
    result = send_executor.send(b.batch_json)          # Backend sends (creds only here, Q21)
    status = "sent" if result["sent"] > 0 and result["failed"] == 0 else (
        "sent" if result["sent"] > 0 else "failed")
    runner.batches.set_result(batch_id, status, result)

    # hand the outcome back to the agent as a background feedback turn (Phase 4, Step 6)
    campaign = b.batch_json.get("campaign", "batch") if isinstance(b.batch_json, dict) else "batch"
    fb = (f"Send result for batch '{campaign}': {result['sent']} sent, {result['failed']} failed. "
          f"Plan follow-ups if appropriate.")
    threading.Thread(target=runner.send_feedback, args=(user, fb), daemon=True).start()

    event(log, "batch_decision", batch_id=batch_id, user_id=user, action="approve",
          sent=result["sent"], failed=result["failed"], status=status)
    return {"ok": True, "status": status, "result": result}


@router.post("/users/{user_id}/batches/{batch_id}/reject")
def reject_batch(user_id: str, batch_id: str, user: str = Depends(require_user)):
    b = _own_batch(batch_id, user)
    runner.batches.set_status(batch_id, "rejected")
    event(log, "batch_decision", batch_id=batch_id, user_id=user, action="reject")
    return {"ok": True, "status": "rejected"}
