---
name: origami-enrichment
description: Use this skill when the task needs to find or enrich a company or a person — contacts, recruiters, decision-makers, verified emails, LinkedIn — through Origami.
---

# Origami Enrichment

Origami researches and enriches **companies and people** and stores the result in
structured **tables**. For this sales agent, its main job is: take the
companies / leads collected upstream (e.g. from Apify) and, for each, find the
**retail-store-hiring decision-maker** (the Sales Head, Store / Retail Head, or
Recruiter / TA Head kind — the person who owns or influences store hiring; see the
ICP in GOAL.md) with a **verified work email and LinkedIn** — the things Apify
cannot supply. This is Origami's own research, not the agent browsing the web.
Never settle for a random employee or an unrelated recruiter; the target is that
retail-hiring decision-maker, and the email is the critical field. The person must
**currently hold** that title **at that same company** — past / former roles, or
the right title at a different company, do not count.

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
(see "Building the enrichment input + query" below), then run the single
table-creation flow.

### One job = one agent = one workspace = one table (never split)

A single enrichment job uses **exactly ONE Origami agent, in ONE workspace, with
ONE table** — every unit of the job goes into that one table in that one run.

- **Never fire a second `POST /api/v2/agents` for the same job.** Each unbound
  `POST /api/v2/agents` spins up a NEW workspace + NEW table (see Workspace
  alignment) — so a "backup" or "second batch" call fragments one job across two
  workspaces. That is a bug, not a strategy.
- **No top-up runs.** If some rows enrich empty / low-confidence, that is the
  result for those rows — do NOT go collect more companies and enrich them in a
  separate run to "make up the count". Reaching the count is the collection
  stage's job (with a buffer; see the apify skill), done BEFORE enrichment. You
  enrich the full set **once**.
- **Gap-fill, if ever needed, is a follow-up run on the SAME agent** — reuse the
  captured table id with `focusTableIds:["<table_id>"]` (same agent → same
  workspace, no mismatch). Never a fresh agent, never a new workspace.

If the delivered complete-lead count still falls short after the single run,
deliver what completed and report honestly — never open a second workspace to top
it up.

### The flow — one table-creation run

One agent builds AND enriches in its **own** workspace, in a single run. No
separate upload, no `focusTableIds` on this first run — so the workspace-mismatch
trap (below) cannot happen.

**Do NOT send loose search lines.** A bare *"find X's email"* prompt makes Origami
answer in chat and **never materialize a table** — then there are no cells to poll
and no rows to read back (this is the "run looks done but nothing came out" bug).
Instead send ONE **table-creation prompt** (built per Step 5A) that tells Origami
to create a single structured table — one row per provided unit, fixed input
columns left untouched, missing enrichment columns filled.

1. `POST /api/v2/agents` with the Step 5A table-creation prompt. Returns
   `202 { agent, run, workspace }`.
2. Poll the run → **wait for cells** (`cells.running === 0`, see below) → read the
   table rows → merge back into your leads.
3. **Capture the created table id** from `run.response.tables[].id` — you need it
   for any follow-up enrich pass (Step 5A operational note).

**Very-large lists (escape hatch).** One prompt only holds so many unit blocks
reliably. If the list is too big for one prompt (~100+ units), do NOT switch
tools — **chunk** the units into batches of ~50 and run one table-creation prompt
per chunk (same shape), then merge the tables' rows back. Only if a list is
genuinely huge AND you already hold the rows as structured data, you may instead
pre-`upsert` them into a table in ONE workspace
(`POST /api/v2/tables/:tableId/rows/upsert`, `matchColumns` = input slugs, max
100/req; verify rows landed before enriching) and enrich an agent bound to that
same workspace with `focusTableIds:[tableId]`. That reintroduces the
workspace-alignment trap (below), so use it only when the scale truly demands it.

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
  (`search_mode = known_person` in the Step 5A table.)
