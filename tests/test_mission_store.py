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


def test_supabase_edge_mission_store_uses_authenticated_gateway(tmp_path, monkeypatch):
    import json

    calls = []
    job = _job("edge-task", "succeeded")
    job["result"] = {"summary": "Stored remotely"}

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, *, json, headers, timeout):
        payload = json
        calls.append((url, payload, headers, timeout))
        operation = payload["operation"]
        if operation == "get":
            return FakeResponse({"ok": True, "record": {
                **{key: value for key, value in job.items() if key != "result"},
                "result": job["result"],
            }})
        if operation == "list":
            return FakeResponse({"ok": True, "records": [{
                **{key: value for key, value in job.items() if key != "result"},
                "result": job["result"],
            }]})
        if operation == "recover":
            return FakeResponse({"ok": True, "records": []})
        return FakeResponse({"ok": True})

    monkeypatch.setattr("core.mission_store.requests.post", fake_post)
    store = MissionStore(
        tmp_path / "unused.sqlite3",
        supabase_function_url="https://example.supabase.co/functions/v1/jarvis-mission-store",
        supabase_function_token="a" * 64,
    )

    store.upsert(job)
    assert store.backend == "supabase-edge"
    assert store.path is None
    assert store.get("edge-task")["result"] == {"summary": "Stored remotely"}
    assert store.list_recent(5)[0]["task_id"] == "edge-task"
    assert store.recover_interrupted() == []
    store.delete("edge-task")
    assert store.health() == {"ok": True, "backend": "supabase-edge"}

    assert [call[1]["operation"] for call in calls] == [
        "upsert", "get", "list", "recover", "delete", "health",
    ]
    assert calls[0][1]["job"]["result"] == {"summary": "Stored remotely"}
    assert calls[0][2]["Authorization"] == f"Bearer {'a' * 64}"
    assert calls[0][3] == (5, 20)


def test_supabase_edge_mission_store_requires_both_credentials(tmp_path):
    try:
        MissionStore(
            tmp_path / "unused.sqlite3",
            supabase_function_url="https://example.supabase.co/functions/v1/jarvis-mission-store",
        )
    except RuntimeError as exc:
        assert "must be configured together" in str(exc)
    else:
        raise AssertionError("Expected incomplete Supabase configuration to fail")


def test_supabase_edge_mission_store_surfaces_gateway_failures_safely(tmp_path, monkeypatch):
    import requests

    class FakeResponse:
        status_code = 503

        def raise_for_status(self):
            raise requests.HTTPError("private provider response", response=self)

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("core.mission_store.requests.post", fake_post)
    store = MissionStore(
        tmp_path / "unused.sqlite3",
        supabase_function_url="https://example.supabase.co/functions/v1/jarvis-mission-store",
        supabase_function_token="b" * 64,
    )
    health = store.health()
    assert health == {
        "ok": False,
        "backend": "supabase-edge",
        "error_type": "RuntimeError",
    }
