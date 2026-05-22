"""FastAPI entrypoint for the BETTER DISCOVERY backend.

Run (dev):
    python -m uvicorn app.main:app --reload --port 8765
Run (sidecar, picks a random port from Rust):
    python -m uvicorn app.main:app --host 127.0.0.1 --port <port>
"""

from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# v1.1.5 — raise the per-process file-descriptor ceiling at startup so a
# multi-run discovery benchmark doesn't exhaust the default limit and crash
# every subsequent API call with `OSError [Errno 24] Too many open files`.
#
# Windows: the Python embedded CRT defaults to 512 stdio handles per process.
# Each mp.Pool worker, each matplotlib figure, each open CSV, and the FastAPI/
# uvicorn socket pool all consume from this pool.  Bumping to 8192 (the CRT
# maximum) gives ~16× headroom — comfortably enough for a full 4-config
# optimizer benchmark without restart.
#
# POSIX: resource.setrlimit raises the soft limit toward the hard cap; this
# is the equivalent fix on macOS / Linux dev installs.
#
# Either call is best-effort; a failure is logged but does NOT block startup.
def _raise_fd_limit() -> None:
    try:
        if sys.platform.startswith("win"):
            # CPython 3.4+ used to expose `msvcrt.setmaxstdio` but the
            # Universal-CRT builds (3.5+) dropped it.  The underlying C
            # symbol `_setmaxstdio` is still exported from `ucrtbase.dll`
            # — call it via ctypes.  Valid range: 512..8192.
            import ctypes
            ucrt = ctypes.CDLL("ucrtbase")
            ucrt._setmaxstdio.restype = ctypes.c_int
            ucrt._setmaxstdio.argtypes = [ctypes.c_int]
            new = ucrt._setmaxstdio(8192)
            if new == -1:
                print("[startup] _setmaxstdio(8192) returned -1", file=sys.stderr)
            else:
                print(f"[startup] CRT stdio limit raised to {new}", file=sys.stderr)
        else:
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            target = min(8192, hard)
            if target > soft:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                print(f"[startup] RLIMIT_NOFILE raised from {soft} to {target}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[startup] fd-limit bump skipped: {e}", file=sys.stderr)


_raise_fd_limit()


# Importing paths has the side effect of prepending toolkit to sys.path and
# creating all userdata subdirs. Must happen before any bridge import.
from . import paths  # noqa: F401, E402
from .config import CONFIG  # noqa: E402
from .routers import data, discovery, health, library, mc, mql, settings as settings_router  # noqa: E402

app = FastAPI(
    on_startup=[lambda: paths.validate_paths()],
    title="BETTER DISCOVERY backend",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

if CONFIG.dev_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[*CONFIG.dev_origins, *CONFIG.tauri_origins],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health.router, tags=["health"])
app.include_router(data.router, tags=["data"])
app.include_router(discovery.router, tags=["discovery"])
app.include_router(library.router, tags=["library"])
app.include_router(mc.router, tags=["mc"])
app.include_router(mql.router, tags=["mql"])
app.include_router(settings_router.router, tags=["settings"])


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
