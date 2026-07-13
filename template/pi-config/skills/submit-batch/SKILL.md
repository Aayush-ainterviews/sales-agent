---
name: submit-batch
description: Use this when outreach emails are drafted and ready to be sent. You do
  NOT send email yourself — you queue a batch for human approval by writing it to the
  outbox. Trigger whenever the user wants to send / email / reach out to leads, or
  when a lead-gen pipeline has produced contacts with drafted messages.
---

# Submitting an outreach batch for approval

You cannot send email. To get outreach sent, write the batch as a single JSON file to
the **outbox**; the backend collects it, a human approves it, and the system sends it.

## Steps

1. Make sure every lead has a real `email`, a `subject`, and a `body` (the drafted message).
2. Pick a short unique `batch_id` (e.g. `retail-2026-07-13-a`).
3. Write the batch to `/home/user/outbox/<batch_id>.json` in EXACTLY this shape:

```json
{
  "campaign": "short label for this batch",
  "leads": [
    {
      "lead_id": "L1",
      "email": "person@company.com",
      "subject": "the email subject line",
      "body": "the full drafted email body (HTML or plain text)",
      "evidence": ["why this lead — the signal you found, e.g. 'hiring 3 SDRs'"]
    }
  ]
}
```

4. After writing the file, tell the user: `batch <batch_id> is queued for approval (<N> leads)`.

## Rules

- Every lead MUST have `email`, `subject`, `body`. No placeholders, no invented emails —
  use only emails you actually found/enriched. A lead without a real email does not go in the batch.
- Put your justification in `evidence` (why this person is a good target). This is what the
  human reviews before approving.
- One file per batch. Valid JSON only (no trailing commas, double-quoted keys/strings).
- Do NOT try to send, and do NOT ask for send credentials — sending is not your job.
