# System Flow — end to end (TARGET state: after the Postgres migration)

> How a request travels through the whole system, step by step, and what happens
> on every lifecycle path (cold start, warm resume, crash, pause, concurrent users,
> steer/abort). This describes the system **as it will be once the Postgres migration
> is done** — the store is Postgres (Railway), session logs live in a `sessions.log`
> column (no R2/S3), and the Phase-3 manual DB lock is gone (a pool handles concurrency).
> Everything else (sandboxes, daemon, PTY, turns, steer) is unchanged from Phases 1–3.
>
> Companion docs: [architecture-decisions.md](architecture-decisions.md) (what/why),
> [implementation-plan.md](implementation-plan.md) (build order), [../CONTEXT.md](../CONTEXT.md) (vocabulary).

---

## 0. The cast — who is who

```
Frontend ──HTTP──► Backend (FastAPI, one instance) ──E2B SDK / PTY──► E2B Sandbox (per user)
                        │            │                                     │
                        │            └─Postgres (Railway): sessions        └─ pi daemon (--mode rpc)
                        ├─ Registry: reads/writes the sessions table             └─ tools: read/write/bash
                        ├─ SandboxManager: connect-or-create                      └─ session JSONL on disk
                        ├─ DaemonClient: PTY supervisor + secrets
                        ├─ TurnRunner: one turn's lifecycle + slots
                        └─ SessionBackup: read JSONL out after each turn
```

| Component | File | One-line job |
|---|---|---|
| **Backend** | `backend/app.py` | FastAPI: 4 endpoints, holds one shared `TurnRunner` |
| **DB / pool** | `backend/db.py` | Postgres connection pool + `sessions` table schema |
| **Registry** | `backend/registry.py` | Reads/writes `sessions` (userId → sandboxId, + the full log). Pool per call, no manual lock |
| **SandboxManager** | `backend/sandbox_manager.py` | `get_or_create(user)`: connect to existing sandbox or make one |
| **DaemonClient** | `backend/daemon_client.py` | Start/supervise the `pi` daemon; inject secrets; restart on death |
| **DaemonPipe** | `lib/daemon_pipe.py` | One live PTY connection to one daemon: send commands, parse JSON events |
| **TurnRunner** | `backend/turn_runner.py` | Run one turn as a stream; per-user turn slot; watchdog; steer/abort |
| **SessionBackup** | `backend/session_backup.py` | Read the sandbox's session JSONL out after each turn (Registry persists it) |
| **Template** | `template/` (E2B cloud) | Frozen image: node 22 + pi + skills/config, baked once |

