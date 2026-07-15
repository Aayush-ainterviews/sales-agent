# Implementation Plan — pi + E2B multi-user agent (v1)

> Companion to [architecture-decisions.md](architecture-decisions.md) (the "what/why") and [../CONTEXT.md](../CONTEXT.md) (vocabulary).
> Ordering principle: kill the riskiest assumption first; end every phase with something you can run and verify. No phase depends on a later phase.

---

> **Status:** Phase 0 ✅ · Phase 1 ✅ (`sales-agent-v1`, boot 1.2s) · **Phase 2 ✅ COMPLETE** — registry, sandbox_manager, daemon_client, turn_runner, session_backup, FastAPI/SSE; all tests green incl. HTTP smoke 6/6. Phase 3 (multi-user) ⬅ next.
> Diag learnings (2026-07-10): the running daemon's process name is `pi` (argv rewritten) → kill with `pkill -x pi`, find with `pgrep -x pi`; `probe()` tolerates a dead daemon (send to a gone PID raises → treat as False, not crash). Both baked into `lib/daemon_pipe.py`.
>
> **Phase 3 ✅ COMPLETE** (2026-07-11, multiuser 14/14) — auth (bearer→user_id), per-user turn slot (Q17: admission-time `try_claim` → deterministic 409), release-slot-at-agent_end-before-backup, `_pipes` guarded.
>
> **Postgres migration ✅ COMPLETE** (2026-07-13, all tests green on PG — registry 14/14, turn_runner 7/7, multiuser 14/14) — SQLite → Postgres (Q18), `sessions` table with full JSONL in a `log` column (Q19, R2/S3 eliminated), manual DB lock removed (pool), backend is single-instance (Q20). Local dev = Docker `postgres:16`.
> **Disaster restore ✅** (2026-07-13, test_restore 3/3) — dead sandbox → recreate + write the Postgres `log` back into the fresh sandbox (before daemon start) → `pi -c` continues it → history survives. `session_backup.restore_session` + sandbox_manager recovery branch.
>
> **Phase 4 ✅ COMPLETE** (2026-07-13) — outreach path, all checkpoints green: pending_batches queue (E 9/9), submit-batch skill + template v2 (14/14), batch_collector (G 6/6), ZeptoMail send_executor with SEND_OVERRIDE_TO safety net (H 2/2), approval endpoints + feedback turn (I 7/7 end-to-end incl. real send). Details: docs/phase-4-plan.md.
> **Phase 5 in progress** — packaging (requirements.txt, railway.json single-instance+healthcheck, runtime 3.12), SSE keepalive, structured JSON logs + turn_complete metrics, CORS, boot-time secret asserts — all ✅ (test_packaging 4/4). **Deployed LIVE to Railway 2026-07-13**: repo on GitHub (Aayush-ainterviews/sales-agent), app service in the production-postgres project, `DATABASE_URL=${{production-postgres.DATABASE_URL}}`, `/health` 200 at sales-agent-production-*.up.railway.app. **v1 LIVE & VERIFIED IN PRODUCTION (2026-07-13):** E1 turn over SSE → LIVE_OK; E3 full outreach loop on the deployed app → real ZeptoMail email received + structured JSON logs visible in Railway. The whole product works end-to-end in the cloud.
> Remaining (all optional / deferred): E4 redeploy self-heal check; per-user cost report; egress allowlist tighten; and the design-settled deferrals (verifier/monitor + stage evals + in-turn submit_batch extension, scoped tokens, web-search allowlist, real frontend).

## Phase 0 — Spike: the PTY ↔ RPC pipe *(do this before writing any real code)*

The single assumption the whole design leans on (Q5b) is: *pi's JSONL RPC protocol can be driven reliably through E2B's PTY layer.* If this is miserable in practice, the fallback is `pi --mode json` one-shot (option a) — and we want to know that in day one, not after the backend is built.

