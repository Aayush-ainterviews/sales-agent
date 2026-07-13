---
name: apify
description: Use this skill when the task needs Apify actors, scrapers, datasets, or source collection workflows.
---

# Apify Platform

Use this skill whenever the task needs an Apify actor, scraper, dataset, or web
automation workflow. Apify is a source collection capability.

Current scope: this skill provides ONE source — LinkedIn job postings (the actor
in the Actor Index). It cannot pull from other job boards (Naukri, Indeed,
Apollo), company careers pages, or the open web. If the user asks for a source
not in the Actor Index, tell them it is out of scope — do not substitute the
LinkedIn actor.

Apify is an actor platform. Do not assume the workflow, actor, target website,
or business purpose from a previous run. Select the actor from the user's
natural-language request, the Actor Index below, and the actor's input schema
read from the Apify API (apify-client) — never from the open web.

## Core Rules

1. Understand the user's task first.
2. Identify whether Apify is needed for data collection or automation.
3. Use the Actor Index to identify candidate actors.
4. Only use actors listed in the Actor Index. If the requested source or actor
   is not in the Index (e.g. Naukri, Indeed, Apollo, a careers page), the
   request is out of scope — tell the user it is not available. Never substitute
   a different actor (e.g. the LinkedIn actor) for an unsupported source.
5. Do not treat any indexed actor as primary, default, or preferred.
6. The agent must use ONLY the provided platforms (Apify, Origami, ZeptoMail),
   their skills, and their own APIs. Never browse or scrape the web, open an
   actor's web page, fetch HTML, or search the internet for documentation. (An
   actor collecting data from its target site is its own job — this rule is
   about how the agent learns and operates.)
7. Learn the actor's input schema from the Apify API (apify-client) before every
   real run; if it is unclear, run a tiny bounded test and inspect the output.
8. Ask clarification before paid, broad, private, or ambiguous runs.
9. Keep first runs small and bounded.
10. Save raw actor metadata, input, and dataset output before transforming them.
11. Normalize output into a task-specific schema.
12. Treat actor schemas as unstable.
13. Do not use the actor's AI fields or AI-powered parameters — any AI-generated
    or AI-enriched input or output field (often labeled "AI" or prefixed `ai_`,
    and frequently a paid add-on). Use only standard search inputs and the raw
    job fields, and ignore AI-derived fields when mapping output.

## Actor Index

Actor Index entries are routing hints, not defaults. Add exact actor names only
after they are known or verified.

| Actor id | Purpose | Supported searches/data | Notes |
| --- | --- | --- | --- |
| `fantastic-jobs/advanced-linkedin-job-search-api` | LinkedIn job openings and hiring signals | Job posts, job descriptions, company/job metadata, recruiter fields available from job posts | Use only when the user intent needs LinkedIn job postings. Verify current docs/schema before use. |

## Field Contract — `fantastic-jobs/advanced-linkedin-job-search-api`

These are the actor's REAL fields (from its published input/output schema). Build
the input from this table and map output from it — do not guess, do not omit
fields, and do not leave the critical fields unset (their defaults silently
narrow the run). If the live actor ever differs, trust the actual keys and update
this table.

### Input — critical (ALWAYS set explicitly; the defaults silently limit the run)

| Field | Type | Default | Set it to |
| --- | --- | --- | --- |
| `limit` | integer 10–5000 | **10** | the bounded number of jobs needed for the target. Default 10 is almost always too small. For >2000, raise run memory to 1GB. |
| `timeRange` | enum `1h` `24h` `7d` `6m` | **7d** | the recency window the task needs. Default `7d` only looks back one week; use `6m` for a broad backfill. |
| `descriptionType` | enum `text` `html` | `text` | keep `text` so the JD comes back as `description_text`. |

### Input — targeting filters (set the ones the confirmed plan needs; all optional)

Array text filters support `:*` prefix matching.

