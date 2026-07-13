---
name: origami-enrichment
description: Use this skill when the task needs to find or enrich a company or a person — contacts, recruiters, decision-makers, verified emails, LinkedIn — through Origami.
---

# Origami Enrichment

Origami researches and enriches **companies and people** and stores the result in
structured **tables**. For this sales agent, its main job is: take the
companies / leads collected upstream (e.g. from Apify) and, for each, find the
relevant hiring contact / decision-maker with a **verified work email and
LinkedIn** — the things Apify cannot supply. This is Origami's own research, not
the agent browsing the web.

## Setup

- Key: `ORIGAMI_API_KEY` (`og_live_…`) from `.env`.
- Base URL: `https://origami.chat`.
- Auth header: `Authorization: Bearer $ORIGAMI_API_KEY`.
- **Use v2 only.** The `/api/v1/*` API is deprecated — do not use it.

## Two planes — pick by need

| Plane | Path | Use for | Cost |
| --- | --- | --- | --- |
| **Agent** | `/api/v2/agents/*` | Natural-language brief → AI finds leads / enriches rows | **Spends credits** |
| **Data** | `/api/v2/tables/*`, `/api/v2/account/credits`, `/api/v2/batches/*` | Read tables/rows, upsert raw rows, check balance | **Reads are free** |

Rule: a prompt that needs AI to research → agent plane. Reading or moving rows on
a table that already exists → data plane. Never fire an agent just to read a
table.

## Enrichment flow (the sales use case)

You have specific companies / leads (e.g. from Apify) and need a contact +
verified email + LinkedIn for each. First build the enrichment units + query
(see "Building the enrichment input + query" below), then pick a path — **prefer
Path A**.

### Path A — prompt-based (default; small–medium lists)

One agent builds AND enriches in its **own** workspace, in a single run. No
upload, no `focusTableIds` — so the workspace-mismatch trap (below) cannot
happen.

1. `POST /api/v2/agents` with one prompt carrying **one line per unit, each in its
   own mode** (built per "Building the enrichment input + query"), e.g.:
   - *"Find the verified work email and LinkedIn URL for Priya Sharma (Talent
     Acquisition) at Acme (domain acme.com)."* — Mode A
   - *"At Zolostays (domain zolostays.com), find the most relevant hiring contact
     for a Trainer role — recruiter, hiring manager, or decision-maker — then
     their verified work email and LinkedIn URL."* — Mode B

   Returns `202 { agent, run, workspace }`.
2. Poll the run → wait for cells → read rows (below) → merge back into your leads.

### Path B — table-then-enrich (large lists only)

Put the companies in a table first, then enrich. The table and the agent MUST
share ONE workspace (see Workspace alignment).

1. `POST /api/v2/workspaces` → `workspaceId`.
2. Put the companies in THAT workspace: upload CSV
   (`POST /api/v2/workspaces/:workspaceId/uploads`, `mode:"table"`) or upsert
   (`POST /api/v2/tables/:tableId/rows/upsert`, `matchColumns` = input column
   slugs from `GET /api/v2/tables/:tableId/columns`, max 100/req) → `tableId`.
3. **Verify the table is ready BEFORE enriching** — a freshly written table can
   lag a moment, and enriching an empty / half-written table wastes the run. The
   readiness signal depends on how you wrote it:
   - **Upserted rows** → poll `GET /api/v2/batches/:batchId` until
     `status:"complete"`, then confirm the rows.
   - **Uploaded a CSV (new table)** → no batch handle here, so confirm directly:
     poll `GET /api/v2/tables/:tableId/rows` until the expected row count appears,
     and `GET /api/v2/tables/:tableId/columns` shows the input columns.
   Do not start the enrich run until the rows are actually in.
4. Enrich with an agent **bound to that same workspace** plus
   `focusTableIds:[tableId]` and the enrich prompt.
5. Poll → wait for cells → read rows → merge back.

## Building the enrichment input + query

Run this BEFORE choosing a path. Origami needs only TWO things from each lead:
enough to PIN the company, and enough to IDENTIFY the target person. Everything
else is noise.