Throwaway script (`scripts/spike-pty-rpc.ts`), plain E2B sandbox (no template yet), `npm i -g pi` by hand inside it. Prove, in order:

1. Start `pi --mode rpc` via PTY with `GEMINI_API_KEY` in the PTY env; disable echo (`stty -echo` or equivalent) so sent commands don't pollute the stream.
2. **Probe:** send `{"id":"1","type":"get_state"}`, parse the response out of the PTY stream (per-line: try-JSON-parse, else log-and-skip).
3. **Turn:** send a `prompt`, stream `message_update` deltas, see `agent_end`.
4. **Steer:** send a second prompt mid-stream with `streamingBehavior: "steer"` — confirm it lands.
5. **Abort:** `{"type":"abort"}` mid-turn — confirm clean stop.
6. **Restart-with-continuity:** kill the pi process, start a new one with `-c`, ask "what did we just talk about" — confirm the session file carried over.
7. **Pause/resume:** pause the sandbox (idle), resume via `Sandbox.connect()`, probe → (likely dead stream) → restart with `-c` → continuity still holds.

**Exit criteria:** all 7 pass, and you've written down every PTY quirk you hit (echo, line endings, stderr noise, U+2028 — don't parse with `readline`). These notes become the DaemonClient's test cases.
**Bail-out trigger:** if 1–3 can't be made reliable in a few days, switch Q5 to json one-shot and simplify Phases 2–3 accordingly (steering drops out; everything else survives).

---

## Phase 1 — Template

Now freeze the environment the spike validated.

