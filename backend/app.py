"""
FastAPI surface: thin router assembly.

All lifecycle + concurrency logic lives in TurnRunner; `backend/deps.py` holds the shared
singleton (and, on import, configures logging + asserts secrets). Each endpoint group is
an APIRouter under `backend/routers/` — adding a feature means a new router file, not a
bigger app.py. Identity comes from the bearer token (auth.require_user), not the URL path.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import config, deps  # noqa: F401  (deps import = configure logging + assert secrets + build runner)
from backend.routers import admin, batches, files, turns

app = FastAPI(title="sales-ai-agent backend")

# let the browser frontend (a different origin) call this API + read the SSE stream
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


app.include_router(turns.router)
app.include_router(files.router)
app.include_router(batches.router)
app.include_router(admin.router)
