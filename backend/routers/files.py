"""
Sandbox file endpoints, scoped to a conversation (each conversation has its own sandbox).
All paths are confined to the sandbox cwd (/home/user) — no traversal, no other sandboxes.

  POST /conversations/{cid}/upload -> store an uploaded file under uploads/, return its path
  GET  /conversations/{cid}/file   -> download one file
  PUT  /conversations/{cid}/file   -> write content back to a file (edited-table save-back)
"""

import base64
import logging
import os
import shlex

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from backend import config
from backend.deps import require_conversation, runner
from backend.logging_setup import event

log = logging.getLogger("app")
router = APIRouter()


@router.post("/conversations/{conversation_id}/upload")
def upload_file(file: UploadFile = File(...), cid: str = Depends(require_conversation)):
    """Store a user-uploaded file in the conversation's sandbox under uploads/ and return its
    path, so the agent can read it in the next turn. Sync def -> runs in the threadpool."""
    data = file.file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 25MB)")
    name = os.path.basename(file.filename or "upload").replace("/", "_").strip() or "upload"
    dest = f"{config.SANDBOX_CWD}/uploads/{name}"
    try:
        sbx = runner.sandboxes.get_or_create(cid)
        sbx.commands.run(f"mkdir -p {config.SANDBOX_CWD}/uploads", timeout=30)
        sbx.files.write(dest, data)
    except Exception as e:
        log.warning("upload write failed for %s: %r", cid, e)
        raise HTTPException(status_code=500, detail="could not store file in sandbox")
    event(log, "file_upload", conversation_id=cid, filename=name, size=len(data))
    return {"ok": True, "path": f"uploads/{name}", "name": name, "size": len(data)}


def _read_sandbox_file(cid: str, path: str) -> bytes:
    """Read one file from the conversation's sandbox, confined to the sandbox cwd. base64 over
    commands.run so binary files survive intact. Raises HTTPException on any problem."""
    row = runner.registry.get(cid)
    if row is None or not row.sandbox_id:
        raise HTTPException(status_code=404, detail="no sandbox for conversation")
    base = config.SANDBOX_CWD
    target = path if path.startswith("/") else f"{base}/{path}"
    target = os.path.normpath(target)
    if target != base and not target.startswith(base + "/"):
        raise HTTPException(status_code=400, detail="path outside allowed root")
    try:
        sbx = runner.sandboxes.get_or_create(cid)
        res = sbx.commands.run(f"base64 -w0 {shlex.quote(target)}", timeout=60)
        raw = base64.b64decode(res.stdout) if res.stdout else b""
    except Exception as e:
        log.warning("file read failed for %s %s: %r", cid, target, e)
        raise HTTPException(status_code=404, detail="file not found or unreadable")
    if not raw:
        raise HTTPException(status_code=404, detail="file is empty or missing")
    return raw


@router.get("/conversations/{conversation_id}/file")
def get_file(path: str, cid: str = Depends(require_conversation)):
    """Stream one output file out of the conversation's sandbox as a download."""
    raw = _read_sandbox_file(cid, path)
    name = os.path.basename(os.path.normpath(path)) or "download"
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


class WriteReq(BaseModel):
    path: str
    content: str


@router.put("/conversations/{conversation_id}/file")
def write_file(req: WriteReq, cid: str = Depends(require_conversation)):
    """Save edited table data back into the conversation's sandbox file (path-confined)."""
    base = config.SANDBOX_CWD
    target = req.path if req.path.startswith("/") else f"{base}/{req.path}"
    target = os.path.normpath(target)
    if target != base and not target.startswith(base + "/"):
        raise HTTPException(status_code=400, detail="path outside allowed root")
    if len(req.content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="content too large (max 25MB)")
    try:
        sbx = runner.sandboxes.get_or_create(cid)
        sbx.commands.run(f"mkdir -p {shlex.quote(os.path.dirname(target))}", timeout=30)
        sbx.files.write(target, req.content)
    except Exception as e:
        log.warning("file write failed for %s %s: %r", cid, target, e)
        raise HTTPException(status_code=500, detail="could not write file")
    event(log, "file_write", conversation_id=cid, path=req.path, bytes=len(req.content))
    return {"ok": True, "path": req.path}
