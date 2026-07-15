"""
FastAPI surface (Q15): SSE for turn events, plain POST for steer/abort/reset.

Thin by design — all lifecycle + concurrency logic lives in TurnRunner. Identity
comes from the bearer token (auth.require_user), not the URL path (Phase 3). Four
endpoints, each scoped to the authenticated user:
  POST /users/{id}/messages   -> text/event-stream of turn events (409 if one already runs, Q17)
  POST /users/{id}/steer      -> inject a mid-turn message (409 if no turn running)
  POST /users/{id}/abort      -> stop the current turn
  POST /users/{id}/reset      -> kill + reprovision the sandbox
"""

import asyncio
import json
import logging
import threading

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from backend import config, db, logging_setup, send_executor
from backend.auth import require_admin, require_user
from backend.logging_setup import event
from backend.registry import Registry
from backend.turn_runner import TurnRunner

logging_setup.configure()          # structured JSON logs (Phase 5)
log = logging.getLogger("app")

config.assert_secrets_present()     # fail loudly at boot if any secret/DB is missing
runner = TurnRunner(Registry(db.pool()))
app = FastAPI(title="sales-ai-agent backend")

# let the browser frontend (a different origin) call this API + read the SSE stream
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/users/{user_id}/messages")
async def messages(user_id: str, request: Request, user: str = Depends(require_user)):
    body = await request.json()
    message = body.get("message", "")

    # Claim the slot synchronously at admission (fast, no I/O) so a concurrent same-user
    # /messages gets an immediate, deterministic 409 — independent of provisioning speed.
    if not runner.try_claim(user):
        return JSONResponse({"ok": False, "error": "turn_in_progress"}, status_code=409)

    # Run the turn in a dedicated thread whose finally ALWAYS releases the slot (via
    # run_claimed), and relay its events through a queue. If the browser goes away
    # (refresh, tab close, Vercel 60s cut), a suspended sync generator would never run
    # that finally — the slot would wedge until the stale-heal. So the async relay watches
    # request.is_disconnected() and, on disconnect, aborts the turn — which ends the daemon
    # loop, runs the thread's finally, and frees the slot within seconds (immediate heal).
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    DONE = object()

    def pump():
        try:
            for ev in runner.run_claimed(user, message):
                loop.call_soon_threadsafe(q.put_nowait, ev)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, DONE)

    threading.Thread(target=pump, daemon=True).start()

    async def sse():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break                      # client gone -> finally aborts + frees the slot
                    continue
                if ev is DONE:
                    break
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            # disconnect / cancel / normal end all land here. On a real end the turn already
            # released its slot, so this abort is a harmless no-op; on a disconnect it's what
            # stops the orphaned turn and unwedges the user immediately.
            runner.abort(user)

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.post("/users/{user_id}/steer")
async def steer(user_id: str, request: Request, user: str = Depends(require_user)):
    body = await request.json()
    ok = runner.steer(user, body.get("message", ""))
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)


@app.post("/users/{user_id}/abort")
def abort(user_id: str, user: str = Depends(require_user)):
    ok = runner.abort(user)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)


@app.post("/users/{user_id}/reset")
def reset(user_id: str, user: str = Depends(require_user)):
    runner.reset(user)
    return {"ok": True}


# --- Draft Batch approval (Phase 4) --------------------------------------

def _own_batch(batch_id: str, user: str):
    b = runner.batches.get(batch_id)
    if b is None or b.user_id != user:
        raise HTTPException(status_code=404, detail="batch not found")
    return b


def _summary(b) -> dict:
    leads = b.batch_json.get("leads", []) if isinstance(b.batch_json, dict) else []
    return {"id": b.id, "campaign": b.batch_json.get("campaign") if isinstance(b.batch_json, dict) else None,
            "leads": len(leads), "status": b.status, "result": b.result}


@app.get("/users/{user_id}/batches")
def list_batches(user_id: str, status: str = "pending", user: str = Depends(require_user)):
    return {"batches": [_summary(b) for b in runner.batches.list_by_status(status, user)]}


@app.get("/users/{user_id}/batches/{batch_id}")
def get_batch(user_id: str, batch_id: str, user: str = Depends(require_user)):
    b = _own_batch(batch_id, user)
    return {"id": b.id, "status": b.status, "batch": b.batch_json, "result": b.result}


@app.post("/users/{user_id}/batches/{batch_id}/approve")
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


@app.post("/users/{user_id}/batches/{batch_id}/reject")
def reject_batch(user_id: str, batch_id: str, user: str = Depends(require_user)):
    b = _own_batch(batch_id, user)
    runner.batches.set_status(batch_id, "rejected")
    event(log, "batch_decision", batch_id=batch_id, user_id=user, action="reject")
    return {"ok": True, "status": "rejected"}


# --- Admin (role=admin): monitor + reset, no approve/send ------------------

@app.get("/admin/users")
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


@app.get("/admin/batches")
def admin_batches(status: str = "pending", admin: str = Depends(require_admin)):
    """All users' batches at a given status (no user filter)."""
    return {"batches": [
        {**_summary(b), "user_id": b.user_id}
        for b in runner.batches.list_by_status(status)
    ]}


@app.post("/admin/users/{user_id}/reset")
def admin_reset(user_id: str, admin: str = Depends(require_admin)):
    """Unstick a user: clear their turn slot + reprovision their sandbox."""
    runner.reset(user_id)
    event(log, "admin_action", action="reset", target=user_id, admin=admin)
    return {"ok": True}


@app.post("/admin/users/{user_id}/abort")
def admin_abort(user_id: str, admin: str = Depends(require_admin)):
    """Stop a user's running turn (frees the slot without killing the sandbox)."""
    ok = runner.abort(user_id)
    event(log, "admin_action", action="abort", target=user_id, admin=admin, ok=ok)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)
