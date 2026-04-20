"""FastAPI entrypoint for the BETTER DISCOVERY backend.

Run (dev):
    python -m uvicorn app.main:app --reload --port 8765
Run (sidecar, picks a random port from Rust):
    python -m uvicorn app.main:app --host 127.0.0.1 --port <port>
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importing paths has the side effect of prepending MONTE CARLO/src to sys.path
# and creating userdata/. Do this before any bridge import.
from . import paths  # noqa: F401
from .config import CONFIG
from .routers import health

app = FastAPI(
    title="BETTER DISCOVERY backend",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

if CONFIG.dev_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[CONFIG.dev_origin, "tauri://localhost", "https://tauri.localhost"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health.router, tags=["health"])


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=CONFIG.host,
        port=CONFIG.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
