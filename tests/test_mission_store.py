from __future__ import annotations

import threading

from core.mission_store import MissionStore
from dashboard_server import DashboardApplication


def _job(task_id: str, status: str = "queued") -> dict:
    return {
        "task_id": task_id,
        "agent_id": "voice.jarvis",
        "status": status,
        "prompt": "A test mission",
        "accepted_at": "2026-11-17T10:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }


def test_mission_store_persists_job_and_result_across_reopen(tmp_path):
    path = tmp_path / "state" / "missions.sqlite3"
    store = MissionStore(path, max_retained=10)
    job = _job("task-1", "succeeded")
    job["result"] = {"status": "succeeded", "output": "complete"}
    job["finished_at"] = "2026-11-17T10:01:00+00:00"
    store.upsert(job)

    reopened = MissionStore(path, max_retained=10)
    assert reopened.get("task-1") == job
    assert reopened.list_recent(5)[0]["result"] == job["result"]


def test_mission_store_marks_queued_and_running_jobs_interrupted_on_restart(tmp_path):
    path = tmp_path / "missions.sqlite3"
    store = MissionStore(path, max_retained=10)
    store.upsert(_job("queued-task", "queued"))
    running = _job("running-task", "running")
    running["started_at"] = "2026-11-17T10:00:10+00:00"
    store.upsert(running)
    completed = _job("done-task", "succeeded")
    store.upsert(completed)

    recovered = MissionStore(path, max_retained=10).recover_interrupted()
    statuses = {item["task_id"]: item["status"] for item in recovered}
    assert statuses == {
        "queued-task": "interrupted",
        "running-task": "interrupted",
        "done-task": "succeeded",
    }
    assert "must be reviewed" not in (MissionStore(path).get("queued-task") or {}).get("error", "")
    assert "Backend restarted" in MissionStore(path).get("queued-task")["error"]


def test_dashboard_mission_status_reads_persisted_record_after_memory_loss(tmp_path):
    store = MissionStore(tmp_path / "missions.sqlite3")
    store.upsert(_job("persisted-task", "interrupted"))

    app = DashboardApplication.__new__(DashboardApplication)
    app.job_lock = threading.Lock()
    app.jobs = {}
    app.mission_store = store

    assert app.mission_status("persisted-task")["status"] == "interrupted"
    assert app.mission_status("missing-task") is None


def test_mission_store_retention_never_prunes_active_jobs(tmp_path):
    store = MissionStore(tmp_path / "missions.sqlite3", max_retained=2)
    store.upsert(_job("active-1", "running"))
    store.upsert(_job("done-1", "succeeded"))
    store.upsert(_job("done-2", "failed"))
    store.upsert(_job("done-3", "succeeded"))

    ids = {item["task_id"] for item in store.list_recent(10)}
    assert "active-1" in ids
    assert len(ids) == 3


def test_recent_mission_history_returns_bounded_summaries(tmp_path):
    store = MissionStore(tmp_path / "missions.sqlite3")
    job = _job("history-task", "succeeded")
    job["result"] = {"summary": "Mission completed", "output": "Long output " * 200}
    store.upsert(job)

    app = DashboardApplication.__new__(DashboardApplication)
    app.mission_store = store
    history = app.recent_missions(10)

    assert len(history) == 1
    assert history[0]["task_id"] == "history-task"
    assert history[0]["summary"] == "Mission completed"
    assert len(history[0]["output_preview"]) <= 1000


def test_mission_store_health_reports_sqlite_backend(tmp_path):
    store = MissionStore(tmp_path / "health.sqlite3")
    assert store.health() == {"ok": True, "backend": "sqlite"}


def test_mission_store_rejects_malformed_records(tmp_path):
    store = MissionStore(tmp_path / "validation.sqlite3")
    try:
        store.upsert({"task_id": "missing-agent", "status": "queued"})
    except ValueError as exc:
        assert "task_id, agent_id and status" in str(exc)
    else:
        raise AssertionError("Expected invalid mission record to be rejected")


def test_mission_store_postgres_configuration_requires_driver(monkeypatch, tmp_path):
    import builtins
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError("test driver unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    try:
        MissionStore(tmp_path / "unused.sqlite3", database_url="postgresql://invalid")
    except RuntimeError as exc:
        assert "hosted extra" in str(exc)
    else:
        raise AssertionError("Expected clear missing-driver error")
