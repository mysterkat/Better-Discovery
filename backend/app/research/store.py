"""SQLite experiment ledger. Report artifacts remain immutable on disk."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..paths import DEFAULT_RESEARCH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path or DEFAULT_RESEARCH / "experiments.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_db(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    strategy_fingerprint TEXT,
                    parent_id TEXT,
                    dataset_role TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experiments_created
                    ON experiments(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_experiments_strategy
                    ON experiments(strategy_fingerprint);
                """
            )
            columns = {
                row["name"] for row in con.execute("PRAGMA table_info(experiments)")
            }
            if "dataset_role" not in columns:
                con.execute("ALTER TABLE experiments ADD COLUMN dataset_role TEXT")

    def create(
        self,
        kind: str,
        request: dict[str, Any],
        strategy_fingerprint: str | None = None,
        parent_id: str | None = None,
        dataset_role: str | None = None,
    ) -> str:
        experiment_id = uuid.uuid4().hex
        now = _now()
        with self.connect() as con:
            con.execute(
                """INSERT INTO experiments
                   (id, kind, status, strategy_fingerprint, parent_id, dataset_role,
                    request_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    experiment_id, kind, "running", strategy_fingerprint, parent_id,
                    dataset_role,
                    json.dumps(request, sort_keys=True, default=str), now, now,
                ),
            )
        return experiment_id

    def finish(self, experiment_id: str, result: dict[str, Any]) -> None:
        self._update(experiment_id, "completed", result=result)

    def fail(self, experiment_id: str, error: str) -> None:
        self._update(experiment_id, "failed", error=error)

    def _update(
        self,
        experiment_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as con:
            cur = con.execute(
                """UPDATE experiments SET status=?, result_json=?, error=?, updated_at=?
                   WHERE id=?""",
                (
                    status,
                    json.dumps(result, sort_keys=True, default=str) if result else None,
                    error, _now(), experiment_id,
                ),
            )
            if cur.rowcount != 1:
                raise KeyError(f"unknown experiment: {experiment_id}")

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["request"] = json.loads(value.pop("request_json"))
        raw_result = value.pop("result_json")
        value["result"] = json.loads(raw_result) if raw_result else None
        return value

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def has_completed_lockbox(self, strategy_fingerprint: str, kind: str | None = None) -> bool:
        with self.connect() as con:
            query = """SELECT 1 FROM experiments
                       WHERE strategy_fingerprint=? AND dataset_role='lockbox'
                         AND status='completed'"""
            values: list[str] = [strategy_fingerprint]
            if kind is not None:
                query += " AND kind=?"
                values.append(kind)
            row = con.execute(query + " LIMIT 1", values).fetchone()
        return row is not None