### Step 0 — Gate: matched rows only
Build the enrichment input only from confirmed-target, normalized rows (Apify
already dropped agencies via the `removeAgency` filter — backed by
`org_linkedin_recruitment_agency_derived` — and off-target rows at the filter
stage). A research / "what is" request → no enrichment.

### Step 1 — Dedupe to enrichment units (cost gate)
Multiple job posts from one company are NOT multiple leads:
- Row has a real `recruiter_name` → unit = **that person**.
- Row has no `recruiter_name` → unit = **the company**.
Enrich each unit **once**. A company's 5 posts = **1 unit**, not 5× credits.

### Step 2 — Pick the MODE per unit
- **Mode A — verify a known person:** the unit has a real recruiter name.
- **Mode B — find a person:** no recruiter, OR the name is generic / a team
  ("Talent Acquisition Team", "HR Department", "Careers") → not a person →
  **guard → Mode B**.

### Step 3 — Seed the input (identifiers only; omit if absent, never fabricate)

| Field | Apify key | Mode | Why |
|---|---|---|---|
| company | `organization` | A+B | base identity |
| domain / website | `org_linkedin_website` | A+B | biggest lever for email accuracy |
| company LinkedIn | `organization_url` | A+B | disambiguation |
| role / title | `title` | B | to pick the *relevant* contact |
| recruiter name + title | `recruiter_name` / `recruiter_title` | **A only** | the known person |
| location, job URL | `locations_derived` / `url` | optional | separate same-name companies |

Pass every company identifier you have, **strongest-first**: `organization_url`
(LinkedIn — most unique) → `org_linkedin_website` (domain) → `organization`
(name — weakest, names collide). If only the name is available → weak identity,
wrong-entity risk → **flag it; do not blindly enrich** (prevents the "which
Apollo?" mismatch).

**Drop-test** — for every other field (`description_text` JD, `date_posted`, the
full lead-record) ask: *does this help pin the company or identify the person?*
No → **drop from the Origami input** (it stays in the output lead-record, never in
the prompt). **But harvest first:** before dropping the JD, pull any contact email
it carries into the lead record (`emailSource: "jd"`, unverified). Dropping from
the prompt ≠ discarding the data — the JD's email still reaches the output via
merge. JD / PII leak into the prompt stays structurally impossible, while any
contact the JD carried is kept.

### Step 4 — Build the query from the MODE
- **Mode A:** `Find the verified work email and LinkedIn URL for {recruiter_name}
  ({recruiter_title}) at {organization} (domain {org_linkedin_website}).`
- **Mode B:** `At {organization} (domain {org_linkedin_website}), find the most
  relevant hiring contact for a {title} role — recruiter, hiring manager, or
  decision-maker — then their verified work email and LinkedIn URL.`

Every query is assembled from 3 parts, each with a fixed source: **SET** (which
companies ← Step 3 identifiers, inline) + **TARGET PERSON** (who ← Mode A known
name / Mode B role descriptor) + **FIELDS** (what ← the GAP = required outputs
minus what Apify already gave, i.e. only verified email + LinkedIn). Prefer Mode A
— cheaper and accurate; use Mode B only when there is no known person.

### Step 5 — Wire into the path
- **Path A** (≤ ~10–15 units): one prompt, one line per unit carrying its own mode
  (named person for A; company + role for B).
- **Path B** (large): upsert units as table rows with input slugs (`company`,
  `domain`, `company_linkedin`, `recruiter_name`, `recruiter_title`, `role`), plus
  ONE generic per-row branch prompt: *"For each row: if a contact name is present,
  find THAT person's verified work email and LinkedIn URL; if no contact name,
  first find the most relevant hiring contact for the given role, then their email
  and LinkedIn."* The branch lives in the row data — the prompt stays constant.

**Form rules (both paths):** 1–4 concise sentences, < ~150 words; concrete with
identifiers inline; **"for EACH of these / enrich-all", never "find N"** (the list
is fixed from Apify — "find N" makes Origami discover new companies); ask only for
the GAP fields; no "return as JSON"; no cost limit in the prompt (bound scope by
the list + role instead).

### Step 6 — Reconcile on merge-back
- **Mode A** → attach email / LinkedIn to the known recruiter (same identity by
  construction).
- **Mode B** → Origami's person becomes the contact.
- **Edge:** a Mode A run returns a different or low-confidence person → KEEP the
  Apify `recruiter_name`, leave email `null` / unverified. Never overwrite a known
  name with an empty or uncertain result.

## Workspace alignment (the #1 trap)

Every agent owns ONE workspace. A `POST /api/v2/agents` that is NOT bound to your
table's workspace gets a NEW one — then `focusTableIds:[tableId]` points at a
table the agent can't see and the call fails `400 WORKSPACE_TABLE_MISMATCH` (the
focusTableIds looks like it "disappeared"). So:

- Path A avoids this entirely — no separate table, no `focusTableIds`.
- For Path B, the table and the agent must be in the **same workspace**: bind the
  agent to your table's workspace (verify the exact param against the live API —
  do NOT guess), or attach the table with `attachments:[{ kind:"table", tableId }]`
  (which also requires same workspace, else `400 INVALID_ATTACHMENT`).
- If `focusTableIds` seems to come back empty, you almost certainly got a
  `400 WORKSPACE_TABLE_MISMATCH` — **read the error code**; do not invent a "sync
  lag" theory.
- Only `focusTableIds` (and `attachments`) exist. There is **no `tableIds`
  field** — never add unknown fields hoping they help; the server ignores or
  rejects them.

## Polling — the run is async (~1–5 min)

`POST` only **admits** the run (`status:"running"`, `response:null`). Poll
`GET /api/v2/agents/:agentId/runs/:runId` until `status !== "running"`.

- **Honor the `Retry-After` header** on each running response (currently ~15s);
  fall back to 15s if missing. Polling faster does NOT finish it sooner — it just
  burns quota. Polling is **free** (a read endpoint).
- A failed poll / network blip does not cancel the run — just retry the same URL.
- **Terminal statuses:** `completed`, `needs_input`, `step_cap_hit`, `incomplete`,
  `cancelled`, `errored`, `timed_out`. Stop polling once you see one.
- **Runaway / stall guard.** Each running response carries `steps:{ completed, max }`.
  Track `steps.completed` and elapsed time across polls. A run normally finishes
  in ~1–5 min. If it runs far longer (e.g. > 10 min) OR `steps.completed` does not
  advance across several consecutive polls (a stall — e.g. a from-scratch search
  stuck at 1 / 30), treat it as a runaway: proactively
  `POST /api/v2/agents/:agentId/cancel`, keep any partial `response.actions[]` /
  `response.tables[]`, then reassess (tighten the brief / shrink the list) rather
  than waiting indefinitely.

## Wait for cells — run-done ≠ data-ready (this is where runs look "stuck")

When a run hits `completed`, the agent stopped *thinking*, but the per-row
enrichment (emails, LinkedIn, etc.) often keeps running in the background for
another 30s–several minutes. Every `response.tables[]` entry (and
`GET /api/v2/tables/:id`) carries `cells:{ running, errored }` at column and table
level.

- If `cells.running > 0` → enrichment is **still working**. Do NOT say "no email
  found". Poll `GET /api/v2/tables/:tableId` every ~10s until `cells.running === 0`.
- If `cells.errored > 0` after it settles → those genuinely failed (no data) —
  report honestly. Distinguish "still loading" (`running`) from "couldn't find"
  (`errored`).

## Reading the run object

- `status` — the single discriminator (above).
- `response.text` — user-facing summary (`null` on `errored` / `timed_out`).
- `response.actions[]` — what changed (`table_created`, `column_added`,
  `leads_added`, …).
- `response.tables[]` — full table objects: `id`, `name`, `leadCount`,
  `columns[]` (each with `cells:{running,errored}`), table-level `cells`, and a
  deep-link `url`. **Always surface the `url`.**
- `todo.pendingQuestions[]` — if `status:"needs_input"`, the agent is asking.

## Non-happy paths (so you never get stuck)

