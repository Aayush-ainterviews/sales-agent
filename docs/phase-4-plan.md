# Phase 4 — Outreach path (v1, slimmed)

> Closes the loop: the agent can already research + draft; Phase 4 lets a human approve
> and the Backend send. Decisions: architecture-decisions.md (Q9, Q10, Q10b, Q21–Q23).
> Vocabulary: ../CONTEXT.md (Draft Batch, Send, Approval).

## Scope decisions (what's IN vs deferred)

| | v1 (now) | Deferred |
|---|---|---|
| Batch submission | **skill** `submit-batch` (markdown) — agent writes JSON to outbox | TS `submit_batch` extension with in-turn TypeBox validation → with evals/self-correction (Q10) |
| Storage | **one `pending_batches` table** (approval queue) | per-lead / send-status tracking tables (Q10b) |
| Send | **ZeptoMail** from Backend (Q21) | — |
| Test target | operator's own inbox (Q23) | — |

## The loop

```
Agent (sandbox)  research → drafts → (skill) write /home/user/outbox/<id>.json → "batch <id> ready"
Backend (turn-end) collect outbox → validate JSON → pending_batches (status=pending)
Human            GET /batches?status=pending → review → POST /batches/<id>/approve
Backend          send_executor: batch_json → per-recipient ZeptoMail POST → status=sent + result
Backend          feedback turn: run_turn(user, "batch <id>: N sent, M failed …")
```

## Schema (one table)

```sql
CREATE TABLE IF NOT EXISTS pending_batches (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     text NOT NULL,
    batch_json  jsonb NOT NULL,     -- the whole batch (campaign + leads[]), one value
    status      text NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | sent | invalid
    result      jsonb,             -- send summary once sent: {sent, failed, errors[]}
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
```
No per-lead rows — `batch_json` holds the leads, `result` holds the send outcome. Per-lead
tracking is Q10b (future).

## Files (new)

```
template/pi-config/skills/submit-batch/SKILL.md   ← NEW skill → template rebuild v2
backend/
├── batches.py         pending_batches DB access (insert / list / get / set_status / set_result)
├── batch_collector.py turn-end: read sandbox outbox → validate → insert
├── send_executor.py   ZeptoMail send (ZEPTOMAIL_* creds live ONLY here)
└── app.py             + approval endpoints
db.py                  + pending_batches schema
```

## submit-batch skill (in sandbox, markdown)

Instructs the agent: when drafts are ready, write the batch as JSON to
`/home/user/outbox/<batch_id>.json` in a fixed shape:
```json
{ "campaign": "...", "leads": [ {"lead_id","email","subject","body","evidence":[...]} ] }
```
then tell the user "batch <id> ready for approval". (Weakness vs the extension: a malformed
JSON is caught post-turn by the collector, not in-turn — accepted for v1, Q10.)

## Backend pieces

- **batch_collector** (turn-end, after save_log): list `/home/user/outbox/*.json`, read each,
  `json.loads` + shape-check (campaign + non-empty leads, each with email/subject/body).
  Valid → `pending_batches` (status=pending) + delete the outbox file (so it isn't re-collected).
  Invalid → status=invalid (+ optionally a feedback turn asking the agent to fix — the Q10 weakness).
- **approval endpoints** (auth-scoped):
  `GET /batches?status=pending`, `GET /batches/{id}`, `POST /batches/{id}/approve`, `.../reject`.
- **send_executor** (on approve): read `batch_json` → for each lead: `POST api.zeptomail.in/v1.1/email`
  with `Authorization: Zoho-enczapikey <ZEPTOMAIL_API_KEY>`, from = `ZEPTOMAIL_FROM_EMAIL`,
  `htmlbody` = the drafted body, `track_opens/clicks: true`. Collect per-recipient outcomes →
  `status=sent`, `result={sent, failed, errors}`. Partial failure tolerated (one bounce ≠ abort).
  - **Dev safety net — `SEND_OVERRIDE_TO`:** `to = os.environ.get("SEND_OVERRIDE_TO") or lead["email"]`.
    When set (dev), EVERY email goes to that one address regardless of the lead's email, so a real
    lead can never be emailed by accident during Phase 4 build (aligns with the no-accidental-send goal).
    Unset in prod → real lead emails. The value lives in `.env` (operator-controlled), never in code.
- **feedback turn**: `run_turn(user, "batch <id>: N sent, M failed: …")` so the agent can plan follow-ups.

## Egress / secrets (Q9, Q21 intact)

- ZeptoMail is called from the **Backend**, not the sandbox → the sandbox egress allowlist stays
  Gemini + Origami + Apify (Q13). The agent has no ZeptoMail key, no send tool, no route.
- `ZEPTOMAIL_API_KEY` + `ZEPTOMAIL_FROM_EMAIL` in Backend env only; never in `secrets_for_user`.

## Build order + checkpoints

```
1. db.py: pending_batches schema + backend/batches.py
   └─ Checkpoint E: batches CRUD test (no E2B, ~1-2s)
2. submit-batch SKILL.md + template rebuild → sales-agent-v2
   └─ Checkpoint F: a turn writes a valid batch JSON to the outbox
3. batch_collector: turn-end → pending_batches
   └─ Checkpoint G: after a drafting turn, batch is in the DB (status=pending)
4. approval endpoints (app.py)
5. send_executor (ZeptoMail) → send to the operator's own inbox
   └─ Checkpoint H: approve → a real email lands in the test inbox
6. feedback turn
   └─ Checkpoint I: end-to-end — draft → approve → send → agent receives the result
```

## Prereqs before build

- `.env` needs `ZEPTOMAIL_API_KEY` + `ZEPTOMAIL_FROM_EMAIL` (both already in .env.example).
- Confirm the ZeptoMail account/domain is set up for sending (verified sender), else sends 4xx.
