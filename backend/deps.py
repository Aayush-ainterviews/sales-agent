"""
Shared runtime singletons + dependencies for the API routers.

Kept out of app.py so every router can import the one TurnRunner without a circular
import. Importing this module (on the first router import) also configures logging and
fails loudly if a secret/DB is missing — before anything touches a sandbox. Runs once.
"""

from fastapi import Depends, HTTPException, Path

from backend import config, db, logging_setup
from backend.auth import require_user
from backend.registry import Registry
from backend.turn_runner import TurnRunner

logging_setup.configure()          # structured JSON logs (Phase 5)
config.assert_secrets_present()     # fail loudly at boot if any secret/DB is missing

# the single TurnRunner all routers share (owns registry, sandboxes, daemons, batches)
runner = TurnRunner(Registry(db.pool()))


def require_conversation(conversation_id: str = Path(...), user: str = Depends(require_user)) -> str:
    """Gate a conversation-scoped endpoint: the conversation must exist and belong to the
    authenticated user. Returns the validated conversation_id."""
    row = runner.registry.get(conversation_id)
    if row is None or row.user_id != user:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation_id
