"""
Phase 2, step 2 test: SandboxManager against real E2B. Creates real sandboxes (~30-60s).

Proves the one self-healing path (Q4):
1. first call for a user CREATES a sandbox + writes the registry row
2. second call RESUMES the same sandbox (same id back) — not a new one
3. metadata carries user_id (debug label, Q3)
4. simulate a dead sandbox (kill it out from under the manager) -> next call
   silently RECREATES with a new id and updates the row — no crash, no new branch
5. reset() kills + forgets; the next call provisions fresh

Cleans up every sandbox it makes.
Run:  .venv/bin/python tests/test_sandbox_manager.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402
from e2b import Sandbox  # noqa: E402

from backend import db  # noqa: E402
from backend.config import assert_secrets_present  # noqa: E402
from backend.registry import Registry  # noqa: E402
from backend.sandbox_manager import SandboxManager  # noqa: E402

checks: list[tuple[str, bool]] = []
made: set[str] = set()


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def main() -> int:
    load_dotenv()
    assert_secrets_present()

    mgr = SandboxManager(Registry(db.pool()))
    uid = "test_rohan"
    mgr.registry.delete(uid)  # idempotent start (shared Postgres)

    try:
        print("== 1. first call creates ==")
        sbx1 = mgr.get_or_create(uid)
        made.add(sbx1.sandbox_id)
        id1 = sbx1.sandbox_id
        check("created + row written", bool(id1) and mgr.registry.get(uid).sandbox_id == id1)

        print("== 2. second call resumes same sandbox ==")
        sbx2 = mgr.get_or_create(uid)
        made.add(sbx2.sandbox_id)
        check("same sandbox id back", sbx2.sandbox_id == id1)

        print("== 3. metadata carries user_id ==")
        info = sbx2.get_info()
        meta = getattr(info, "metadata", {}) or {}
        check("metadata user_id", meta.get("user_id") == uid, str(meta))

        print("== 4. dead sandbox -> silent recreate ==")
        Sandbox.connect(id1).kill()  # kill it behind the manager's back
        time.sleep(2)
        sbx3 = mgr.get_or_create(uid)
        made.add(sbx3.sandbox_id)
        check("recreated with new id", sbx3.sandbox_id != id1)
        check("row updated to new id", mgr.registry.get(uid).sandbox_id == sbx3.sandbox_id)

        print("== 5. reset forgets ==")
        mgr.reset(uid)
        check("row deleted after reset", mgr.registry.get(uid) is None)

    finally:
        print("== cleanup ==")
        for sid in made:
            try:
                Sandbox.connect(sid).kill()
            except Exception:
                pass
        mgr.registry.delete(uid)

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    print("verdict: " + ("SANDBOX MANAGER OK — next: daemon_client"
                         if not failed else f"FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