- **Mode B — find a person:** no recruiter, OR the name is generic / a team
  ("Talent Acquisition Team", "HR Department", "Careers") → not a person →
  **guard → Mode B**. (`search_mode = find_decision_maker` in the Step 5A table —
  the target is the retail-store-hiring decision-maker, per the ICP.)

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

### Step 4 — Write each unit's per-row Task line (from its MODE)
This is NOT a standalone query you send — it is the **`Task` line for that unit's
row block** inside the Step 5A table-creation prompt. Write it from the unit's mode:
- **Mode A** (`known_person`): `Verify {recruiter_name} ({recruiter_title}) at
  {organization} (domain {org_linkedin_website}) and return their verified work
  email and LinkedIn URL.`
- **Mode B** (`find_decision_maker`): `At {organization} (domain
  {org_linkedin_website}), find the person who CURRENTLY holds the
  retail-store-hiring decision-maker role there — the Sales Head, Store / Retail
  Head, or Recruiter / TA Head who owns store hiring for a {title} role (or the
  closest equivalent), in their present position at this company, not a past role —
  then their verified work email and LinkedIn URL.`

Each Task line has 3 fixed parts: **SET** (which company ← Step 3 identifiers,
inline) + **TARGET PERSON** (who ← Mode A known name / Mode B the current
retail-store-hiring decision-maker for the role, per the ICP — never a random
employee) + **FIELDS** (what ← the GAP = required outputs minus what Apify already
gave, i.e. only verified email + LinkedIn). Prefer Mode A — cheaper and accurate;
use Mode B only when there is no known person.

### Step 5 — Wire into the table-creation prompt
Feed every unit into ONE Step 5A table-creation prompt — one row block per unit,
each carrying its own `search_mode` (known person → `known_person`; company + role
→ `find_decision_maker`). For a list too big for one prompt, chunk into ~50-unit
batches (escape hatch above); the row-block shape stays identical. For the rare
pre-upsert escape hatch, the input column slugs are `company`, `domain`,
`company_linkedin`, `recruiter_name`, `recruiter_title`, `role`.

**Form rules (the table-creation prompt):** concrete with identifiers inline;
**"for EACH of these / enrich-all", never "find N"** (the list is fixed from
Apify — "find N" makes Origami discover new companies); ask only for the GAP
fields; no "return as JSON"; no cost limit in the prompt (bound scope by the list
+ role instead). The prompt is a structured, multi-part block (table intent,
columns, per-mode behavior, row blocks) — not a one-liner; its length simply
follows from the row count.

### Step 5A — the table-creation prompt (build the table, don't just chat)

The enrichment prompt is a **table-creation brief**, not loose search lines. It
must make Origami create one structured table from the fixed input units, then
fill the missing enrichment columns — every unit stays a row so the result has
cells to poll and rows to read back. Everything the ICP (GOAL.md) targets:
retail-store-hiring decision-makers with a verified email.

The prompt must include, in order:

1. **Table intent** — create ONE new table named `{table_name}`; exactly one row
   per provided unit; do NOT add unrelated companies or extra rows, and do NOT go
   discover new companies (the list is fixed).
2. **Fixed-input rule** — the company / domain / person / role fields provided are
   fixed inputs; do not overwrite or change them.
3. **Column schema** — tell Origami to create these columns:
   - *Input columns* (I fill; keep untouched): `company_name`, `domain`,
     `search_mode`, `provided_person_name`, `provided_person_title`, `target_role`.
   - *Enrichment columns* (Origami fills): `decision_maker_name`,
     `decision_maker_title`, `verified_work_email`, `email_verification_status`,
     `linkedin_url`, `confidence`, `source_provenance`, `notes`.
