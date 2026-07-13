"""
Checkpoint E (Phase 4): the pending_batches approval queue. No E2B — runs in ~1-2s.
Needs the Docker Postgres up + DATABASE_URL set.

Proves the queue does what the approval flow relies on:
- insert a batch (pending) -> get it back with batch_json intact (jsonb round-trip)
- list_by_status('pending') sees it; other statuses don't
- approve (set_status) moves it; rejected/sent filters work
- set_result records the send summary + final status
- delete removes it

Run:  .venv/bin/python tests/test_batches.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import db  # noqa: E402
from backend.batches import PendingBatches  # noqa: E402

U = "test_batch_user"
checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def main() -> int:
    pb = PendingBatches(db.pool())

    # idempotent start: clear any leftover rows for the test user
    for b in pb.list_by_status("pending", U) + pb.list_by_status("sent", U) + pb.list_by_status("approved", U):
        pb.delete(b.id)

    batch = {
        "campaign": "retail leads week 1",
        "leads": [
            {"lead_id": "L1", "email": "a@x.com", "subject": "hi", "body": "hello", "evidence": ["hiring"]},
            {"lead_id": "L2", "email": "b@y.com", "subject": "hi", "body": "hello", "evidence": ["news"]},
        ],
    }

    print("== queue ==")
    bid = pb.insert(U, batch)
    check("insert returns id", bool(bid))

    got = pb.get(bid)
    check("get round-trips batch_json", got is not None
          and got.batch_json["campaign"] == "retail leads week 1"
          and len(got.batch_json["leads"]) == 2)
    check("starts pending", got.status == "pending")

    pend = pb.list_by_status("pending", U)
    check("list_by_status sees it", any(b.id == bid for b in pend))
    check("not in 'approved' yet", not any(b.id == bid for b in pb.list_by_status("approved", U)))

    print("== approve + send outcome ==")
    pb.set_status(bid, "approved")
    check("moved to approved", pb.get(bid).status == "approved")

    pb.set_result(bid, "sent", {"sent": 2, "failed": 0, "errors": []})
    final = pb.get(bid)
    check("result recorded", final.status == "sent" and final.result["sent"] == 2)

    print("== reject filter ==")
    bid2 = pb.insert(U, {"campaign": "x", "leads": []})
    pb.set_status(bid2, "rejected")
    check("rejected filter works", any(b.id == bid2 for b in pb.list_by_status("rejected", U)))

    pb.delete(bid)
    pb.delete(bid2)
    check("delete removes", pb.get(bid) is None and pb.get(bid2) is None)

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("BATCHES OK — next: submit-batch skill + template v2"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
