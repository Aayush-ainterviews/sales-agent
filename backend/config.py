"""
Every tunable and every secret in one place.

Decisions this file encodes (docs/architecture-decisions.md):
- Q11: secrets are read here and handed to the daemon at start time; they never
  enter the template and never touch Sandbox.create().
- Q12: platform tokens are shared operator tokens today. secrets_for_user() takes
  a user_id anyway, so per-user scoped tokens (future item) drop in without a refactor.
- Q16: 15 min idle-pause, bumped every 5 min while a turn streams; 20 min watchdog.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent

# --- E2B / template -------------------------------------------------------
TEMPLATE_ALIAS = "sales-agent-v2"   # v2: submit-batch skill + /home/user/outbox (Phase 4)
TEMPLATE_VERSION = "v2"          # stamped per sandbox in the registry (Q14 interim)
SANDBOX_CWD = "/home/user"       # pi stores sessions per-cwd; `-c` depends on this being constant

# --- time policies (seconds) ---------------------------------------------
IDLE_PAUSE = 15 * 60             # sandbox auto-pauses after this much silence
BUMP_INTERVAL = 5 * 60           # while a turn streams, push the countdown back this often
TURN_WATCHDOG = 20 * 60          # abort a turn that runs longer than this
STALE_CLAIM = TURN_WATCHDOG + 5 * 60   # a busy-slot older than this is treated as dead (client disconnected mid-turn) and cleared
PROBE_TIMEOUT = 20               # daemon must answer get_state within this
HEARTBEAT_INTERVAL = 15          # SSE keepalive when a turn is idle (Railway proxy would cut the stream)

# --- storage --------------------------------------------------------------
# Postgres (Railway in prod, local Docker in dev). The session JSONL log lives in
# a column on the `sessions` table — no separate object store (R2/S3 eliminated).
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:pass@localhost:5432/postgres")

# --- secrets --------------------------------------------------------------
# names the skills expect inside the sandbox; see template/pi-config/skills/*
_SECRET_NAMES = (
    "GEMINI_API_KEY",     # pi's LLM calls
    "ORIGAMI_API_KEY",    # origami-enrichment skill
    "APOLLO_API_KEY",     # apollo-enrichment skill
    "APIFY_TOKEN",        # apify skill
)

# backend-only send credentials (never reach the sandbox, Q21) — required to send,
# checked at startup so a misconfigured deploy fails loudly, not mid-approval
_SEND_SECRETS = ("ZEPTOMAIL_API_KEY", "ZEPTOMAIL_FROM_EMAIL")
# deliberately absent: ZEPTOMAIL_* — the agent cannot send (Q9). Sending happens
# in the backend's send executor (Phase 4), with its own credentials.


def secrets_for_user(user_id: str) -> dict[str, str]:
    """Env injected into this user's pi daemon at start. Shared values today (Q12)."""
    return {name: os.environ[name] for name in _SECRET_NAMES if os.environ.get(name)}


# --- users / auth (Phase 3) ----------------------------------------------
# Internal users: bearer token -> user_id. Set via env USER_TOKENS as a
# comma-separated "token:user_id" list, e.g. USER_TOKENS="tok_rohan:rohan,tok_priya:priya".
# Real auth (SSO/JWT) is a frontend-phase concern; this is enough for internal use.
def cors_origins() -> list[str]:
    """Allowed browser origins for the frontend. Comma-separated CORS_ORIGINS env,
    default '*' (fine for a bearer-token API — no cookies). Set the real frontend
    origin(s) in prod, e.g. CORS_ORIGINS="https://app.example.com"."""
    raw = os.environ.get("CORS_ORIGINS", "*").strip()
    return [o.strip() for o in raw.split(",") if o.strip()] or ["*"]


def user_tokens() -> dict[str, str]:
    raw = os.environ.get("USER_TOKENS", "")
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            tok, uid = pair.split(":", 1)
            out[tok.strip()] = uid.strip()
    return out


def assert_secrets_present() -> None:
    """Fail loudly at startup rather than mysteriously mid-turn."""
    missing = [n for n in _SECRET_NAMES if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"missing secrets in environment/.env: {', '.join(missing)}")
    if not os.environ.get("E2B_API_KEY"):
        raise RuntimeError("missing E2B_API_KEY in environment/.env")
    if not DATABASE_URL:
        raise RuntimeError("missing DATABASE_URL in environment/.env")
    send_missing = [n for n in _SEND_SECRETS if not os.environ.get(n)]
    if send_missing:
        raise RuntimeError(f"missing send credentials in environment/.env: {', '.join(send_missing)}")