| Field | Type | Purpose |
| --- | --- | --- |
| `titleSearch` / `titleExclusionSearch` | string[] | job-title keywords to include / exclude |
| `organizationSearch` / `organizationExclusionSearch` | string[] | company names to include / exclude |
| `organizationSlugFilter` / `organizationSlugExclusionFilter` | string[] | exact LinkedIn org slug include / exclude |
| `locationSearch` / `locationExclusionSearch` | string[] | locations, exact `City, State, Country` format |
| `descriptionSearch` / `descriptionExclusionSearch` | string[] | terms in title+description (NOT supported with `6m`) |
| `datePostedAfter` | ISO-8601 string | only jobs posted after this UTC datetime |
| `seniorityFilter` | enum[] | Entry, Associate, Mid-Senior, Director, Executive, Internship |
| `industryFilter` / `industryExclusionFilter` | string[] | exact, case-sensitive LinkedIn industry names |
| `organizationEmployeesGte` / `organizationEmployeesLte` | integer | min / max company headcount |
| `organizationSizeFilter` | enum[] | size buckets: 1, 2-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+ |
| `removeAgency` | boolean | drop recruitment agencies / low-quality sources |
| `excludeATSDuplicate` | boolean | de-dup vs the career-site dataset |
| `noDirectApply` / `directApply` | boolean | exclude / require LinkedIn Easy Apply |
| `hasNoLocation` | boolean | only jobs with no normalized location |

Do not use the actor's AI (`ai*`) input filters, or any deprecated or paid
filter.

### Output — read from each returned job → canonical lead field

| Canonical field | Actor output key | Notes |
| --- | --- | --- |
| company | `organization` | |
| company URL | `organization_url` | |
| website | `org_linkedin_website` | |
| industry | `org_linkedin_industry` | |
| company size | `org_linkedin_size` / `org_linkedin_headcount` | |
| is-agency flag | `org_linkedin_recruitment_agency_derived` | drop agencies for lead-gen |
| job title | `title` | |
| job URL | `url` | |
| date posted | `date_posted` | the real key (also `date_created`) |
| seniority | `seniority` | |
| employment type | `employment_type` | |
| location | `locations_derived` | normalized; also `cities_derived` / `regions_derived` / `countries_derived` |
| job description (JD) | `description_text` | NOT `description`; needs `descriptionType:"text"` |
| contact name | `recruiter_name` | on the job post — carry it; do not wait for Origami |
| contact title | `recruiter_title` | |

Apify returns NO email — `recruiter_name` / `recruiter_title` are the only contact
handles on the post. The email comes from Origami enrichment, or occasionally
from the JD text. Ignore any `ai_*` field the actor returns.

## Actor Selection

When choosing an actor, consider:

- target platform or website
- requested data type
- search vs scrape vs monitoring vs automation
- public vs private/login-only data
- expected output fields
- result count, cost, and scale
- rate limits and platform rules

If more than one actor might fit, prefer the actor whose input schema (read from
the Apify API) most directly supports the user's required fields with the
smallest bounded run.

## Actor Verification

Before running any actor:

1. Use apify-client to read the actor's metadata and its default build's input
   schema from the Apify API. Do not open the actor's web page, scrape HTML, or
   search the open web.
2. Save the actor metadata/schema to `runs/<runId>/raw/apify-actor.json`.
3. If the schema is unclear or incomplete, run a tiny bounded test (limit 1) and
   inspect the returned items.
4. Do not invent schema fields.
5. Confirm the actor still exists and supports the needed workflow.
6. Confirm the current input schema and output shape.
7. If the actor does not support a requested filter or field, report that and
   adapt the plan before spending credits on a broad run.

## No External Docs

Do not keep or fetch a web-sourced documentation cache. Read the actor's input
schema from the Apify API (apify-client) each run, and save what you used under
`runs/<runId>/raw/` if useful. Never search the web or open actor web pages for
documentation.

## Clarification Gate

Ask before running Apify if any relevant parameter is unclear:

- target website or platform
- search query
- geography or location
- result count
- date or time range
- required fields
- whether login/private data is involved
- whether paid credits are acceptable
- whether broad scraping is acceptable

Do not spend credits based on guessed intent.

## Schema Mapping

Map the user request to the selected actor's actual input schema after
verification.

1. Start from the confirmed plan: Target, Collect, Filters, Output fields, and
   anything the user ruled out.
2. Always read the actor's live input schema from the Apify API (apify-client)
   first. Use the Field Contract above as mapping guidance — which fields matter
   and how they map to the plan — not as a replacement for the live schema. If
   the live schema differs from the contract, trust the live schema and update
   the contract.
3. Map only the confirmed Selection filters to supported actor input fields.
4. Do not add broad keywords, adjacent brands, departments, roles, or rejected
   interpretations unless the confirmed contract allows them.
5. Do not invent input fields.
6. Preserve unsupported user constraints as notes in the run output.
7. Save the exact input payload to `runs/<runId>/raw/apify-input.json`.

## Run Flow