- **needs_input:** surface the question(s) verbatim; answer with
  `POST /api/v2/agents/:agentId/runs` (prompt = the answer). Don't guess.
- **incomplete** — or `completed` with empty `actions[]` on an enrichment task:
  the agent researched but didn't materialize the table. Follow up on the **same
  agent**: `POST /api/v2/agents/:agentId/runs` "build the table from what you just
  researched." Don't start a fresh agent — you'd pay twice.
- **AGENT_BUSY (409):** a run is already in flight on that agent — wait or
  `POST /api/v2/agents/:agentId/cancel`.
- **INSUFFICIENT_CREDITS (402):** surface `creditsRequired` / `creditsAvailable`
  and stop; do not retry.
- Save raw run + table responses under `runs/<runId>/raw/origami-*.json`.

## Input / output schema

**Input — what you write into the table.** Only `kind:"input"` columns are
writable (enrichment / score columns populate automatically). Confirm the real
slugs with `GET /api/v2/tables/:id/columns` — never use display names. Omit any
field you don't have (never fabricate):

| Canonical | Slug (example) | Comes from (Apify) |
|---|---|---|
| company | `company` | `organization` |
| domain | `domain` | `org_linkedin_website` |
| company LinkedIn | `company_linkedin` | `organization_url` |
| recruiter name | `recruiter_name` | `recruiter_name` |
| recruiter title | `recruiter_title` | `recruiter_title` |
| role | `role` | `title` |

**Output — what you read back** (`GET /api/v2/tables/:id/rows`): typed cells keyed
by column slug — `{type:"scalar",value}` (input), `{type:"value",value,run?}`
(enrichment; `run` carries status / error), `{type:"sequence",…}`. Use
`?cells=flat` for `{ slug: value }`, `?format=csv` to export.

Extract each enriched lead into a stable object (missing → `null` / `unknown`;
keep confidence / provenance):

```json
{
  "name": null,
  "companyName": null,
  "role": null,
  "email": null,
  "emailSource": "origami",
  "linkedinUrl": null,
  "website": null,
  "confidence": null,
  "provenance": []
}
```

## Enrichment rules (sales)

- Enrich only entities that match the confirmed target; not different / uncertain
  ones unless the user confirms.
- **Merge, never overwrite.** Origami fills the email / LinkedIn that Apify can't;
  an empty Origami result must never blank a recruiter name / title already on the
  Apify post or in the JD text.
- **Verified vs unverified.** An email / contact is "verified" only when Origami
  returns it with supporting confidence / provenance. Never present low-confidence
  enrichment as verified. An email harvested from a JD or job post is an
  **unverified candidate** (`emailSource: "jd"`) — keep it, optionally have Origami
  verify it, and never discard it; if Origami finds nothing better, the JD email
  stays (marked unverified).
- Do not invent data; missing values → `null` / `unknown`.
- **Fallback to Apollo.** A contact / company field still missing or unverified
  after Origami → hand it to Apollo (the fallback enrichment rung; see the
  apollo-enrichment skill), for those fields only. Never overwrite a verified
  value.

## Enrichment waterfall (per-field precedence)

For each field, fill from sources in order; stop at the first acceptable
(verified) value; call a later source ONLY for fields still missing:

- contact email:                   Origami → JD-harvested → Apollo
- contact name / title / LinkedIn: Apify on-post → Origami → Apollo
- contact phone:                   Apollo (async) — **deferred** (no webhook infra yet)
- company fields:                  Apify `org_*` → Origami → Apollo org-enrich

Never overwrite a verified value with a guessed one. Keep source + status per
field so the merge stays traceable.

## Cost

Agent runs spend credits; data reads/upserts of an existing table do not (a paid
key is still required). Check `GET /api/v2/account/credits` before large runs.

## Boundaries

- v2 only; never call `/api/v1/*`.
- Do not collect source data here (use Apify); do not send email here (use
  ZeptoMail).
- Never fabricate `agentId` / `runId` / `tableId` — use only ids seen in prior
  responses, else `GET /api/v2/tables`.
- Public professional data only; no private / hidden contact data.
