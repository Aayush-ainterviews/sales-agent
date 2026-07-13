---
name: apollo-enrichment
description: Use Apollo to fill contact and company fields that are still MISSING after Origami — the last rung of the enrichment waterfall. Direct request/response (no tables). People and organization enrichment sync; phone and waterfall are async (webhook + polling).
---

# Apollo Enrichment

Apollo is the **last rung of the enrichment waterfall**: after Apify (collection)
and Origami have filled what they can, Apollo fills the contact / company fields
that are **still missing** on a lead. Same kind of data as Origami (people /
company enrichment) — used only as a **fallback for gaps**, never first.

Apollo is a **direct request/response** API. Unlike Origami there is NO table,
workspace, upsert, or cell pipeline — you put identifiers into the request and
read fields back from the response synchronously. Only phone and Apollo's own
waterfall are asynchronous (webhook + polling).

## Current scope (now)

Use ONLY the plain **synchronous** calls: `POST /people/match` and
`GET /organizations/enrich` (plus their bulk variants). These fill the work
email, contact (name / title / LinkedIn), and company firmographics — Apollo's
role as the last rung of our waterfall.

Do NOT set these flags for now — they are deferred (need the webhook / polling
infra that does not exist yet):

- `run_waterfall_email` / `run_waterfall_phone` — Apollo's OWN data-waterfall
  across its connected providers.
- `reveal_phone_number` — async phone.
- `reveal_personal_emails` — personal email; off for now (we use the work email
  from the plain match).

So phone and Apollo's own waterfall are unavailable until that infra exists; the
Async section below applies only then.

## When to use / not use

Use Apollo when, after Origami, a lead still has a missing / unverified field it
can supply: a contact's email / LinkedIn / title / name, a phone, or company
firmographics (industry, size, domain, …).

Do NOT use Apollo:
- as the FIRST enrichment source — run it only after Origami, only for what is
  still missing (it is paid, per-field).
- to collect job postings (Apify) or to send email (ZeptoMail).
- to re-fetch a field already present and verified (never overwrite a verified
  value with a guessed one — see Merge).

## Setup

- Key: `APOLLO_API_KEY` from `.env`.
- Base URL: `https://api.apollo.io/api/v1`.
- Headers: `x-api-key: $APOLLO_API_KEY`, `Content-Type: application/json`,
  `accept: application/json`.
- Rate limit: ~600 calls/hour. Bulk endpoints share the hourly/daily limit and
  run at ~50% of the single endpoint's per-minute rate.

## Modes (pick the smallest that fits)

| Mode | Endpoint | For |
| --- | --- | --- |
| Person | `POST /people/match` | one contact |
| People (bulk) | `POST /people/bulk_match` | 2–10 contacts (chunk > 10) |
| Organization | `GET /organizations/enrich` | one company |
| Organizations (bulk) | `POST /organizations/bulk_enrich` | 2–10 companies (chunk > 10) |
| Phone / waterfall | reveal / waterfall flags on the people endpoints | **async** — see below |

Prefer the **bulk** people / org endpoints when enriching several leads at once
(credit- and call-efficient), chunking into batches of 10.

## Input map — lead → Apollo request (identifiers, strongest first)

Apollo matches better with more / stronger identifiers. Build the request from
what the lead already has, in this order (a `200` with thin input — e.g. name
only — can return no match):

**Person** (`/people/match` query params, or each `details[]` item in
`/people/bulk_match`):
1. `linkedin_url`  — best we will usually have
2. `email`  — if a candidate email already exists
3. `first_name` + `last_name` + `domain`
4. `name` + `domain`
5. `name` + `organization_name`
6. `name` alone — last resort; warn (weak match)

(We will not have `id`, the Apollo person ID.)

**Organization** (`/organizations/enrich` query params, or `domains[]` for bulk):
- `domain` (best) plus any of `linkedin_url` / `website` / `name` for accuracy.

Normalize before sending: strip `www.` / `@` / protocol from `domain`; keep
`website` a full URL; lowercase email + domain; keep LinkedIn URLs as-is.

## Output map — Apollo response → lead fields (fill missing only)

Contact (from `person`, or each `matches[]` item):

