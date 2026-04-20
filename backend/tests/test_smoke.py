"""Smoke tests for the backend skeleton."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["python"].startswith("3.")


def test_echo_roundtrip() -> None:
    resp = client.post("/smoke/echo", json={"payload": {"a": 1, "b": "two"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["echoed"] == {"a": 1, "b": "two"}
    assert body["size"] == 2
