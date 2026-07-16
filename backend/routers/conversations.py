"""
Conversations (multi-session): list a user's chats, start a new one, open one (its message
history), rename, or delete.

  GET    /users/{user_id}/conversations        -> list (id, title, updated_at), newest first
  POST   /users/{user_id}/conversations        -> create a new chat, returns {id}
  GET    /conversations/{cid}/messages          -> parsed message history for rendering
  PATCH  /conversations/{cid}                    -> rename
  DELETE /conversations/{cid}                    -> kill sandbox + forget the chat

A conversation's sandbox is created lazily on its first turn, so a new chat is cheap.
"""

import json
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.auth import require_user
from backend.deps import require_conversation, runner

log = logging.getLogger("app")
router = APIRouter()


def _parse_history(jsonl: str) -> list[dict]:
    """pi session JSONL -> [{role, content}] for the chat UI. Keeps user + assistant text;
    drops tool calls/results and control lines (they're activity, not conversation)."""
    out: list[dict] = []
    for line in jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("type") != "message":
            continue
        m = o.get("message") or {}
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            text = "".join(p.get("text", "") for p in content
                           if isinstance(p, dict) and p.get("type") == "text")
        elif isinstance(content, str):
            text = content
        else:
            text = ""
        if role == "assistant" and not text.strip():
            continue   # an assistant turn that was only tool calls
        out.append({"role": role, "content": text})
    return out


@router.get("/users/{user_id}/conversations")
def list_conversations(user_id: str, user: str = Depends(require_user)):
    return {"conversations": runner.registry.list_by_user(user)}


@router.post("/users/{user_id}/conversations")
def create_conversation(user_id: str, user: str = Depends(require_user)):
    cid = runner.registry.create(user)
    return {"id": cid}


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(cid: str = Depends(require_conversation)):
    """The chat's history, parsed from its saved session log (empty for a fresh chat)."""
    jsonl = runner.registry.get_log(cid)
    return {"messages": _parse_history(jsonl) if jsonl else []}


class RenameReq(BaseModel):
    title: str


@router.patch("/conversations/{conversation_id}")
def rename_conversation(req: RenameReq, cid: str = Depends(require_conversation)):
    runner.registry.set_title(cid, req.title.strip()[:120] or "Untitled")
    return {"ok": True}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(cid: str = Depends(require_conversation)):
    runner.delete_conversation(cid)
    return {"ok": True}
