"""
Admin (role=admin): monitor all conversations + batches, and unstick a conversation
(reset/abort). No approve/send — admin is monitor + reset only.

  GET  /admin/conversations
  GET  /admin/batches
  POST /admin/conversations/{cid}/reset
  POST /admin/conversations/{cid}/abort
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.auth import require_admin
from backend.deps import runner
from backend.logging_setup import event
from backend.routers.batches import _summary

log = logging.getLogger("app")
router = APIRouter()


@router.get("/admin/conversations")
def admin_conversations(admin: str = Depends(require_admin)):
    """Every conversation + its metadata, merged with live turn state (which are busy)."""
    busy = runner.busy_snapshot()
    convs = runner.registry.list_all()
    for c in convs:
        b = busy.get(c["id"])
        c["turn"] = ({"busy": True, **b} if b else {"busy": False})
    # include busy conversations that have no row yet (mid-first-provision, unlikely)
    known = {c["id"] for c in convs}
    for cid, b in busy.items():
        if cid not in known:
            convs.append({"id": cid, "user_id": None, "sandbox_id": None, "status": "provisioning",
                          "title": None, "turn": {"busy": True, **b}})
    return {"conversations": convs}


@router.get("/admin/batches")
def admin_batches(status: str = "pending", admin: str = Depends(require_admin)):
    """All users' batches at a given status (no user filter)."""
    return {"batches": [
        {**_summary(b), "user_id": b.user_id, "conversation_id": b.conversation_id}
        for b in runner.batches.list_by_status(status)
    ]}


@router.post("/admin/conversations/{conversation_id}/reset")
def admin_reset(conversation_id: str, admin: str = Depends(require_admin)):
    """Unstick a conversation: clear its turn slot + reprovision its sandbox."""
    runner.reset(conversation_id)
    event(log, "admin_action", action="reset", target=conversation_id, admin=admin)
    return {"ok": True}


@router.post("/admin/conversations/{conversation_id}/abort")
def admin_abort(conversation_id: str, admin: str = Depends(require_admin)):
    """Stop a conversation's running turn (frees the slot without killing the sandbox)."""
    ok = runner.abort(conversation_id)
    event(log, "admin_action", action="abort", target=conversation_id, admin=admin, ok=ok)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)