Three places state lives (never confused):
- **Postgres `sessions` table** — durable: `userId → sandboxId` + the **full session JSONL** in a `log` column. Survives backend redeploys.
- **Sandbox disk** — the pi **session JSONL**, source of truth *while the sandbox lives*; the `log` column is its persisted copy.
- **Backend RAM** — `DaemonClient._pipes` (live PTY connections), `TurnRunner._busy`/`_active` (who's mid-turn). Disposable; rebuilt on demand; **per-process → one instance only** (see §8).

---

## 1. Before any request — the template exists

Done once, offline (`template/build_template.py`):
1. E2B builds an image on their cloud: base → **node 22** (base's node 20 is too old for pi) → `pi` installed (pinned) → `pi-config/` copied to `~/.pi/agent/` (settings, AGENTS.md, 3 skills) → `GOAL.md` at `/home/user/`.
2. Stored in the E2B account as **`sales-agent-v1`**. No secrets, no start command baked in.
3. From now on `Sandbox.create("sales-agent-v1")` boots this in ~1–2s — nothing installs at request time.

---

## 2. THE MAIN FLOW — a returning user sends a message

This is the common path. A user (say `rohan`) whose sandbox already exists (paused) sends a message.

```
Frontend ──► POST /users/rohan/messages   { "message": "20 retail leads nikaalo" }
             Header: Authorization: Bearer <rohan's token>
```

### Step 1 — Auth (backend/app.py + auth.py)
- `require_user` reads the bearer token → looks it up in `config.user_tokens()` → gets `user_id`.
- Token unknown → **401**. Token's user ≠ path `{user_id}` → **403**.
- Identity comes from the **token**, never blindly from the URL.

### Step 2 — Claim the turn slot (app.py → TurnRunner.try_claim)
- `runner.try_claim("rohan")` runs **synchronously, before any slow work**.
- If `rohan` already has a turn streaming → returns False → endpoint returns **409** ("turn_in_progress").
- Else marks `rohan` busy and returns True. (This admission-time claim is what makes "which concurrent request wins" deterministic.)
- The endpoint returns a `StreamingResponse(sse())` — an SSE stream. Everything below runs *inside* that generator, iterated by Starlette's threadpool.

### Step 3 — Get the sandbox (TurnRunner → SandboxManager.get_or_create)
- Registry lookup: `SELECT sandbox_id FROM sessions WHERE user_id='rohan'` → `sbx_abc123`. The read borrows a connection from the Postgres pool (each thread its own → no manual lock).
- `Sandbox.connect("sbx_abc123")` → the paused sandbox **resumes** in ~1s.
- (If connect fails — sandbox was reaped — the SAME code path creates a fresh one and `INSERT ... ON CONFLICT(user_id) UPDATE`s the Registry row. No separate recovery branch.)

### Step 4 — Ensure the daemon is running (DaemonClient.ensure_running)
- Is there a live `DaemonPipe` for this sandbox in `_pipes`? Probe it (`get_state`).
  - **Alive** → reuse it.
  - **Dead / none** (a resumed sandbox's daemon is always dead — pi doesn't survive pause) → `pkill -x pi` (clean any orphan) → open a fresh PTY → start `pi --mode rpc -c` with **this user's secrets** injected via the PTY env (`GEMINI_API_KEY`, `ORIGAMI_API_KEY`, `APOLLO_API_KEY`, `APIFY_TOKEN`) → probe until it answers.
  - `-c` continues the existing session on disk → the agent remembers past turns.

### Step 5 — Send the prompt & stream (TurnRunner._run_turn_locked)
- `daemon_client.prompt(sid, message)` → the message goes over the PTY into the daemon's stdin as JSON.
- Yields `{"type": "turn_start"}` to the SSE stream.
- Then loops, reading events off the pipe's queue and yielding each one:
  - `message_update` (text deltas — the agent "typing"), `tool_execution_start/end` (agent running bash/read/etc.), … until `agent_end`.
  - Each event → `data: {json}\n\n` on the SSE stream → **frontend sees it live**.
- **Two timers run during the loop (Q16):**
  - every 5 min: `sbx.set_timeout(15min)` — pushes the idle-pause countdown back so E2B doesn't pause a working sandbox.
  - 20 min hard cap: if exceeded → `abort` the turn, yield `turn_error`, break.

### Step 6 — Inside the sandbox (what pi actually does)
- pi builds context (system prompt + session history + skills) → calls Gemini → the model decides ("query Origami for retail leads") → pi runs that as a tool (bash → a skill script) → result goes back to the model → next decision → … → final answer.
- **Every event pi produces is written to the session JSONL on disk in real time** (pi's own behavior — we don't trigger it). This file is the source of truth.
- The API key that Gemini needs is in the daemon's env (Step 4), so the LLM call goes out from *inside* the sandbox.

### Step 7 — Turn ends (TurnRunner)
- On `agent_end`: **release the slot immediately** (`_release("rohan")`) — so rohan's next turn can start right away, *before* the (slower) DB write.
- Then persist: `log = SessionBackup.read_latest_session(sbx)` (read the JSONL from the sandbox) → `registry.save_log("rohan", log)` = `UPDATE sessions SET log=..., updated_at=now() WHERE user_id='rohan'`. The full JSONL now lives in the `log` column — **that IS the backup** (no R2/S3). Best-effort (a failure never breaks the turn).
- The SSE stream ends. The frontend has the full answer.

### Step 8 — Idle → pause
- 15 min with no request → E2B auto-pauses the sandbox. **Paused = ₹0**, full state preserved on disk. Next request resumes it (back to Step 3).

---

## 3. Cold start — a brand-new user's first request

Same as §2 with two differences in Steps 3–4:
- Registry has **no row** for the user → `Sandbox.create("sales-agent-v1", metadata={user_id}, lifecycle=pause-on-idle)` boots a fresh sandbox (~1–2s) → Registry row written.
- The sandbox has **no session yet** → DaemonClient starts the daemon **without `-c`** (fresh session). `_has_session()` checks the disk to decide `-c` or not — by fact, not guess.

---

## 4. Concurrent users — how isolation holds

3 users hit `/messages` at the same time:
- Each gets its own thread (FastAPI threadpool), its own `try_claim` (different keys, no conflict), its own sandbox, its own `DaemonPipe` (keyed by `sandbox_id` in `_pipes`).
- User A's events come only from A's pipe; A's `/steer` reaches only A's daemon. **They never touch each other** — this is verified (multiuser test, 14/14).
- Shared in-RAM state (`_pipes` dict, `_busy` set) is guarded by locks. **The DB no longer needs a manual lock** — the Postgres pool hands each thread its own connection (the Phase-3 shared-connection hazard is gone).
- Isolation rests on: VM boundary (separate sandbox) + `sandbox_id`-keyed pipe + per-user slot.

---

## 5. Mid-turn control — steer & abort

While a turn is streaming, the frontend can send:

**Steer** — `POST /users/rohan/steer { "message": "Tesco skip karo" }`
- TurnRunner finds rohan's live sandbox via `_active["rohan"]` → sends `{"type":"steer",...}` over that pipe.
- Inside pi: the message goes into a **steering queue**. pi lets the **current tool finish**, then injects the message before the next LLM call — so the agent adjusts its plan mid-turn. (Not an interrupt: if a 30s bash is running, the steer lands after it finishes.)
- No turn active for that user → **409** (nothing to steer).

**Abort** — `POST /users/rohan/abort`
- Sends `{"type":"abort"}` → pi stops the turn cleanly (`stopReason: aborted`), daemon stays alive.

---

## 6. Failure paths — how the system self-heals

| What breaks | How it's detected | What happens |
|---|---|---|
| **Daemon crashes** (OOM, bug) | Next `ensure_running` probe fails (or `pipe.exited` set) | `pkill -x pi` → restart with `-c` → session continuity intact (file survived) |
| **Sandbox paused mid-idle** | Next request's `connect` resumes it | Daemon is dead after resume → Step 4 restarts it with `-c` (normal, not an error) |
| **Sandbox reaped/killed by E2B** | `Sandbox.connect` raises `SandboxNotFoundException` | SandboxManager catches → creates fresh → **writes the Postgres `log` back into it** (`restore_session`, before daemon start) → `pi -c` continues → history survives → updates Registry row |
| **Turn runs away** (tool loop) | 20-min watchdog in TurnRunner | `abort` + `turn_error` event; work so far is in the session file |
| **Backend redeploys mid-turn** | PTYs die → daemons get EOF | Postgres persists (separate service); RAM is empty on restart but the next request rebuilds everything: `sessions` row → `Sandbox.connect` → `ensure_running` restart-with-`-c` |
| **`probe` on a dead daemon** | `send` to a gone PID raises | Caught → treated as "not alive" → restart (never crashes the request) |

The through-line: **the session JSONL on the sandbox disk is the anchor.** Anything in RAM (pipes, slots) can be lost and rebuilt; the conversation survives because it's a file, continued with `-c`.

---

## 7. Two hard-won facts baked into the code

From the spike + diagnostics (docs/spike-notes.md):
1. **The daemon's process name is `pi`** (pi rewrites its argv), not any `--mode rpc` string → kill with `pkill -x pi`, find with `pgrep -x pi`.
2. **pi does NOT survive sandbox pause/resume** → the resume path is *always* restart-with-`-c`; never try to reconnect an old PTY (`pty.connect` even succeeds on a dead PTY — a trap).

---

## 8. Deployment constraint — ONE backend instance

The live daemon handles (`_pipes`), turn slots (`_busy`), and active-turn map (`_active`)
live in **backend RAM**, tied to specific sandboxes/PTYs — per process. If Railway ran 2
instances, rohan's SSE stream could be on instance-1 while his `/steer` lands on instance-2
(no pipe for him → steer fails). So **1 instance** for v1 (fine for 5-10 users). Postgres is
shared and durable; the in-RAM daemon state is not — that's the single-instance constraint.
Multi-instance (sticky routing / shared daemon registry) is a future item, not needed now.

## 9. Status

Built & tested: Phases 0–3, Postgres migration, disaster restore, **and the Phase 4 outreach
path** (submit-batch skill → collector → pending_batches → approval endpoints → ZeptoMail
send with SEND_OVERRIDE_TO safety net → feedback turn). The agent researches + drafts;
a human approves; the Backend sends; the result returns to the agent. The agent still has
**no send capability** (no tool, no ZeptoMail creds, no egress to it) — sending is the Backend's.

Not built yet:
- **Ops (Phase 5)** — packaging (requirements.txt, Python pin, start cmd), secrets in Railway
  vars, structured logs, per-user cost report, Railway deploy (single instance), egress allowlist tighten.
- **Deferred** (design settled, triggers noted): verifier/monitor + stage evals (+ the in-turn
  `submit_batch` TS extension comes with these), scoped tokens, web-search allowlist, real frontend.

---

## 9. One-paragraph mental model

> A request authenticates by token, claims the user's single turn slot (else 409), then connects-or-creates that user's private E2B sandbox and makes sure a `pi` daemon is running inside it with the user's secrets. The prompt goes over a PTY into the daemon; pi drives the LLM loop *inside the sandbox*, writing every event to a session file as it goes, while the backend relays those events to the frontend as SSE and keeps the sandbox awake. When the turn ends, the slot frees and the full session log is written into the user's `sessions` row in Postgres. Different users are fully parallel and isolated by separate VMs; mid-turn the user can steer or abort. If anything in memory dies, it's rebuilt from Postgres + the session file with `-c`. The whole thing is one backend process (one instance) talking to N per-user sandboxes, with Postgres as the durable memory.
