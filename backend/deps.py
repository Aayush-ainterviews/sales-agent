"""
Shared runtime singletons for the API routers.

Kept out of app.py so every router can import the one TurnRunner without a circular
import. Importing this module (which happens on the first router import) also configures
logging and fails loudly if a secret/DB is missing — before anything touches a sandbox.
Runs exactly once.
"""

from backend import config, db, logging_setup
from backend.registry import Registry
from backend.turn_runner import TurnRunner

logging_setup.configure()          # structured JSON logs (Phase 5)
config.assert_secrets_present()     # fail loudly at boot if any secret/DB is missing

# the single TurnRunner all routers share (owns registry, sandboxes, daemons, batches)
runner = TurnRunner(Registry(db.pool()))
