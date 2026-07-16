"""
Turn lifecycle endpoints (Q15): SSE stream for a turn, plus steer/abort/reset.

  POST /users/{id}/messages -> text/event-stream of turn events (409 if one already runs, Q17)
  POST /users/{id}/steer    -> inject a mid-turn message (409 if no turn running)
  POST /users/{id}/abort    -> stop the current turn
  POST /users/{id}/reset    -> kill + reprovision the sandbox

Thin by design — all lifecycle + concurrency logic lives in TurnRunner.
"""

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.auth import require_user
from backend.deps import runner

router = APIRouter()


@router.post("/users/{user_id}/messages")
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


@router.post("/users/{user_id}/steer")
async def steer(user_id: str, request: Request, user: str = Depends(require_user)):
    body = await request.json()
    ok = runner.steer(user, body.get("message", ""))
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)


@router.post("/users/{user_id}/abort")
def abort(user_id: str, user: str = Depends(require_user)):
    ok = runner.abort(user)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)


@router.post("/users/{user_id}/reset")
def reset(user_id: str, user: str = Depends(require_user)):
    runner.reset(user)
    return {"ok": True}
