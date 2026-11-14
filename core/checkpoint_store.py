"""Opt-in SQLite checkpoints for resumable local workflows.

Checkpoint files may contain step outputs. Keep them local and private; callers
must opt in by supplying a store. Never persist credentials or secret-bearing
provider payloads in workflow outputs.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


class WorkflowCheckpointStore:
    """Persist workflow step state in a local SQLite database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                    workflow_id TEXT NOT NULL,
                    graph_fingerprint TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_json TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (workflow_id, step_id)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def load(self, workflow_id: str, fingerprint: str) -> dict[str, dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT graph_fingerprint, step_id, status, output_json, error "
                "FROM workflow_checkpoints WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchall()
        if not rows:
            return {}
        if any(row[0] != fingerprint for row in rows):
            raise ValueError(
                f"Checkpoint graph mismatch for workflow {workflow_id!r}; "
                "use a new workflow_id rather than reusing stale results."
            )
        restored: dict[str, dict[str, Any]] = {}
        for _, step_id, status, output_json, error in rows:
            output = json.loads(output_json) if output_json is not None else None
            # A process may have died after a step began. Never silently rerun
            # an operation whose side effects are unknown.
            if status == "running":
                status = "interrupted"
                error = "Process stopped while this step was running; manual review required."
            restored[step_id] = {"status": status, "output": output, "error": error}
        return restored

    def save(
        self, workflow_id: str, fingerprint: str, step_id: str,
        status: str, output: Any = None, error: str | None = None,
    ) -> None:
        output_json = json.dumps(output, ensure_ascii=False, allow_nan=False) if output is not None else None
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO workflow_checkpoints
                   (workflow_id, graph_fingerprint, step_id, status, output_json, error, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(workflow_id, step_id) DO UPDATE SET
                     graph_fingerprint=excluded.graph_fingerprint,
                     status=excluded.status, output_json=excluded.output_json,
                     error=excluded.error, updated_at=CURRENT_TIMESTAMP""",
                (workflow_id, fingerprint, step_id, status, output_json, error),
            )