1. Create `runs/<runId>/`.
2. Save actor metadata/schema to `runs/<runId>/raw/apify-actor.json`.
3. Save actor input to `runs/<runId>/raw/apify-input.json`.
4. Start the actor.
5. Poll until terminal status.
6. Fetch dataset items.
7. Save raw dataset to `runs/<runId>/raw/apify-dataset.json`.
8. Check each result against the confirmed plan: keep results that match the
   confirmed target, drop clearly different companies / people / scopes, and ask
   if unsure.
9. Save rejected or uncertain records with reasons under `runs/<runId>/rejected/`
   or `runs/<runId>/review/`.
10. Normalize only the results that matched the confirmed target into
   `runs/<runId>/normalized/`, mapping each field via the Field Contract above —
   this includes the on-post `recruiter_*` contact fields, so carry them now and
   do not wait for Origami. Include every required output field; only when a
   field is truly absent in the raw data, write `null` or `unknown` instead of
   omitting it. Before the JD (`description_text`) is dropped downstream, scan it
   for any contact email / poster details and carry them into the lead record
   marked `emailSource: "jd"` (unverified) — dropping the JD later is not
   discarding this; the harvested contact stays on the lead.
11. Save final task output under `outputs/`. For a lead-gen task, the normalized
    rows are not final — hand them to Origami to add the relevant person + email
    before delivering.

Do not pass non-matching or uncertain results to normalization, final reports,
Origami, ZeptoMail, or any downstream capability unless the user confirms the
expansion.

## Reaching the target (collection loop)

The requested count is N **of the confirmed unit** (a company / chain, a job
posting, or a person — whatever the request implies). Count and dedupe by that
unit, after filtering to the target — not by raw actor records.

This loop runs at the Apify collection stage ONLY. Reach the target here BEFORE
any enrichment or output — never enrich / output a partial set and top up later.

### Initial run

- `limit` = `max(target × 3, 50)`; for a high-noise search (broad / common role,
  or a large company with many unrelated postings) use `target × 5`. Cap by any
  agreed max-raw / cost bound; hard ceiling 5000.
- `timeRange` from the freshness intent, not a fixed default: latest / today /
  this week → `24h` or `7d`; currently hiring / active jobs → `7d`; broad hiring
  data → `6m`.

### The loop

1. Run the actor → fetch raw → filter to the confirmed target → dedupe → count by
   the confirmed unit.
2. If count ≥ target → stop; take N.
3. If count < target → apply the NEXT ladder lever (ONE change), log what you
   changed and the new count, and re-run:
   - L1 — widen `timeRange` toward `6m` (if not already there). `descriptionSearch`
     / `descriptionExclusionSearch` are NOT supported with `6m`, so drop them
     before switching and log it.
   - L2 — add on-target `titleSearch` synonyms (same role concept) — ONLY if the
     confirmed plan has a role / title. If the target is a company only, SKIP
     this lever; never invent role filters.
   - L3 — raise `limit`.
   After each re-run, re-filter to the SAME target.
4. Stop when: the target is met, OR the full applicable ladder has been tried once
   (minimum effort), OR you reach 4 collection attempts total (the initial run
   plus up to 3 re-runs).
5. Take the N on-target results (or fewer if exhausted) and record the exact count.

Widening `timeRange` (e.g. `7d`→`6m`) only broadens the source window; it does NOT
relax what counts as a match. Never relax the match-definition or non-target
filters, and never invent roles, to reach the count — if fewer on-target results
exist, deliver those and say so. Zero is just the terminal case of that rule:
deliver none and report it, and do NOT hand off to another platform for jobs
(see AGENTS, no cross-capability substitution). Only ask the user before running
a clearly expensive or broad paid job beyond the agreed scope.

## Boundaries

- The agent must not browse or scrape the web, open actor web pages, or fetch
  external documentation. Read everything it needs from the Apify API.
- Do not scrape private, hidden, login-only, or non-public personal data.
- Do not bypass access controls.
- Do not run broad or high-cost jobs without approval.
- Do not send messages or update external systems unless explicitly approved.
- Do not infer missing private contact data.

## Skill Composition

Use Apify for data collection or actor workflows. For a lead-generation /
sales-prospecting task, Apify rows are NOT the final output — a company / job
list alone is not a finished lead. Apify gives the opportunity and at most a
recruiter name / title / LinkedIn, never an email. So whenever the contact or
email is missing (the default for outreach), hand off to Origami. Downstream:

- Origami -> find / enrich the relevant person and their email for each lead
  (required for lead-gen; Apify cannot supply emails)
- ZeptoMail -> email drafting or sending after explicit approval
- local scripts -> normalization, dedupe, classification, scoring, joins
