"""Durable SQLite storage for dashboard mission lifecycle records.

The database path is configurable so local installations can use the project data
folder and hosted deployments can point it at an attached persistent volume.
Running jobs are marked interrupted on process restart; they are never replayed
automatically because the previous step may have performed an external side effect.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


class MissionStore:
    """Thread-safe, process-restart-safe mission record store."""

    TERMINAL_STATES = (
        "succeeded", "failed", "partial", "timed_out", "cancelled", "interrupted",
    )

    def __init__(self, path: str | Path, *, max_retained: int = 200) -> None:
        self.path = Path(path).expanduser()
        self.max_retained = max(1, int(max_retained))
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS mission_jobs (
                    task_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL DEFAULT '',
                    accepted_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_mission_jobs_status_updated "
                "ON mission_jobs(status, updated_at)"
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            # Windows and managed volumes may not support POSIX permission modes.
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result_json = row["result_json"]
        return {
            "task_id": row["task_id"],
            "agent_id": row["agent_id"],
            "status": row["status"],
            "prompt": row["prompt"],
            "accepted_at": row["accepted_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "result": json.loads(result_json) if result_json else None,
            "error": row["error"],
        }

    def upsert(self, job: Mapping[str, Any]) -> None:
        task_id = str(job.get("task_id") or "").strip()
        agent_id = str(job.get("agent_id") or "").strip()
        status = str(job.get("status") or "").strip().lower()
        if not task_id or not agent_id or not status:
            raise ValueError("Mission records require task_id, agent_id and status.")
        result = job.get("result")
        result_json = (
            json.dumps(result, ensure_ascii=False, allow_nan=False, default=str)
            if result is not None else None
        )
        with self._lock, self._connect() as connection:
            connection.execute("""
                INSERT INTO mission_jobs (
                    task_id, agent_id, status, prompt, accepted_at, started_at,
                    finished_at, result_json, error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(task_id) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    status=excluded.status,
                    prompt=excluded.prompt,
                    accepted_at=excluded.accepted_at,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                task_id, agent_id, status, str(job.get("prompt") or "")[:500],
                job.get("accepted_at"), job.get("started_at"), job.get("finished_at"),
                result_json, str(job.get("error") or "") or None,
            ))
            self._prune_locked(connection)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mission_jobs WHERE task_id = ?", (str(task_id),)
            ).fetchone()
        return self._decode(row)

    def list_recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        count = self.max_retained if limit is None else max(0, min(int(limit), self.max_retained))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM mission_jobs ORDER BY accepted_at DESC, rowid DESC LIMIT ?",
                (count,),
            ).fetchall()
        return [item for row in rows if (item := self._decode(row)) is not None]

    def recover_interrupted(self) -> list[dict[str, Any]]:
        """Mark unfinished jobs interrupted and return the recovered records."""
        with self._lock, self._connect() as connection:
            connection.execute("""
                UPDATE mission_jobs
                SET status='interrupted',
                    error='Backend restarted before this mission completed. Review its history before rerunning.',
                    updated_at=CURRENT_TIMESTAMP
                WHERE status IN ('queued', 'running')
            """)
            rows = connection.execute(
                "SELECT * FROM mission_jobs ORDER BY accepted_at DESC, rowid DESC LIMIT ?",
                (self.max_retained,),
            ).fetchall()
            self._prune_locked(connection)
        return [item for row in rows if (item := self._decode(row)) is not None]

    def delete(self, task_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM mission_jobs WHERE task_id = ?", (str(task_id),))

    def _prune_locked(self, connection: sqlite3.Connection) -> None:
        placeholders = ",".join("?" for _ in self.TERMINAL_STATES)
        connection.execute(f"""
            DELETE FROM mission_jobs
            WHERE task_id IN (
                SELECT task_id FROM mission_jobs
                WHERE status IN ({placeholders})
                ORDER BY updated_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
            )
        """, (*self.TERMINAL_STATES, self.max_retained))
