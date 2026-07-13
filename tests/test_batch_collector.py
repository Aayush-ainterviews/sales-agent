"""
Checkpoint G (Phase 4): batch collection. ~3-4 min (real sandbox + a drafting turn).

Part A (deterministic) — the collector itself: write a valid + an invalid batch file
into the sandbox outbox, call collect(), assert the valid one lands as 'pending', the
invalid as 'invalid', and both files are removed (not re-collected).

Part B (agent integration) — the wired-in path: run a real drafting turn that asks the
agent to queue a batch via the submit-batch skill; after the turn, a pending batch shows
up in the queue (this exercises turn_runner's auto-collect + the skill).

Run:  .venv/bin/python tests/test_batch_collector.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend import batch_collector, db  # noqa: E402
from backend.config import assert_secrets_present  # noqa: E402
from backend.registry import Registry  # noqa: E402
from backend.turn_runner import TurnRunner  # noqa: E402

U = "test_collector"
checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def clear_batches(pb, user_id):
    for st in ("pending", "invalid", "approved", "rejected", "sent", "failed"):
        for b in pb.list_by_status(st, user_id):
            pb.delete(b.id)


def main() -> int:
    load_dotenv()
    assert_secrets_present()

    runner = TurnRunner(Registry(db.pool()))
    pb = runner.batches
    runner.reset(U)
    clear_batches(pb, U)

    try:
        sbx = runner.sandboxes.get_or_create(U)

        print("== A. collector: valid + invalid files ==")
        valid = json.dumps({
            "campaign": "collector-test",
            "leads": [{"lead_id": "L1", "email": "a@b.com", "subject": "s", "body": "hello", "evidence": ["x"]}],
        })
        sbx.files.write("/home/user/outbox/valid.json", valid)
        sbx.files.write("/home/user/outbox/bad.json", "{ not valid json")

        ids = batch_collector.collect(sbx, U, pb)
        check("collected 2 files", len(ids) == 2, f"({len(ids)})")
        pending = pb.list_by_status("pending", U)
        invalid = pb.list_by_status("invalid", U)
        check("valid -> pending", len(pending) == 1 and pending[0].batch_json["campaign"] == "collector-test")
        check("bad -> invalid", len(invalid) == 1)

        left = sbx.commands.run("ls /home/user/outbox/*.json 2>/dev/null || true", timeout=30).stdout.strip()
        check("outbox emptied (not re-collected)", left == "", f"({left!r})")
        clear_batches(pb, U)

        print("== B. agent drafts + queues via submit-batch skill ==")
        list(runner.run_turn(
            U,
            "Use the submit-batch skill to queue ONE outreach email for approval. "
            "Lead: email lead@test.com, name Test User, company Acme (they are hiring SDRs). "
            "Subject 'Quick question', body one short sentence. Campaign 'agent-test'. "
            "Do not send anything — just write it to the outbox.",
        ))
        after = pb.list_by_status("pending", U) + pb.list_by_status("invalid", U)
        check("a batch was queued from the turn", len(after) >= 1, f"({len(after)} queued)")
        if after:
            check("queued batch is valid (pending)", any(b.status == "pending" for b in after))

    finally:
        print("== cleanup ==")
        try:
            clear_batches(pb, U)
            runner.reset(U)
        except Exception:
            pass

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("COLLECTOR OK — next: approval endpoints + send_executor"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
