# Architecture Decisions — pi + E2B multi-user agent

> Grilling session, 2026-07-10. Vocabulary: see [CONTEXT.md](../CONTEXT.md).
> Status: design phase — nothing built yet. Locked = shared understanding reached, not immutable.

## Locked

| # | Decision | Choice | Why (short) |
|---|---|---|---|
| Q1 | First deliverable | Backend API first, frontend later | Sandbox lifecycle + turn loop matter more than UI |
| Q1b | Backend language | Python (FastAPI) | User's stack is Python; pi RPC is language-agnostic JSONL, E2B Python SDK is first-class; spike learnings transfer 1:1 |
| Q2 | Sandbox lifetime | Per-user persistent, pause-on-idle; kill only on offboard/reset | Session continuity free; paused = $0 |
| Q3 | userId → sandboxId truth | Own DB (Sandbox Registry); E2B metadata = debug label only | Fast lookup, survives kills, self-healing on drift |
| Q4 | Provisioning | Lazy connect-or-create; one code path = provisioning + recovery | Template boot ~1-2s, eager not worth a second path |
| Q5 | Turn transport | `pi --mode rpc` daemon over E2B PTY (options a/c/d rejected by user) | Live steering + sync dialogs; cost: backend owns supervisor/protocol-client (probe, parser, id-correlation, health-ping, restart-with-`-c`, ui-bridge). **Validated by spike 2026-07-10** ([spike-notes.md](spike-notes.md)): all 7 steps pass; daemon does NOT survive pause/resume → resume path = always restart with `-c`; never use `pty.connect` (connects to dead PTYs without error) |
| Q6 | Session durability | Async backup of session JSONL after each turn (S3/blob) | Disaster recovery + audit; sandbox file stays source of truth |
| Q7 | Mid-turn input | Steer by default; abort button always available | Mid-turn messages are corrections in this domain |
| Q8 | Daemon owner | Backend only; no template start command | One code path for fresh/resumed/crashed/redeploy |
| Q9 | Send execution | Backend sends (after approval); agent has NO send tool, no provider creds, no egress route to provider | Structural safety > policy; approval and execution co-located |
| Q9b | Approval model | Asynchronous: turn ends "batch pending", sandbox pauses, approval triggers next turn | No running-meter while human decides; offline-safe |
| Q10 | Draft Batch contract | **v1: a `submit-batch` skill (markdown)** — agent writes the batch JSON to `/home/user/outbox/`; backend collects + validates post-turn. The TS `submit_batch` extension (in-turn TypeBox validation) is **deferred to the evals/self-correction phase**. Both need a template rebuild, so the skill's only cost vs the extension is post-turn (not in-turn) validation | Start simple; upgrade to in-turn validation when we build evals |
| Q10b | Per-lead/send tracking schema | **Deferred** — no `batches`/`batch_items` relational tables in v1. Minimal pending-batch handling only (see Phase 4 plan) | Elaborate send/lead tracking isn't needed until volume/reporting demands it |
| Q11 | Secrets delivery | At daemon start (`commands.run` envs), never template-baked, nothing at sandbox create | Rotation = daemon restart; single door for all secrets |
| Q11b | LLM provider key | Google Gemini (`GEMINI_API_KEY`), shared operator key for all users | Per-user cost attribution comes free from pi session usage data |
| Q12 | Platform tokens (Origami/Apify) | Shared operator tokens accepted for now (users are internal) | See future items for the external-user trigger |
| Q13 | Network egress | Default-deny; allow only generativelanguage.googleapis.com (Gemini) + Origami API + Apify API | Research is platform-mediated; exfiltration path closed |

