from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import dashboard_server
from core.contracts import TaskResult, TaskStatus
from core.runtime import AgentRuntime


def _result(task_id: str) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        agent_id="search.scout",
        status=TaskStatus.SUCCEEDED,
        output={"results": []},
        summary="done",
    )


def test_runtime_task_result_cache_evicts_oldest_terminal_result() -> None:
    runtime = AgentRuntime()
    runtime.MAX_RETAINED_TASK_RESULTS = 2
    runtime._store_task_result(_result("task-1"))
    runtime._store_task_result(_result("task-2"))
    runtime._store_task_result(_result("task-3"))

    assert runtime.get_task_result("task-1") is None
    assert runtime.get_task_result("task-2") is not None
    assert runtime.get_task_result("task-3") is not None
    assert len(runtime._task_results) == 2


def test_dashboard_prunes_oldest_terminal_job_but_preserves_active_jobs() -> None:
    app = object.__new__(dashboard_server.DashboardApplication)
    jobs = {
        "old-success": {"status": "succeeded"},
        "active": {"status": "running"},
        "old-failure": {"status": "failed"},
    }
    futures = {key: object() for key in jobs}

    app._prune_terminal_jobs_locked(jobs, futures, max_retained=3)

    assert "old-success" not in jobs
    assert "old-success" not in futures
    assert jobs["active"]["status"] == "running"
    assert "old-failure" in jobs


def test_dashboard_rejects_new_mission_when_active_capacity_is_reached() -> None:
    app = object.__new__(dashboard_server.DashboardApplication)
    app.runtime = SimpleNamespace(has_handler=lambda agent_id: True)
    app.job_lock = threading.Lock()
    app.jobs = {
        f"active-{index}": {"status": "running"}
        for index in range(app.MAX_ACTIVE_MISSIONS)
    }
    app.job_futures = {}

    with pytest.raises(dashboard_server.MissionCapacityError):
        app.start_mission("test bounded queue", "search.scout")

    assert len(app.jobs) == app.MAX_ACTIVE_MISSIONS


def test_quant_refresh_queue_rejects_when_active_capacity_is_reached() -> None:
    app = object.__new__(dashboard_server.DashboardApplication)
    app.job_lock = threading.Lock()
    app.quant_refresh_jobs = {
        f"active-{index}": {"status": "running"}
        for index in range(app.MAX_ACTIVE_QUANT_REFRESH_JOBS)
    }
    app.quant_refresh_futures = {}

    with pytest.raises(dashboard_server.MissionCapacityError):
        app.start_quant_refresh()

    assert len(app.quant_refresh_jobs) == app.MAX_ACTIVE_QUANT_REFRESH_JOBS
