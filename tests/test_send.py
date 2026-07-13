"""
Checkpoint H (Phase 4): ZeptoMail send executor. Sends ONE REAL email — to your own
inbox only, via the SEND_OVERRIDE_TO safety net (never a real lead). ~2-5s, no E2B.

Needs in .env: ZEPTOMAIL_API_KEY, ZEPTOMAIL_FROM_EMAIL, SEND_OVERRIDE_TO.
(The lead email below is a dummy; SEND_OVERRIDE_TO redirects it to your inbox.)

After it passes, CHECK YOUR INBOX (the SEND_OVERRIDE_TO address) for the test email.

Run:  .venv/bin/python tests/test_send.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend import send_executor  # noqa: E402

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  -> {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def main() -> int:
    load_dotenv()

    missing = [v for v in ("ZEPTOMAIL_API_KEY", "ZEPTOMAIL_FROM_EMAIL", "SEND_OVERRIDE_TO")
               if not os.environ.get(v)]
    if missing:
        print(f"SKIP: set these in .env first: {', '.join(missing)}")
        return 1

    override = os.environ["SEND_OVERRIDE_TO"]
    print(f"== sending 1 test email (override -> {override}) ==")

    batch = {
        "campaign": "send-test",
        "leads": [{
            "lead_id": "L1",
            "name": "Test Lead",
            "email": "not-a-real-lead@example.com",   # dummy — overridden to your inbox
            "subject": "sales-ai-agent send test",
            "body": "<p>This is a Phase 4 send-executor test. If you got this, ZeptoMail works.</p>",
            "evidence": ["test"],
        }],
    }

    result = send_executor.send(batch)
    print(f"   result: {result}")

    check("override was active (safety net)", result["override"] is True)
    check("1 sent, 0 failed", result["sent"] == 1 and result["failed"] == 0,
          "" if result["failed"] == 0 else f"(errors: {result['errors']})")

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== {len(checks) - len(failed)}/{len(checks)} checks passed =====")
    if not failed:
        print(f"verdict: SEND OK — now CHECK YOUR INBOX at {override}")
    else:
        print(f"verdict: FAILED: {failed}  (check ZeptoMail: verified sender? region .in vs .com? API key?)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