- Template def (SDK or CLI): base image → node → `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` → copy `pi-config/` → `/root/.pi/agent/` (settings.json, AGENTS.md, skills; **no** extensions yet — `submit_batch` comes in Phase 4).
- `settings.json` essentials: `defaultProjectTrust: "always"` is NOT needed (config is global-level, trust doesn't apply) — but set compaction defaults, `quietStartup`, and pin the model.
- Non-secret env only (slot 0). No start command (Q8). No secrets (Q11).
- Repo layout: `template/` (template def + `pi-config/`), build script, template tag versioned (`sales-agent-v1`).

**Exit criteria:** `Sandbox.create('sales-agent-v1')` → manually start daemon via the spike script → one turn works. Create with egress allowlist (Gemini + Origami + Apify only) → verify a `curl google.com` from inside times out **at the application level** (E2B caveat: TCP may appear to connect).

---

## Phase 2 — Backend core (single hardcoded user)

Node/TypeScript service. One user id hardcoded; multi-user is Phase 3.

Modules, in build order:

1. **Registry** — SQLite table `sandboxes(user_id, sandbox_id, template_version, status, created_at, last_used_at)`.
2. **SandboxManager** — `getOrCreate(userId)`: DB → `connect()` → on failure create + upsert row (Q3/Q4, one path).
3. **DaemonClient** — the module the spike de-risked: `ensureRunning()` (probe → start with secrets in env + `-c`), line parser, id-correlation map, event emitter, health-ping loop, `restart()`. Port the spike's quirk list into unit tests (feed recorded PTY streams through the parser).
4. **TurnRunner** — `POST /users/:id/messages` → ensure sandbox+daemon → send prompt → return SSE stream of events; `setTimeout` bump every 5 min while streaming; 20-min watchdog → abort + error event (Q16).
5. **Steer/abort** — `POST /users/:id/steer`, `POST /users/:id/abort` (Q7/Q15).
6. **SessionBackup** — on `agent_end`: `files.read` the session JSONL → local `backups/` dir first (S3 later, same interface) (Q6).

**Exit criteria (scripted, keep as smoke tests):**
- `curl -N` one full turn, deltas streaming.
- Second turn remembers the first (session continuity).
- `kill -9` the pi process inside the sandbox → next request self-heals (probe → restart → turn works).
- Idle 15 min → sandbox paused (check E2B dashboard) → next request resumes and continues.
- Watchdog: set cap to 30s temporarily, run a long turn, confirm abort + clean error to client.
- Backup file appears after every turn and is valid JSONL.

---

## Phase 3 — Multi-user + lifecycle hardening

- Remove the hardcoded user: user id from request (internal users — a static token header per user is enough for v1; real auth is a frontend-phase concern).
- Per-user secrets map in backend config (today: same shared values — the *shape* is per-user so scoped tokens slot in later without refactor).
- Concurrent turns for different users (each has its own PTY/daemon — verify no cross-talk in the DaemonClient registry; key everything by sandboxId).
- Same-user concurrent request policy: if a turn is streaming, new message → steer (Q7); expose queue state in an SSE event so the frontend can show it.
- `POST /users/:id/reset` — kill sandbox, delete row, next request provisions fresh (backup retained).
- Template-version stamp checked at connect: mismatch → log only (v1 interim policy from Q14: manual recreate when we choose to).

**Exit criteria:** 3 users, interleaved turns from all of them, transcripts never bleed; user A's `submit`/steer/abort never touches user B's daemon; reset works and old backups survive.

---

## Phase 4 — Outreach path (Draft Batch → Approval → Send)

1. **`submit_batch` extension** (first extension in `pi-config/`, template rebuild → `sales-agent-v2`): TypeBox schema (batch_id, campaign, per-lead: lead_id, email, subject, body, evidence[]), validates in-turn, writes `/workspace/outbox/<batch_id>.json` + session custom entry, tool result confirms registration (Q10).
2. **Batch collector** — backend on `agent_end`: list `/workspace/outbox/`, pull new batches, insert into `batches` table (status `pending_approval`), delete from sandbox outbox.
3. **Approval API** — `GET /batches?status=pending`, `POST /batches/:id/approve|reject` (+ optional per-lead edits recorded as diffs). Every decision row: who/when/what (audit).
4. **Send executor** — on approve: send via Origami sequencer API (creds live ONLY here); per-lead send status back into `batches`; retries + partial-failure handling (Q9).
5. **Feedback turn** — after send completes: backend sends the agent a next-turn message ("batch B-123: 18 sent, 2 bounced: …") so follow-ups have ground truth.

**Exit criteria:** end-to-end dry run with a test campaign: agent drafts → schema-rejects a bad draft in-turn → registers batch → appears in approval queue → approve → sends land (test inbox) → agent's next turn knows the results. Negative test: grep the sandbox env + fs for any send credential → must be absent; egress attempt to the provider from inside → blocked.

---

## Phase 5 — Ops polish (before real teammates use it)

- Secrets out of `.env`-in-repo into proper env/secret store; startup fails loudly if any missing.
- Structured logs: every turn (user, duration, tokens/cost from `agent_end` usage, stop reason), every daemon restart, every watchdog abort, every batch decision.
- Cost report per user from session usage data (Q11b's justification — prove it works).
- Backup destination → S3/R2; restore script (`restore-session.ts <userId> <backup>`) tested once for real (Q14's interim recreate path depends on it).
- Config file for all tunables (timeouts, watchdog, template tag, allowlist).
- Deploy backend (single small VM/service is fine for 5–10 users); confirm backend-redeploy mid-turn → daemons die → next requests self-heal (the Q8 path, now in production shape).

**Exit criteria:** a teammate you didn't brief can be onboarded (row + token), runs a campaign end-to-end, and you can answer "what did that cost / what happened" from logs alone.

---

## Explicitly NOT in v1 (triggers in architecture-decisions.md)

Verifier/monitor (two-layer design settled, deferred) · Stage evals · Config-sync at daemon start (Q14 deferred; interim = template rebuild) · Scoped tokens / credential proxy (trigger: first external user) · Web search API in allowlist · Real frontend (Postman/curl + the SSE stream are the v1 "UI").

## Suggested first session of work

Phase 0, steps 1–3. Everything else in this plan is hostage to that spike.