4. **Per-mode behavior** (keyed by the row's `search_mode`, ICP-scoped):
   - `known_person` → verify THAT exact person and find their verified work email
     + LinkedIn URL.
   - `find_decision_maker` → find the **retail-store-hiring decision-maker** for
     `target_role` — the person who owns physical-store hiring (Sales Head, Store /
     Retail Head, Recruiter / TA Head, HR Head, or the closest equivalent), never a
     random employee or an unrelated / tech recruiter — then their verified work
     email + LinkedIn URL. The person must **currently hold** that title **at this
     same company**; past / former roles, or the title at another company, do not
     count.
5. **Safety** — do not invent emails or LinkedIn URLs; if a verified email can't be
   found, leave `verified_work_email` empty/null and say why in `notes`; keep
   source / provenance for every found detail; mark `email_verification_status`
   honestly (verified vs unverified).
6. **Rows** — append each enrichment unit as a structured row block: Company,
   Domain, Search mode, Provided person + title (if `known_person`), Target role
   (if `find_decision_maker`), Task.

`{table_name}` = a short run-specific name (e.g. the target + run id). Confirm the
real column slugs with `GET /api/v2/tables/:id/columns` after creation before
reading cells — display names ≠ slugs.

**Assembled example** (two units — one per mode; follow this exact shape):

```
Create ONE new Origami table named "retail-leads-<runId>".
Use exactly one row per unit I list below. Do NOT add any other companies or
extra rows, and do NOT discover new companies — the list is fixed.

The values I give (company_name, domain, search_mode, provided_person_name,
provided_person_title, target_role) are FIXED inputs — do not overwrite or change them.

Create the table with these columns:
- Input columns (I fill; leave untouched): company_name, domain, search_mode,
  provided_person_name, provided_person_title, target_role
- Enrichment columns (you fill): decision_maker_name, decision_maker_title,
  verified_work_email, email_verification_status, linkedin_url, confidence,
  source_provenance, notes

For EACH row, by its search_mode:
- known_person → verify THAT exact person (provided_person_name/_title) at that
  company; return their verified work email + LinkedIn URL.
- find_decision_maker → find the person who CURRENTLY holds the retail-store-hiring
  decision-maker role for target_role at that same company — Sales Head,
  Store/Retail Head, Recruiter/TA Head, HR Head, or closest equivalent who owns
  physical-store hiring (present position, not a past role, not a different
  company; never a random employee or a tech recruiter) — then their verified work
  email + LinkedIn URL.

Do not invent emails or LinkedIn URLs. If a verified email can't be found, leave
verified_work_email empty and say why in notes. Keep source/provenance for every
found detail; mark email_verification_status honestly.

Rows:
1) Company: Acme Retail | Domain: acme.com | Search mode: known_person |
   Provided person: Priya Sharma (Talent Acquisition) | Task: verify this person, get email + LinkedIn.
2) Company: Zolostays | Domain: zolostays.com | Search mode: find_decision_maker |
   Target role: Store Manager | Task: find the current retail-store-hiring decision-maker, get email + LinkedIn.
```

**Operational note — table id & follow-ups:**

```
After the run completes, get the created table id from
`run.response.tables[].id`.
Do NOT use `focusTableIds` on the first run — the table does not exist yet.
For any follow-up run on that created table, call `POST /api/v2/agents/:agentId/runs`
with `focusTableIds: ["<created_table_id>"]` (same agent, so same workspace — no
WORKSPACE_TABLE_MISMATCH).
```

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

- The default table-creation flow avoids this entirely — the agent makes the
  table in its OWN workspace, no separate table, no `focusTableIds` on the first
  run. A follow-up run on that table is on the SAME agent, so still same workspace.
- Only the rare pre-upsert escape hatch can hit it: there the table and the agent
  must be in the **same workspace** — bind the agent to your table's workspace
  (verify the exact param against the live API — do NOT guess), or attach the table
  with `attachments:[{ kind:"table", tableId }]` (also requires same workspace,
  else `400 INVALID_ATTACHMENT`).
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
- Do not collect source data here (use Apify); do not draft email here (use the
  zeptomail-email skill, which drafts only and never sends).
- Never fabricate `agentId` / `runId` / `tableId` — use only ids seen in prior
  responses, else `GET /api/v2/tables`.
- Public professional data only; no private / hidden contact data.
