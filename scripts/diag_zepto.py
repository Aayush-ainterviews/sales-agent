"""
Diagnostic: why is ZeptoMail returning 401? Prints the SHAPE of the key (never the
full value) + the exact request result, so we can tell if the .env value is mangled
(prefix doubled, quotes, whitespace, truncation) vs a genuinely wrong/revoked token.

Run:  .venv/bin/python scripts/diag_zepto.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

key = os.environ.get("ZEPTOMAIL_API_KEY", "")
frm = os.environ.get("ZEPTOMAIL_FROM_EMAIL", "")
to = os.environ.get("SEND_OVERRIDE_TO", "")
url = os.environ.get("ZEPTOMAIL_API_URL", "https://api.zeptomail.in/v1.1/email")

quote_chars = ("'", '"')
wrapped = len(key) >= 2 and key[0] in quote_chars and key[-1] in quote_chars

print("== key shape (value hidden) ==")
print(f"  length:              {len(key)}")
print(f"  starts 'Zoho-encz':  {key.lower().startswith('zoho-enczapikey')}")
print(f"  has leading/trailing space: {key != key.strip()}")
print(f"  has newline:         {chr(10) in key or chr(13) in key}")
print(f"  wrapped in quotes:   {wrapped}")
print(f"  first3..last3:       {key[:3]!r} .. {key[-3:]!r}")
print(f"  FROM email:          {frm}")
print(f"  OVERRIDE to:         {to}")
print(f"  URL:                 {url}")

# build auth exactly as send_executor does
raw = key.strip()
auth = raw if raw.lower().startswith("zoho-enczapikey") else f"Zoho-enczapikey {raw}"
print(f"\n  final Authorization: 'Zoho-enczapikey <token>' form? "
      f"{auth.lower().startswith('zoho-enczapikey ')}")
print(f"  auth header length:  {len(auth)}")

print("\n== exact minimal request (like your working curl) ==")
r = httpx.post(
    url,
    headers={"Authorization": auth, "Accept": "application/json", "Content-Type": "application/json"},
    json={
        "from": {"address": frm},
        "to": [{"email_address": {"address": to, "name": "Test"}}],
        "subject": "Test Email",
        "htmlbody": "<div>Test email sent successfully.</div>",
    },
    timeout=30,
)
print(f"  status: {r.status_code}")
print(f"  body:   {r.text[:400]}")
