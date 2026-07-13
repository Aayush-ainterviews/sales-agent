---
name: zeptomail-email
description: Use this skill to draft and (after explicit approval) send outreach email to a lead's contact through ZeptoMail — the send step of the Apify → Origami → ZeptoMail pipeline.
---

# ZeptoMail Email

ZeptoMail is the **send step** of the lead pipeline: once Origami has enriched a
lead's contact with a verified email, this skill drafts a personalized outreach
to that contact about the opportunity, shows it for approval, and sends it.

## Setup

- Send-mail token: `ZEPTOMAIL_API_KEY` from `.env`.
- Verified sender: `ZEPTOMAIL_FROM_EMAIL` — the ONLY allowed `from.address`.
- Endpoint: `POST https://api.zeptomail.com/v1.1/email`
- Headers: `Authorization: zoho-enczapikey $ZEPTOMAIL_API_KEY`,
  `Content-Type: application/json`.

## Where the fields come from (derive, don't ask)

Build the email from the lead + the user's outreach intent — never ask for what
you already have:

- `to` → the lead's **enriched, verified** contact email (recipient name = the
  contact's name).
- `from` → `ZEPTOMAIL_FROM_EMAIL` (with a sender name).
- `subject` + body → a personalized outreach for that contact about the
  opportunity (company / role context from the lead).

Ask only genuine unknowns (e.g. the sender name / signature, or the offer angle
if it is not implied by the task).

## Verified-email guard (before any send)

- **Verified** email → may send (after approval).
- **Unverified** email (`emailSource:"jd"` or low-confidence) → flag it; send only
  if the user explicitly OKs that specific unverified address.
- `null` / `unknown` email → **skip** the lead (cannot send); report it.

Never send to an address you would not stand behind — bounces hurt sender
reputation.

## Send approval — HARD gate (every send)

Never send without explicit user approval. Before any send:

1. Show the **exact** recipient(s) and the **exact** final content.
2. If the user asks for any change (wording, subject, tone, recipient, etc.),
   apply it, update the draft, and **re-show the exact updated content** — repeat
   until they approve. Only ever send the version they approved.
3. Get an explicit "yes, send to these".

Approval is per content + per recipient — a yes for one draft is NOT a yes for a
different recipient or edited content. Sending is irreversible.

## Send API

`POST https://api.zeptomail.com/v1.1/email`:

```json
{
  "from": { "address": "<ZEPTOMAIL_FROM_EMAIL>", "name": "<sender name>" },
  "to": [ { "email_address": { "address": "<contact email>", "name": "<contact name>" } } ],
  "subject": "<subject>",
  "htmlbody": "<html>",
  "textbody": "<plain text>",
  "reply_to": [ { "address": "<reply email>", "name": "<name>" } ],
  "client_reference": "<lead id>",
  "track_opens": true,
  "track_clicks": true
}
```

- Required: `from{address,name}`, `to[].email_address{address,name}`, `subject`,
  and at least one of `htmlbody` / `textbody`. Everything else is optional.
- `from.address` MUST equal `ZEPTOMAIL_FROM_EMAIL` (the verified sender) — never
  spoof another address.
- Send one recipient per request for 1:1 outreach. `client_reference` carries the
  lead id so the send is traceable.
- Response: success → `{ data[], message, request_id, object:"email" }` — save
  `request_id` as the send-log `providerMessageId`. Failure →
  `{ error:{ code, message, details[], request_id } }`; **4xx** = fix the request
  (e.g. unverified sender, bad address), **5xx** = ZeptoMail-side, retry later.
- Never mark an email sent unless you received a 2xx.

## Draft contract

Prepare each draft as:

```json
{
  "to": null,
  "recipientName": null,
  "from": null,
  "subject": null,
  "textBody": null,
  "htmlBody": null,
  "replyTo": null,
  "context": null,
  "sourceLeadId": null,
  "emailStatus": "verified | unverified",
  "approvalStatus": "draft"
}
```

Validate that `to`, `from`, `subject`, and a body are present before any send.

## Send log

After an approved, successful send, log:

```json
{
  "provider": "zeptomail",
  "to": null,
  "from": null,
  "subject": null,
  "sentAt": null,
  "providerMessageId": null,
  "status": null,
  "error": null,
  "relatedRunId": null,
  "relatedContactId": null
}
```

`providerMessageId` = the response `request_id`.

## Boundaries

- Do not enrich contacts here (Origami) or collect data here (Apify) — this is the
  send step only.
- Never send without the approval gate above.
- Only `ZEPTOMAIL_FROM_EMAIL` as sender; never spoof another from-address.
- Never send to an unverified / `null` address without an explicit per-address OK.
