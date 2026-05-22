"""Library router round-trip tests.

Uses BD_USERDATA env-var to redirect all file I/O to a tmp directory so
there are no side-effects on the developer's userdata folder and tests
are hermetic and repeatable.

Happy-path: save → list → delete.
Also exercises the /attach and /trades_csv endpoints.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Stand up the app with a fresh tmp userdata dir."""
    ud = tmp_path_factory.mktemp("userdata")
    os.environ["BD_USERDATA"] = str(ud)

    # CRITICAL: if another test file (e.g. test_smoke.py) already imported
    # app.main, paths.USER_DATA has been cached to the dev userdata folder
    # before BD_USERDATA was set.  Force-reload the path module + bridges so
    # USER_DATA gets re-resolved to our tmp dir.  Without this, library_save
    # rejects paths under our tmp /discovery/ tree as "outside output".
    import importlib  # noqa: PLC0415
    import sys  # noqa: PLC0415
    for mod_name in list(sys.modules):
        if mod_name.startswith("app.") or mod_name == "app":
            del sys.modules[mod_name]

    # Now reimport with the env var in effect.
    from app.main import app  # noqa: PLC0415
    return TestClient(app)


@pytest.fixture(scope="module")
def disc_set_file(tmp_path_factory):
    """Create a synthetic .set file inside the discovery output folder.

    The library_save endpoint refuses paths outside DEFAULT_DISC_OUTPUT, so
    we write the file into the BD_USERDATA/discovery/ tree (which paths.py
    auto-creates on import).
    """
    ud = Path(os.environ["BD_USERDATA"])
    disc = ud / "discovery" / "seed_1"
    disc.mkdir(parents=True, exist_ok=True)
    set_path = disc / "pattern_test_C1_LONG_seed1.set"
    set_path.write_text("[BetterDiscovery]\nversion=0.8.0\n", encoding="utf-8")

    trades_path = disc / "cluster_1_LONG_seed1.csv"
    trades_path.write_text("time,profit\n2024-01-02,10.5\n2024-01-03,-2.0\n",
                            encoding="utf-8")
    return set_path


PATTERN_ID = "pattern_test_C1_LONG_seed1"
METADATA = {
    "pattern_id": PATTERN_ID,
    "direction": "LONG",
    "cluster": 1,
    "seed": 1,
    "win_rate": 0.65,
    "profit_factor": 1.8,
}


def test_save_to_library(client, disc_set_file):
    resp = client.post("/library/save", json={
        "pattern_id": PATTERN_ID,
        "set_file": str(disc_set_file),
        "metadata": METADATA,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # v0.8.0 schema: response is { entry: LibraryEntry, duplicate: bool }
    assert body["entry"]["pattern_id"] == PATTERN_ID
    assert body["duplicate"] is False


def test_save_idempotent(client, disc_set_file):
    resp = client.post("/library/save", json={
        "pattern_id": PATTERN_ID,
        "set_file": str(disc_set_file),
        "metadata": METADATA,
    })
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is True


def test_list_contains_saved(client, disc_set_file):
    resp = client.get("/library/list")
    assert resp.status_code == 200
    entries = resp.json()
    ids = [e["pattern_id"] for e in entries]
    assert PATTERN_ID in ids


def test_trades_csv_served(client, disc_set_file):
    resp = client.get(f"/library/{PATTERN_ID}/trades_csv")
    assert resp.status_code == 200
    assert "profit" in resp.text


def test_mt5_csv_missing_returns_404(client, disc_set_file):
    resp = client.get(f"/library/{PATTERN_ID}/mt5_csv")
    assert resp.status_code == 404


def test_attach_mt5_csv(client, disc_set_file):
    payload = "time,profit\n2024-01-10,50.0\n"
    encoded = base64.b64encode(payload.encode()).decode()
    # v0.8.0 schema: { pattern_id, kind, content_b64 }
    resp = client.post("/library/attach", json={
        "pattern_id": PATTERN_ID,
        "kind": "mt5_csv",
        "content_b64": encoded,
    })
    assert resp.status_code == 200


def test_mt5_csv_served_after_attach(client, disc_set_file):
    resp = client.get(f"/library/{PATTERN_ID}/mt5_csv")
    assert resp.status_code == 200
    assert "50.0" in resp.text


def test_delete_from_library(client, disc_set_file):
    resp = client.delete(f"/library/{PATTERN_ID}")
    assert resp.status_code == 200


def test_list_empty_after_delete(client, disc_set_file):
    resp = client.get("/library/list")
    assert resp.status_code == 200
    ids = [e["pattern_id"] for e in resp.json()]
    assert PATTERN_ID not in ids
