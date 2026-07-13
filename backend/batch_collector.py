"""
Batch collector (Phase 4): after a turn, pull Draft Batches the agent wrote to the
sandbox outbox into the pending_batches approval queue.

Skill-based submission means validation happens HERE, post-turn (Q10 weakness accepted
for v1): a malformed batch is queued with status='invalid' rather than crashing. Each
collected file is deleted so it isn't picked up again next turn.
"""

import json
import logging
import shlex

from e2b import Sandbox

from backend.batches import PendingBatches

log = logging.getLogger("batch_collector")

OUTBOX = "/home/user/outbox"


def _valid(b) -> bool:
    """A batch is valid if it has a campaign and >=1 lead, each with email/subject/body."""
    if not isinstance(b, dict) or not isinstance(b.get("campaign"), str):
        return False
    leads = b.get("leads")
    if not isinstance(leads, list) or not leads:
        return False
    for lead in leads:
        if not isinstance(lead, dict):
            return False
        if not all(isinstance(lead.get(k), str) and lead.get(k) for k in ("email", "subject", "body")):
            return False
    return True


def collect(sandbox: Sandbox, user_id: str, batches: PendingBatches) -> list[str]:
    """Read every *.json in the sandbox outbox into pending_batches. Returns the ids.
    Best-effort: never let collection break a turn."""
    try:
        listing = sandbox.commands.run(
            f"ls {OUTBOX}/*.json 2>/dev/null || true", timeout=30
        ).stdout.strip()
    except Exception as e:
        log.warning("outbox list failed for %s: %r", user_id, e)
        return []
    if not listing:
        return []

    collected: list[str] = []
    for path in (p.strip() for p in listing.splitlines() if p.strip()):
        try:
            text = sandbox.commands.run(f"cat {shlex.quote(path)}", timeout=30).stdout
            try:
                b = json.loads(text)
                status = "pending" if _valid(b) else "invalid"
                bid = batches.insert(user_id, b, status)
            except json.JSONDecodeError:
                status = "invalid"
                bid = batches.insert(user_id, {"_raw": text[:5000], "_error": "invalid JSON"}, status)
            collected.append(bid)
            sandbox.commands.run(f"rm -f {shlex.quote(path)}", timeout=30)  # don't re-collect
            log.info("collected batch %s (%s) for %s", bid, status, user_id)
        except Exception as e:
            log.warning("collecting %s failed for %s: %r", path, user_id, e)
    return collected
