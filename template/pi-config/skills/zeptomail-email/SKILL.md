---
name: zeptomail-email
description: Use this skill to DRAFT (never send) a personalized, ICP-relevant outreach email to a lead's verified contact — the drafting step of the Apify → Origami → draft pipeline. Sending is out of scope and handled elsewhere, never by this skill.
---

# ZeptoMail Email — draft only

This skill is the **drafting step** of the lead pipeline: once Origami has enriched
a lead's contact with a verified email, this skill composes a personalized,
**ICP-relevant** outreach draft to that contact about the retail-store-hiring
opportunity. It **does NOT send** — no email leaves here. Sending is out of scope
and happens elsewhere (a human / the backend send executor after approval), never
in this skill. The draft is shaped for ZeptoMail so a downstream sender can use it
as-is.

## What this skill does / does not do

- **Does:** build one well-formed, ICP-relevant outreach draft per eligible lead;
  show it for review; revise it on request; save it as the draft artifact.
- **Does NOT:** call any send API, transmit email, or mark anything "sent." Never.

## Field sources (derive, don't ask)

Build the draft from the lead + the outreach intent — never ask for what you
already have:

- `to` → the lead's **enriched, verified** contact email (recipient name = the
  contact's name — the retail-store-hiring decision-maker).
- `from` → `ZEPTOMAIL_FROM_EMAIL` (with a sender name) — the identity the draft is
  written for; this skill does not authenticate or send with it.
- `subject` + body → a personalized, ICP-relevant outreach (see below).

Ask only genuine unknowns (e.g. the sender name / signature, or the offer angle if
it is not implied by the task).

## ICP relevance — the draft must fit what we sell (GOAL.md)

Our product is for companies hiring for their **physical retail stores**, and the
recipient is that company's **retail-store-hiring decision-maker**. Every draft
must read that way — generic outreach is off-target:

- **Anchor on the lead's store-hiring context.** Reference the actual retail
  opening / store-hiring signal that surfaced this lead (the role, the company's
  stores) — concretely, not as a mail-merge blank.
- **Speak to the decision-maker's job.** Frame the value around *their* store
  hiring — filling store-level, customer-facing roles faster / better — not around
  unrelated corporate, tech, or warehouse hiring.
- **Stay on the product's purpose.** The ask connects our retail-store-hiring
  product to their store-hiring need. Do not drift into offers we don't make.
- If a lead is off-ICP (not retail-store hiring, or the contact is not a
  retail-hiring decision-maker), it should not have reached this step — flag it and
  do not draft, rather than writing a generic email.

Judge relevance by the concept ("does this speak to their retail-store hiring?"),
not by matching specific words.

## Eligibility guard (before drafting)

- **Verified** contact email → draft normally.
- **Unverified** email (`emailSource:"jd"` or low-confidence) → still draft, but
  mark the draft `emailStatus:"unverified"` so the downstream sender knows not to
  send without an explicit per-address OK.
- `null` / `unknown` email → **skip** the lead (no one to address); report it.

Never invent a recipient address.

## Draft review + revision loop

The draft is the deliverable — get it right, but never send it.

1. Show the **exact** recipient and the **exact** draft content.
2. If the user asks for any change (wording, subject, tone, angle, recipient),
   apply it, update the draft, and **re-show the exact updated content** — repeat
   until they are satisfied.
3. Finalize the draft (mark it `status:"final"`). Stop there — do not send, and do
   not treat "looks good" as permission to send (this skill has no send step).

## Draft contract (the artifact)

Prepare each draft as, shaped for a downstream ZeptoMail send:

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
  "status": "draft | final"
}
```

- `from` MUST be `ZEPTOMAIL_FROM_EMAIL` — never write a draft from another address.
- Validate that `to`, `from`, `subject`, and a body are present, and that the body
  is ICP-relevant to this lead, before marking the draft `final`.
- Save drafts under `outputs/` (e.g. `outputs/drafts/`) as the pipeline artifact.

## ZeptoMail send shape (reference only — this skill does NOT call it)

For the downstream sender's benefit, a ZeptoMail send expects
`POST https://api.zeptomail.com/v1.1/email` with `from{address,name}`,
`to[].email_address{address,name}`, `subject`, and at least one of `htmlbody` /
`textbody`. The draft's fields map straight onto that. **This skill never makes
that call** — it only produces the draft.

## Boundaries

- **Never send.** No send API call, no transmission, no "sent" status — ever.
- Do not enrich contacts here (Origami) or collect data here (Apify) — this is the
  draft step only.
- Only `ZEPTOMAIL_FROM_EMAIL` as the draft's `from`; never another address.
- Do not draft for a `null` / unknown address, and do not draft off-ICP leads —
  flag them instead.
