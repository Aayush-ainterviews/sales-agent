# Sales AI Agent

A multi-user sales outreach agent: each user gets an isolated pi agent running inside their own E2B sandbox, orchestrated by a backend API. This glossary is the canonical language for the system.

## Language

### Product & ICP

**ICP (Ideal Customer Profile)**:
The standing, always-on definition of what this product targets: companies hiring for their **physical retail stores**. Every agent run is auto-scoped to it — the user never restates it, and a request may narrow *within* the ICP, never outside. Encoded in [GOAL.md](GOAL.md) "Product & who we target"; enforced as a hard filter at both the Apify search stage and the filter stage.
_Avoid_: "relevance" as a per-query flag, "the user's criteria"

**Lead**:
A relevant company (runs physical retail stores + shows a store-hiring signal) **plus** the retail-store-hiring decision-maker to reach it, with a verified email. A bare company or job list is not a finished Lead.

**Retail-store hiring signal**:
A store-level, customer-facing retail opening — the kind a physical store hires for. NOT corporate-HQ, tech/engineering, warehouse-only, or unrelated roles. This is the "Job" leg of the ICP.
_Avoid_: any job, any opening

**Retail-hiring decision-maker**:
The person who owns or influences a company's store hiring — the Sales Head, Store/Retail Head, or Recruiter/TA Head kind (and equivalents: regional/area retail HR, VP Retail Ops). The Origami enrichment target. Must **currently hold** that title **at that same company** — a past/former role, or the right title at a different company, does not count. NOT a random employee, a tech recruiter, or an unrelated executive.
_Avoid_: any contact, any recruiter, hiring manager (too generic), former/ex- title-holders

### Orchestration

**Backend**:
The service we own that sits between any frontend and E2B. It holds the E2B API key, maps users to sandboxes, and drives pi turns. The only component that talks to the E2B cloud API.
_Avoid_: server, orchestrator, middleware

**Frontend**:
Any user-facing surface (web app, bot) that talks to the Backend over HTTP. Never talks to E2B or pi directly.

### E2B

**Template**:
A frozen, pre-built sandbox image (pi installed, skills/extensions/settings copied in). Built once with `e2b template build`; nothing is installed at sandbox-creation time.
_Avoid_: image, container config

**Sandbox**:
One running (or paused) E2B micro-VM belonging to exactly one user. The hard isolation boundary: filesystem, credentials, and network egress are per-sandbox.
_Avoid_: container, VM, environment

**Sandbox Registry**:
The Backend's DB table mapping `userId → sandboxId` (+ status, timestamps). The source of truth for which Sandbox belongs to whom; E2B metadata carries the same `userId` only as a debug label, never as the lookup path.

**Sandbox Handle**:
The object returned by `Sandbox.create()` / `Sandbox.connect()`. Its existence means the sandbox is ready — liveness is not polled separately.

### Agent

**Pi**:
The agent harness process that runs *inside* a Sandbox. Owns the decision loop: builds context, calls the LLM, executes tool calls.

**Daemon**:
The long-lived `pi --mode rpc` process inside a Sandbox. Started on boot/resume, spoken to over a PTY pipe (JSONL on stdin/stdout), supervised by the Backend (health-ping; on silence: restart with `-c`).

**Turn**:
One user message → one `prompt` command to the Daemon → streamed events → final answer. The unit of agent work the Backend drives.

**Session Backup**:
An async copy of the Session JSONL pulled out of the sandbox after each Turn, stored by the Backend (S3/blob). Disaster-recovery and audit only — never the source of truth; the sandbox's file is.

### Outreach

**Draft Batch**:
The structured set of outreach messages (leads + copy + metadata) an agent Turn produces. The only artifact that crosses from Sandbox to Backend for sending.

**Send**:
Executed by the Backend via **ZeptoMail** (Zoho; `POST api.zeptomail.in/v1.1/email`, `Authorization: Zoho-enczapikey`), only after Approval. The agent has no send capability — no send tool, no ZeptoMail credentials, no egress route to it (the sandbox reaches only Gemini/Origami/Apify — research providers). Sending runs from the Backend.
_Avoid_: agent sends, sandbox sends, Origami sends (Origami is research, not send)

**Approval**:
A human decision on a Draft Batch, recorded by the Backend (who, when, what). Asynchronous — a Turn never blocks waiting for it.

**Stage Eval** *(planned, not yet built)*:
An automated evaluation of an agent's work at a pipeline stage, gating progression to the next stage. Runs in the Backend against Draft Batches and Session Backups.

**Session**:
Pi's persisted conversation state — a JSONL file on the sandbox's disk (`~/.pi/agent/sessions/`). Survives pause/resume; the reason a returning user's context continues.
