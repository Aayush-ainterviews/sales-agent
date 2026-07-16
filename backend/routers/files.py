"""
Sandbox file endpoints: upload into the sandbox, download an output file, and save an
edited file back. All paths are confined to the sandbox cwd (/home/user) — no traversal,
no other users' boxes.

  POST /users/{id}/upload -> store an uploaded file under uploads/, return its path
  GET  /users/{id}/file   -> download one file
  PUT  /users/{id}/file   -> write content back to a file (edited-table save-back)
"""

import base64
import logging
import os
import shlex

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from backend import config
from backend.auth import require_user
from backend.deps import runner
from backend.logging_setup import event

log = logging.getLogger("app")
router = APIRouter()


@router.post("/users/{user_id}/upload")
def upload_file(user_id: str, file: UploadFile = File(...), user: str = Depends(require_user)):
    """Store a user-uploaded file in the caller's sandbox under uploads/ and return its
    path, so the agent can read it in the next turn. Sync def -> runs in the threadpool,
    so the blocking sandbox write doesn't stall the event loop."""
    data = file.file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 25MB)")
    name = os.path.basename(file.filename or "upload").replace("/", "_").strip() or "upload"
    dest = f"{config.SANDBOX_CWD}/uploads/{name}"
    try:
        sbx = runner.sandboxes.get_or_create(user)
        sbx.commands.run(f"mkdir -p {config.SANDBOX_CWD}/uploads", timeout=30)
        sbx.files.write(dest, data)
    except Exception as e:
        log.warning("upload write failed for %s: %r", user, e)
        raise HTTPException(status_code=500, detail="could not store file in sandbox")
    event(log, "file_upload", user_id=user, filename=name, size=len(data))
    return {"ok": True, "path": f"uploads/{name}", "name": name, "size": len(data)}


def _read_sandbox_file(user: str, path: str) -> bytes:
    """Read one file from the caller's own sandbox, confined to the sandbox cwd
    (/home/user) — no traversal, no other users' boxes. base64 over commands.run so
    binary files survive intact. Raises HTTPException on any problem."""
    if not runner.registry.get(user):
        raise HTTPException(status_code=404, detail="no sandbox for user")
    base = config.SANDBOX_CWD
    target = path if path.startswith("/") else f"{base}/{path}"
    target = os.path.normpath(target)
    if target != base and not target.startswith(base + "/"):
        raise HTTPException(status_code=400, detail="path outside allowed root")
    try:
        sbx = runner.sandboxes.get_or_create(user)
        res = sbx.commands.run(f"base64 -w0 {shlex.quote(target)}", timeout=60)
        raw = base64.b64decode(res.stdout) if res.stdout else b""
    except Exception as e:
        log.warning("file read failed for %s %s: %r", user, target, e)
        raise HTTPException(status_code=404, detail="file not found or unreadable")
    if not raw:
        raise HTTPException(status_code=404, detail="file is empty or missing")
    return raw


@router.get("/users/{user_id}/file")
def get_file(user_id: str, path: str, user: str = Depends(require_user)):
    """Stream one output file out of the caller's own sandbox as a download."""
    raw = _read_sandbox_file(user, path)
    name = os.path.basename(os.path.normpath(path)) or "download"
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


class WriteReq(BaseModel):
    path: str
    content: str


@router.put("/users/{user_id}/file")
def write_file(user_id: str, req: WriteReq, user: str = Depends(require_user)):
    """Save edited table data back into the caller's sandbox file (path-confined to
    /home/user). The agent's next turn then reads the updated file."""
    base = config.SANDBOX_CWD
    target = req.path if req.path.startswith("/") else f"{base}/{req.path}"
    target = os.path.normpath(target)
    if target != base and not target.startswith(base + "/"):
        raise HTTPException(status_code=400, detail="path outside allowed root")
    if len(req.content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="content too large (max 25MB)")
    try:
        sbx = runner.sandboxes.get_or_create(user)
        sbx.commands.run(f"mkdir -p {shlex.quote(os.path.dirname(target))}", timeout=30)
        sbx.files.write(target, req.content)
    except Exception as e:
        log.warning("file write failed for %s %s: %r", user, target, e)
        raise HTTPException(status_code=500, detail="could not write file")
    event(log, "file_write", user_id=user, path=req.path, bytes=len(req.content))
    return {"ok": True, "path": req.path}
