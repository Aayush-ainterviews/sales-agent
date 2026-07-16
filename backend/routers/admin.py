"""
Admin (role=admin): monitor all users + batches, and unstick a user (reset/abort).
No approve/send — admin is monitor + reset only.

  GET  /admin/users
  GET  /admin/batches
  POST /admin/users/{id}/reset
  POST /admin/users/{id}/abort
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


@router.get("/admin/users")
def admin_users(admin: str = Depends(require_admin)):
    """Every user + their sandbox metadata, merged with live turn state (who's busy)."""
    busy = runner.busy_snapshot()
    users = runner.registry.list_all()
    for u in users:
        b = busy.get(u["user_id"])
        u["turn"] = ({"busy": True, **b} if b else {"busy": False})
    # include busy users that have no registry row yet (mid-first-provision)
    known = {u["user_id"] for u in users}
    for uid, b in busy.items():
        if uid not in known:
            users.append({"user_id": uid, "sandbox_id": None, "status": "provisioning",
                          "turn": {"busy": True, **b}})
    return {"users": users}


@router.get("/admin/batches")
def admin_batches(status: str = "pending", admin: str = Depends(require_admin)):
    """All users' batches at a given status (no user filter)."""
    return {"batches": [
        {**_summary(b), "user_id": b.user_id}
        for b in runner.batches.list_by_status(status)
    ]}


@router.post("/admin/users/{user_id}/reset")
def admin_reset(user_id: str, admin: str = Depends(require_admin)):
    """Unstick a user: clear their turn slot + reprovision their sandbox."""
    runner.reset(user_id)
    event(log, "admin_action", action="reset", target=user_id, admin=admin)
    return {"ok": True}


@router.post("/admin/users/{user_id}/abort")
def admin_abort(user_id: str, admin: str = Depends(require_admin)):
    """Stop a user's running turn (frees the slot without killing the sandbox)."""
    ok = runner.abort(user_id)
    event(log, "admin_action", action="abort", target=user_id, admin=admin, ok=ok)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)
