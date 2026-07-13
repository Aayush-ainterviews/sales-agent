"""
Send executor (Phase 4, Q21): the ONLY place email is sent — via ZeptoMail, from the
Backend, after human approval. The agent has none of this (Q9).

Dev safety net (Q23): if SEND_OVERRIDE_TO is set, EVERY email goes to that one address
regardless of the lead's email — so a real lead can never be emailed by accident during
Phase 4 build. Unset in prod -> real lead emails. All values come from env, never code.
"""

import logging
import os

import httpx

log = logging.getLogger("send_executor")

ZEPTO_URL = os.environ.get("ZEPTOMAIL_API_URL", "https://api.zeptomail.in/v1.1/email")


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"send disabled: missing {name} in environment/.env")
    return v


def send(batch_json: dict) -> dict:
    """Send every lead's drafted email via ZeptoMail. Returns a summary:
    {sent, failed, errors[], override}. Partial failure is tolerated — one bad
    recipient does not stop the rest."""
    raw_key = _require("ZEPTOMAIL_API_KEY").strip()
    # accept the key either as the bare token OR as the full "Zoho-enczapikey <token>"
    # header value — normalize so we never double the prefix (a common 401 cause)
    auth = raw_key if raw_key.lower().startswith("zoho-enczapikey") else f"Zoho-enczapikey {raw_key}"
    from_addr = _require("ZEPTOMAIL_FROM_EMAIL")
    from_name = os.environ.get("ZEPTOMAIL_FROM_NAME", "Sales")
    override = os.environ.get("SEND_OVERRIDE_TO")  # dev safety net (Q23)

    leads = batch_json.get("leads", [])
    sent, failed, errors = 0, 0, []

    with httpx.Client(timeout=30) as client:
        for lead in leads:
            to = override or lead.get("email")
            try:
                r = client.post(
                    ZEPTO_URL,
                    headers={
                        "Authorization": auth,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": {"address": from_addr, "name": from_name},
                        "to": [{"email_address": {"address": to, "name": lead.get("name", "")}}],
                        "subject": lead.get("subject", ""),
                        "htmlbody": lead.get("body", ""),
                        "track_opens": True,
                        "track_clicks": True,
                    },
                )
                if r.status_code in (200, 201):
                    sent += 1
                else:
                    failed += 1
                    errors.append({"lead_id": lead.get("lead_id"), "status": r.status_code,
                                   "body": r.text[:300]})
            except Exception as e:
                failed += 1
                errors.append({"lead_id": lead.get("lead_id"), "error": repr(e)})

    log.info("send done: %d sent, %d failed (override=%s)", sent, failed, bool(override))
    return {"sent": sent, "failed": failed, "errors": errors, "override": bool(override)}
