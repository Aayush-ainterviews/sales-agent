"""
Checkpoint A (Postgres migration): config + db + registry against local Postgres.
Touches no cloud E2B — runs in ~1-2s. Needs the Docker Postgres up + DATABASE_URL set.

Proves the registry does exactly what the rest relies on:
- unknown user -> None (so the caller creates a sandbox)
- upsert then get -> the row comes back
- upsert again (self-healing after a dead sandbox) -> new sandbox_id replaces the old
- users are independent; delete forgets the mapping (reset path)
- save_log / get_log round-trips the full session JSONL (the backup, Postgres-only)
- get() never returns the big log blob
- secrets_for_user carries the 4 skill tokens and never a send credential (Q9)

Run:  .venv/bin/python tests/test_registry.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import db  # noqa: E402
from backend.config import TEMPLATE_VERSION, secrets_for_user  # noqa: E402
from backend.registry import Registry  # noqa: E402

U1, U2 = "test_reg_rohan", "test_reg_priya"
checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def main() -> int:
    reg = Registry(db.pool())
    reg.delete(U1)
    reg.delete(U2)  # idempotent start (shared DB)

    print("== registry ==")
    check("unknown user -> None", reg.get(U1) is None)

    row = reg.upsert(U1, "sbx_aaa", TEMPLATE_VERSION)
    check("upsert returns row", row.sandbox_id == "sbx_aaa" and row.status == "active")
    check("get after upsert", reg.get(U1).sandbox_id == "sbx_aaa")

    # self-healing path: old sandbox died, SandboxManager created a new one
    reg.upsert(U1, "sbx_bbb", TEMPLATE_VERSION)
    check("upsert replaces dead sandbox", reg.get(U1).sandbox_id == "sbx_bbb")

    reg.upsert(U2, "sbx_ccc", TEMPLATE_VERSION)
    check("users are independent", reg.get(U1).sandbox_id == "sbx_bbb"
          and reg.get(U2).sandbox_id == "sbx_ccc")

    reg.touch(U2)  # must not error
    check("touch ok", reg.get(U2) is not None)

    print("== log round-trip (the backup) ==")
    sample = '{"type":"session"}\n{"type":"message"}\n'
    reg.save_log(U1, sample)
    check("get_log returns saved log", reg.get_log(U1) == sample)
    check("get() has NO log field", not hasattr(reg.get(U1), "log"))

    reg.delete(U1)  # reset path
    check("delete forgets mapping", reg.get(U1) is None and reg.get(U2) is not None)

    print("== secrets (Q9/Q11/Q12) ==")
    s = secrets_for_user(U1)
    for name in ("GEMINI_API_KEY", "ORIGAMI_API_KEY", "APOLLO_API_KEY", "APIFY_TOKEN"):
        check(f"carries {name}", name in s)
    check("NO send credential (Q9)", not any("ZEPTO" in k or "SMTP" in k for k in s))

    reg.delete(U2)  # cleanup

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("REGISTRY OK (Postgres) — next: session_backup + turn_runner"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
