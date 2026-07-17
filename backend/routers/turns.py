"""
Turn lifecycle endpoints (Q15), scoped to a conversation:

  POST /conversations/{cid}/messages -> text/event-stream of turn events (409 if one runs, Q17)
  POST /conversations/{cid}/steer    -> inject a mid-turn message (409 if no turn running)
  POST /conversations/{cid}/abort    -> stop the current turn
  POST /conversations/{cid}/reset    -> kill + reprovision the conversation's sandbox

Thin by design — all lifecycle + concurrency logic lives in TurnRunner. Ownership is
enforced by require_conversation (the cid must belong to the token's user).
"""

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.deps import require_conversation, runner

router = APIRouter()


@router.post("/conversations/{conversation_id}/messages")
async def messages(request: Request, cid: str = Depends(require_conversation)):
    body = await request.json()
    message = body.get("message", "")

    # Claim the slot synchronously at admission (fast, no I/O) so a concurrent same-conversation
    # /messages gets an immediate, deterministic 409 — independent of provisioning speed.
    if not runner.try_claim(cid):
        return JSONResponse({"ok": False, "error": "turn_in_progress"}, status_code=409)

    # Run the turn in a dedicated thread whose finally ALWAYS releases the slot (via
    # run_claimed), and relay its events through a queue. If the browser goes away (refresh,
    # tab close, Vercel 60s cut), a suspended sync generator would never run that finally —
    # so the async relay watches request.is_disconnected() and, on disconnect, aborts the
    # turn — ending the daemon loop, running the thread's finally, freeing the slot in seconds.
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    DONE = object()

    def pump():
        try:
            for ev in runner.run_claimed(cid, message):
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
            runner.abort(cid)   # no-op on a clean end; stops an orphaned turn on disconnect

    return StreamingResponse(sse(), media_type="text/event-stream")


@router.post("/conversations/{conversation_id}/steer")
async def steer(request: Request, cid: str = Depends(require_conversation)):
    body = await request.json()
    ok = runner.steer(cid, body.get("message", ""))
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)


@router.post("/conversations/{conversation_id}/abort")
def abort(cid: str = Depends(require_conversation)):
    ok = runner.abort(cid)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 409)


@router.post("/conversations/{conversation_id}/reset")
def reset(cid: str = Depends(require_conversation)):
    runner.reset(cid)
    return {"ok": True}


@router.get("/conversations/{conversation_id}/status")
def status(cid: str = Depends(require_conversation)):
    """Is a turn currently running for this conversation? Lets the client recover when a
    long turn's SSE stream is cut mid-way (it polls this, then reloads the result)."""
    return {"busy": cid in runner.busy_snapshot()}
