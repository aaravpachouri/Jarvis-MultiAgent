"""Durable mission lifecycle storage with SQLite and hosted PostgreSQL backends.

SQLite remains the default for local installs. Set JARVIS_DATABASE_URL to use a
shared PostgreSQL database in hosted deployments. The PostgreSQL implementation
stores records only in the private schema and never exposes credentials to clients.
Unfinished jobs are marked interrupted on restart; they are never replayed.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Mapping


RESTART_ERROR = (
    "Backend restarted before this mission completed. "
    "Review its history before rerunning."
)


class MissionStore:
    """Thread-safe mission store with interchangeable local and hosted backends."""

    TERMINAL_STATES = (
        "succeeded", "failed", "partial", "timed_out", "cancelled", "interrupted",
    )

    def __init__(
        self,
        path: str | Path = "data/jarvis_missions.sqlite3",
        *,
        max_retained: int = 200,
        database_url: str | None = None,
    ) -> None:
        self.max_retained = max(1, int(max_retained))
        self._lock = RLock()
        self.database_url = (
            database_url or os.environ.get("JARVIS_DATABASE_URL", "")
        ).strip()
        self.backend = "postgres" if self.database_url else "sqlite"
        if self.backend == "postgres":
            self.path = None
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "JARVIS_DATABASE_URL is configured but the PostgreSQL driver "
                    "is missing. Install the hosted extra: pip install -e '.[hosted]'."
                ) from exc
            self._psycopg = psycopg
            self._dict_row = dict_row
            self._initialize_postgres()
        else:
            self.path = Path(path).expanduser()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect_sqlite() as connection:
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
                pass

    def _connect_sqlite(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _connect_postgres(self):
        return self._psycopg.connect(
            self.database_url,
            connect_timeout=8,
            row_factory=self._dict_row,
            application_name="jarvis-multiagent",
            options="-c search_path=jarvis_private",
        )

    def _initialize_postgres(self) -> None:
        # Schema and grants are provisioned by the Supabase migration, not by
        # runtime code. The backend must not have DDL privileges.
        with self._connect_postgres() as connection:
            connection.execute("SELECT 1 FROM jarvis_missions LIMIT 0")

    @staticmethod
    def _decode(row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result_json = row["result_json"]
        if isinstance(result_json, str):
            result_json = json.loads(result_json) if result_json else None
        return {
            "task_id": row["task_id"],
            "agent_id": row["agent_id"],
            "status": row["status"],
            "prompt": row["prompt"],
            "accepted_at": row["accepted_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "result": result_json,
            "error": row["error"],
        }

    def _prune_postgres_locked(self, connection) -> None:
        connection.execute("""
            DELETE FROM mission_jobs
            WHERE task_id IN (
                SELECT task_id FROM mission_jobs
                WHERE status = ANY(%s)
                ORDER BY updated_at DESC, task_id DESC
                OFFSET %s
            )
        """, (list(self.TERMINAL_STATES), self.max_retained))

    def upsert(self, job: Mapping[str, Any]) -> None:
        task_id = str(job.get("task_id") or "").strip()
        agent_id = str(job.get("agent_id") or "").strip()
        status = str(job.get("status") or "").strip().lower()
        if not task_id or not agent_id or not status:
            raise ValueError("Mission records require task_id, agent_id and status.")
        result = job.get("result")
        result_json = (
            json.dumps(result, ensure_ascii=False, default=str)
            if result is not None else None
        )
        values = (
            task_id, agent_id, status, str(job.get("prompt") or "")[:500],
            job.get("accepted_at"), job.get("started_at"), job.get("finished_at"),
            result_json, str(job.get("error") or "") or None,
        )
        with self._lock:
            if self.backend == "postgres":
                with self._connect_postgres() as connection:
                    connection.execute("""
                        INSERT INTO mission_jobs (
                            task_id, agent_id, status, prompt, accepted_at, started_at,
                            finished_at, result_json, error, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, now())
                        ON CONFLICT(task_id) DO UPDATE SET
                            agent_id=excluded.agent_id,
                            status=excluded.status,
                            prompt=excluded.prompt,
                            accepted_at=excluded.accepted_at,
                            started_at=excluded.started_at,
                            finished_at=excluded.finished_at,
                            result_json=excluded.result_json,
                            error=excluded.error,
                            updated_at=now()
                    """, values)
                    self._prune_postgres_locked(connection)
            else:
                with self._connect_sqlite() as connection:
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
                    """, values)
                    self._prune_sqlite_locked(connection)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self.backend == "postgres":
                with self._connect_postgres() as connection:
                    row = connection.execute(
                        "SELECT * FROM mission_jobs WHERE task_id = %s",
                        (str(task_id),),
                    ).fetchone()
            else:
                with self._connect_sqlite() as connection:
                    row = connection.execute(
                        "SELECT * FROM mission_jobs WHERE task_id = ?",
                        (str(task_id),),
                    ).fetchone()
        return self._decode(row)

    def list_recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        retention_window = self.max_retained + 64
        count = retention_window if limit is None else max(0, min(int(limit), retention_window))
        with self._lock:
            if self.backend == "postgres":
                with self._connect_postgres() as connection:
                    rows = connection.execute(
                        "SELECT * FROM mission_jobs ORDER BY accepted_at DESC NULLS LAST, "
                        "updated_at DESC, task_id DESC LIMIT %s",
                        (count,),
                    ).fetchall()
            else:
                with self._connect_sqlite() as connection:
                    rows = connection.execute(
                        "SELECT * FROM mission_jobs ORDER BY accepted_at DESC, rowid DESC LIMIT ?",
                        (count,),
                    ).fetchall()
        return [item for row in rows if (item := self._decode(row)) is not None]

    def recover_interrupted(self) -> list[dict[str, Any]]:
        """Mark unfinished jobs interrupted and return the recovered records."""
        with self._lock:
            if self.backend == "postgres":
                with self._connect_postgres() as connection:
                    connection.execute("""
                        UPDATE mission_jobs SET status='interrupted', error=%s, updated_at=now()
                        WHERE status IN ('queued', 'running')
                    """, (RESTART_ERROR,))
                    rows = connection.execute(
                        "SELECT * FROM mission_jobs ORDER BY accepted_at DESC NULLS LAST, "
                        "updated_at DESC LIMIT %s",
                        (self.max_retained + 64,),
                    ).fetchall()
                    self._prune_postgres_locked(connection)
            else:
                with self._connect_sqlite() as connection:
                    connection.execute("""
                        UPDATE mission_jobs
                        SET status='interrupted', error=?, updated_at=CURRENT_TIMESTAMP
                        WHERE status IN ('queued', 'running')
                    """, (RESTART_ERROR,))
                    rows = connection.execute(
                        "SELECT * FROM mission_jobs ORDER BY accepted_at DESC, rowid DESC LIMIT ?",
                        (self.max_retained + 64,),
                    ).fetchall()
                    self._prune_sqlite_locked(connection)
        return [item for row in rows if (item := self._decode(row)) is not None]

    def delete(self, task_id: str) -> None:
        with self._lock:
            if self.backend == "postgres":
                with self._connect_postgres() as connection:
                    connection.execute("DELETE FROM mission_jobs WHERE task_id = %s", (str(task_id),))
            else:
                with self._connect_sqlite() as connection:
                    connection.execute("DELETE FROM mission_jobs WHERE task_id = ?", (str(task_id),))

    def _prune_sqlite_locked(self, connection: sqlite3.Connection) -> None:
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

    def health(self) -> dict[str, Any]:
        """Return backend identity and a live read-only connection check."""
        try:
            with self._lock:
                if self.backend == "postgres":
                    with self._connect_postgres() as connection:
                        connection.execute("SELECT 1").fetchone()
                else:
                    with self._connect_sqlite() as connection:
                        connection.execute("SELECT 1").fetchone()
            return {"ok": True, "backend": self.backend}
        except Exception as exc:
            return {
                "ok": False,
                "backend": self.backend,
                "error_type": type(exc).__name__,
            }
