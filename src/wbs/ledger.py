"""SQLite ledger: run history, idempotent step states, model usage/cost and human review records.

One ledger per workspace. SQLite (WAL) is safe for several worker processes on one machine;
for multi-machine workers point every machine at its own workspace or move this module to a
server database (see docs/deployment.md).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, command TEXT, args TEXT,
    started_at REAL, finished_at REAL, status TEXT);
CREATE TABLE IF NOT EXISTS steps (
    job_key TEXT, step TEXT, input_hash TEXT, status TEXT, attempts INTEGER DEFAULT 0,
    started_at REAL, finished_at REAL, output TEXT, error TEXT, run_id TEXT,
    PRIMARY KEY (job_key, step));
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT, batch_id TEXT, step TEXT,
    provider TEXT, model TEXT, request_id TEXT,
    input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER,
    images INTEGER, video_seconds REAL, cost_usd REAL, dry_run INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT, field TEXT, value TEXT,
    reviewer TEXT, note TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS usage_job ON usage(job_key);
CREATE INDEX IF NOT EXISTS usage_batch ON usage(batch_id);
CREATE INDEX IF NOT EXISTS reviews_job ON reviews(job_key);
"""


@dataclass
class StepRecord:
    job_key: str
    step: str
    input_hash: str | None
    status: str
    attempts: int
    output: dict[str, Any] | None
    error: str | None
    started_at: float | None
    finished_at: float | None


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- runs ---------------------------------------------------------------
    def start_run(self, command: str, args: dict[str, Any] | None = None) -> str:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?)",
                (run_id, command, json.dumps(args or {}, ensure_ascii=False, default=str), time.time(), None, "running"),
            )
        return run_id

    def finish_run(self, run_id: str, status: str) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE runs SET finished_at=?, status=? WHERE run_id=?", (time.time(), status, run_id))

    # --- steps --------------------------------------------------------------
    def get_step(self, job_key: str, step: str) -> StepRecord | None:
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM steps WHERE job_key=? AND step=?", (job_key, step)).fetchone()
        return self._step(row) if row else None

    def steps_for(self, job_key: str) -> list[StepRecord]:
        with self.tx() as conn:
            rows = conn.execute("SELECT * FROM steps WHERE job_key=? ORDER BY started_at", (job_key,)).fetchall()
        return [self._step(r) for r in rows]

    def begin_step(self, job_key: str, step: str, input_hash: str, run_id: str | None) -> int:
        with self.tx() as conn:
            row = conn.execute("SELECT attempts FROM steps WHERE job_key=? AND step=?", (job_key, step)).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            conn.execute(
                "INSERT OR REPLACE INTO steps (job_key, step, input_hash, status, attempts, started_at,"
                " finished_at, output, error, run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_key, step, input_hash, "running", attempts, time.time(), None, None, None, run_id),
            )
        return attempts

    def succeed_step(self, job_key: str, step: str, output: dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE steps SET status='succeeded', finished_at=?, output=?, error=NULL WHERE job_key=? AND step=?",
                (time.time(), json.dumps(output, ensure_ascii=False, default=str), job_key, step),
            )

    def fail_step(self, job_key: str, step: str, error: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE steps SET status='failed', finished_at=?, error=? WHERE job_key=? AND step=?",
                (time.time(), error[:4000], job_key, step),
            )

    def reset_step(self, job_key: str, step: str | None = None) -> None:
        with self.tx() as conn:
            if step is None:
                conn.execute("DELETE FROM steps WHERE job_key=?", (job_key,))
            else:
                conn.execute("DELETE FROM steps WHERE job_key=? AND step=?", (job_key, step))

    @staticmethod
    def _step(row: sqlite3.Row) -> StepRecord:
        return StepRecord(
            job_key=row["job_key"], step=row["step"], input_hash=row["input_hash"], status=row["status"],
            attempts=row["attempts"], output=json.loads(row["output"]) if row["output"] else None,
            error=row["error"], started_at=row["started_at"], finished_at=row["finished_at"],
        )

    # --- usage / cost -------------------------------------------------------
    def record_usage(self, *, job_key: str, batch_id: str | None, step: str, provider: str, model: str | None,
                     request_id: str | None, input_tokens: int = 0, cached_input_tokens: int = 0,
                     output_tokens: int = 0, images: int = 0, video_seconds: float = 0.0,
                     cost_usd: float | None = None, dry_run: bool = False) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO usage (job_key, batch_id, step, provider, model, request_id, input_tokens,"
                " cached_input_tokens, output_tokens, images, video_seconds, cost_usd, dry_run, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_key, batch_id, step, provider, model, request_id, input_tokens, cached_input_tokens,
                 output_tokens, images, video_seconds, cost_usd, int(dry_run), time.time()),
            )

    def spend_usd(self, *, job_key: str | None = None, batch_id: str | None = None) -> float:
        clauses, params = ["dry_run=0", "cost_usd IS NOT NULL"], []
        if job_key:
            clauses.append("job_key=?")
            params.append(job_key)
        if batch_id:
            clauses.append("batch_id=?")
            params.append(batch_id)
        with self.tx() as conn:
            row = conn.execute(f"SELECT COALESCE(SUM(cost_usd),0) AS s FROM usage WHERE {' AND '.join(clauses)}",
                               params).fetchone()
        return float(row["s"])

    def usage_rows(self, *, job_key: str | None = None, batch_id: str | None = None) -> list[dict[str, Any]]:
        clauses, params = ["1=1"], []
        if job_key:
            clauses.append("job_key=?")
            params.append(job_key)
        if batch_id:
            clauses.append("batch_id=?")
            params.append(batch_id)
        with self.tx() as conn:
            rows = conn.execute(f"SELECT * FROM usage WHERE {' AND '.join(clauses)} ORDER BY id", params).fetchall()
        return [dict(r) for r in rows]

    # --- human reviews ------------------------------------------------------
    def add_review(self, job_key: str, field: str, value: str, reviewer: str, note: str = "") -> None:
        with self.tx() as conn:
            conn.execute("INSERT INTO reviews (job_key, field, value, reviewer, note, created_at) VALUES (?,?,?,?,?,?)",
                         (job_key, field, value, reviewer, note, time.time()))

    def reviews(self, job_key: str) -> dict[str, dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute("SELECT * FROM reviews WHERE job_key=? ORDER BY id", (job_key,)).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest[row["field"]] = {"value": row["value"], "reviewer": row["reviewer"], "note": row["note"],
                                    "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(row["created_at"]))}
        return latest