| Lead field | Apollo key |
| --- | --- |
| contact.name | `person.name` (`first_name` + `last_name`) |
| contact.title | `person.title` (or `employment_history[]` where `current: true`) |
| contact.linkedin_url | `person.linkedin_url` |
| contact.email | `person.email` + `person.email_status`; fallback `person.contact.contact_emails[].email` |
| contact.email_confidence | `person.extrapolated_email_confidence` (for guessed emails) |
| contact.phone | `person.contact.phone_numbers[].sanitized_number` (+ `status`, `type`, `dnc_status`) — async |
| (optional) seniority / dept | `person.seniority`, `departments[]`, `functions[]` |

Company (from `person.organization`, or `organization` on the org endpoints):

| Lead field | Apollo key |
| --- | --- |
| company.name | `organization.name` |
| company.domain | `organization.primary_domain` |
| company.website | `organization.website_url` |
| company.linkedin | `organization.linkedin_url` |
| company.industry | `organization.industry` (+ `industries[]`) |
| company.size | `organization.estimated_num_employees` |
| company.location | `organization.city` / `state` / `country` |
| company.phone | `organization.primary_phone` / `phone` |
| (optional) revenue / funding / tech / headcount | `annual_revenue`, `latest_funding_stage`, `technology_names[]`, `departmental_head_count` |

Save each filled field with `source: "apollo"` and its status.

## Email / phone status → verified vs unverified

- `email_status: verified` → **verified**.
- `email_status: guessed` / extrapolated (use `extrapolated_email_confidence`) →
  **unverified candidate** (`emailSource: "apollo"`).
- `unavailable` / masked (`email_not_unlocked@…`) / absent → **not found** — skip;
  do not overwrite anything.
- Phone: respect `phone_numbers[].status` and `dnc_status`; a DNC number is
  flagged and not used for outreach.

## Reveal flags + credit safety

- Emails and phones are **hidden by default** — set `reveal_personal_emails=true`
  (email) / `reveal_phone_number=true` (phone) **only** for leads whose email /
  phone is still missing. Reveal flags consume credits.
- Never call Apollo speculatively — only for **missing** fields. Dedupe targets;
  cache results (`last_enriched_at`, `enrichment_source`) and do not re-enrich the
  same person / company without reason.
- A `200` does NOT mean a match — confirm `person` is present (single) or
  `unique_enriched_records ≥ 1` (bulk) before treating it as enriched.

## Async — phone and Apollo's own waterfall (infra-gated)

`reveal_phone_number=true` and `run_waterfall_email` / `run_waterfall_phone=true`
return **asynchronously**: the sync response only confirms acceptance
(`waterfall.status`) and returns a `request_id`; the actual email / phone arrives
at a `webhook_url` (public HTTPS) or via `GET /webhook_result/{request_id}`.

This agent has no webhook receiver yet. Until that infra exists:
- Provide the configured `webhook_url`, save the `request_id`, then **poll
  `GET /webhook_result/{request_id}` for up to 3 minutes**; take whatever has
  arrived by then and move on (**partial is OK**).
- If nothing has arrived in 3 minutes, mark the phone / waterfall field `pending`
  and report it — do not block the run.
- `reveal_phone_number` (native phone) and `run_waterfall_phone` return different
  webhook payload shapes — parse them separately; do not mix.

## Bulk + errors

- Bulk: max 10 per request; chunk larger inputs; store each item's original index
  (response order can be ambiguous on partial failure).
- Errors: `400` bad / missing input or > 10 records; `401` bad key; `422` no
  usable match parameter; `429` rate limit → back off; `5xx` transient → retry
  with backoff. Never mark a `200` as success without confirming the body matched.

## Merge into the lead (never overwrite)

Apollo fills **only** fields still missing after Origami. Never overwrite an
existing verified value with an Apollo guessed value. A guessed Apollo email stays
an unverified candidate; a verified Apollo email may replace a `null` or an
unverified candidate. Keep `source` + `status` + confidence on every filled field
so the merge stays traceable.

## Boundaries

- Enrichment only — do not collect job postings (Apify) or send email (ZeptoMail).
- Last rung: run after Origami, only for missing fields.
- Public professional data only; respect DNC / GDPR (personal-email reveal is
  blocked in GDPR regions and may return nothing).
- Never log raw emails / phones in plaintext debug output.