| Q15 | Frontend streaming | SSE for events + POST for steer/abort | Plain HTTP, auto-reconnect; WS only if a real duplex need appears |
| Q16 | Time policies | 15 min idle-pause; backend bumps `setTimeout` every 5 min while a turn streams; 20 min turn watchdog (abort + log) | E2B can't see in-sandbox work; watchdog aborts feed rulebook evolution |
| Q17 | Same-user concurrent `/messages` | One active turn per user; a second `/messages` while one streams → 409. Interjection is the `/steer` endpoint; different users run fully in parallel | Refines Q7: mid-turn input is `/steer` (built + tested); `/messages` always means a fresh-turn intent, keeps semantics clean and closes the double-provision race |
| Q18 | Datastore | Postgres (Railway managed; local dev = Docker `postgres:16`). `sessions` table keyed by `user_id UNIQUE` | SQLite's local file is wiped on Railway's ephemeral redeploy → mapping lost. Postgres persists; pool removes the Phase-3 manual DB lock. **Done 2026-07-13, all tests green on PG** |
| Q19 | Session log storage | Full pi session JSONL stored as a single `text` column (`sessions.log`), overwritten each turn; read via `cat` over `commands.run` (not `files.read`, which threw transient EAGAIN) | One row per session, not row-per-message (that would duplicate pi's tree). Eliminates R2/S3 entirely. Caveat: revisit if a session grows to MBs |
| Q20 | Backend instances | Exactly ONE (v1) | `_pipes`/`_busy`/`_active` are per-process RAM tied to specific sandboxes/PTYs; 2 instances → a user's `/steer` could miss their pipe. Multi-instance (sticky routing / shared daemon registry) is a future item |
| Q21 | Send provider | **ZeptoMail** (Zoho; `POST api.zeptomail.in/v1.1/email`, `Authorization: Zoho-enczapikey`). `ZEPTOMAIL_API_KEY` + `ZEPTOMAIL_FROM_EMAIL` live ONLY in the Backend send executor — never in `secrets_for_user`, never in the sandbox | Reinforces Q9: research providers (Origami/Apollo/Apify) reach the sandbox; the send credential never does |
| Q22 | Pending-batch store | A single minimal `pending_batches(id, user_id, batch_json jsonb, status, created_at)` table — approval queue only, no per-lead rows (per-lead/send tracking = Q10b future). Survives backend restart / sandbox death | The batch must outlive the sandbox for async approval; a sandbox-file-only store would lose it on reap/reset |
| Q23 | Send test target | The operator's own email inbox (never real leads) | Simplest way to verify real delivery in dev |
| Q24 | Product relevance (ICP) | **Hard, always-on filter**: product = companies hiring for their physical retail stores. Company = runs retail stores + store-hiring signal; Job = store-level retail role; Contact = retail-hiring decision-maker (Sales/Store/TA Head kind); email = critical. Auto-scoped (user never restates); applied at BOTH the Apify search stage (`titleSearch`/`industryFilter`/`removeAgency`) and the filter stage (strict drop); the ICP's retail-store-role scope always applies, even for company-only queries (overrides apify L2 "skip role for company-only"). Encoded concept-based, not keyword lists | Agent had no product-relevance anchor → drifted to any company/employee data. A hard standing filter, not a per-query flag, keeps every run on-ICP without the user re-specifying. Concept-based because flash overfits literal token lists ([[instruction-writing-style]]). See [GOAL.md](../GOAL.md), [CONTEXT.md](../CONTEXT.md) |

## Deferred (decide at evals implementation)

**Q14 — Config update path for live sandboxes.** Template is a frozen snapshot; running/paused sandboxes don't get rebuilds. Options noted:

- **(a) Recreate-on-version-bump:** stamp template-version per sandbox in Registry; on request with stale version → kill → create from new template → restore history from Session Backup. Clean but heavy per small fix; depends on restore path daily.
- **(b) Config-sync at daemon start:** template holds only heavy things (node, pi, apt); backend `files.write()`s skills/extensions/rulebook at every daemon start. Rulebook fixes deploy in seconds without recreate. Cost: sandbox state = template-version + config-version, both stamped in Registry.

Interim behaviour until decided: everything in template; updates = rebuild (+ recreate if needed).

## Future items (with triggers)

- **Scoped tokens / credential proxy** — trigger: first external (non-team) user. Shared platform tokens mean one user's agent can reach another user's platform data; VM isolation does not cover this. Proxy pattern (real token never enters sandbox) is the strong fix.
- **Web search/fetch API in egress allowlist** (Exa/Serper/Tavily) — trigger: agent repeatedly blocked on legitimate web reads.
- **Stage Evals** — automated per-stage evaluation gating pipeline progression; runs in Backend against Draft Batches + Session Backups.
- **Verifier/monitor system** (deferred by user, design settled): two layers — Layer 1: deterministic `tool_call`-hook checks as pi extension inside the sandbox (scope enforcement, blocklists, batch dedupe; instant block, no LLM cost). Layer 2: verifier sub-agent in the Backend, post-turn, reading Session Backup + Draft Batch; output annotates the approval queue; repeat failures become human-gated rulebook proposals. Layer 2 never races the turn. Rulebook distribution depends on Q14's outcome.
- **Send-result feedback loop** — backend reports send outcomes (sent/bounced/replied) into the agent's next turn so it can plan follow-ups. (Design exists, build with sequencer integration.)
