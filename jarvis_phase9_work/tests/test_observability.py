import asyncio
import json
from pathlib import Path

from core.contracts import AgentSpec, TaskRequest
from core.observability import build_diagnostics, read_task_history, render_diagnostics
from core.registry import AgentRegistry
from core.runtime import AgentRuntime


def _spec():
    return AgentSpec(
        agent_id="test.worker", name="Test Worker", role="test", description="test handler",
        capabilities=("test.execute",),
    )


def test_runtime_persists_lifecycle_without_prompt_or_output(tmp_path):
    log_path = tmp_path / "data" / "task_history.jsonl"
    runtime = AgentRuntime(registry=AgentRegistry(), audit_log_path=log_path)

    async def handler(task, context):
        return "private output should not be persisted"

    runtime.register_agent(_spec(), handler)
    request = TaskRequest(prompt="private prompt should not be persisted", target_agent_id="test.worker")
    result = asyncio.run(runtime.run_task(request))

    assert result.succeeded
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["task.started", "task.succeeded"]
    assert all(line["task_id"] == request.task_id for line in lines)
    assert all(line["correlation_id"] == request.correlation_id for line in lines)
    raw = log_path.read_text(encoding="utf-8")
    assert "private prompt" not in raw
    assert "private output" not in raw


def test_diagnostics_counts_executable_and_registered_only_agents(tmp_path):
    runtime = AgentRuntime(registry=AgentRegistry(), audit_log_path=tmp_path / "history.jsonl")
    runtime.registry.register(_spec())
    report = build_diagnostics(runtime)
    assert report["runtime"]["registered_agents"] == 1
    assert report["runtime"]["executable_agents"] == 0
    assert report["runtime"]["registered_only_agents"] == 1
    assert "registered-only agents" in render_diagnostics(report).lower()


def test_history_aggregates_terminal_status_and_keeps_recent_lineage(tmp_path):
    path = tmp_path / "history.jsonl"
    rows = [
        {"event": "task.started", "status": "running", "task_id": "a"},
        {"event": "task.succeeded", "status": "succeeded", "task_id": "a", "agent_id": "test.worker", "correlation_id": "c1"},
        {"event": "task.failed", "status": "failed", "task_id": "b", "agent_id": "test.worker", "correlation_id": "c2"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    history = read_task_history(path, limit=1)
    assert history["event_count"] == 3
    assert history["terminal_event_count"] == 2
    assert history["status_counts"] == {"failed": 1, "succeeded": 1}
    assert len(history["recent_tasks"]) == 1
    assert history["recent_tasks"][0]["correlation_id"] == "c2"


def test_malformed_history_lines_are_reported_not_fatal(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text('{"event":"task.succeeded","status":"succeeded"}\nnot-json\n[]\n', encoding="utf-8")
    history = read_task_history(path)
    assert history["event_count"] == 1
    assert history["malformed_lines"] == 2


def test_failed_task_persists_safe_error_type_without_exception_text(tmp_path):
    log_path = tmp_path / "history.jsonl"
    runtime = AgentRuntime(registry=AgentRegistry(), audit_log_path=log_path)

    async def handler(task, context):
        raise ValueError("private token=do-not-persist")

    runtime.register_agent(_spec(), handler)
    request = TaskRequest(prompt="private prompt", target_agent_id="test.worker")
    result = asyncio.run(runtime.run_task(request))

    assert not result.succeeded
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    failed = next(row for row in rows if row["event"] == "task.failed")
    assert failed["error_type"] == "ValueError"
    report = build_diagnostics(runtime, history_path=log_path)
    assert "error_type=ValueError" in render_diagnostics(report)
    raw = log_path.read_text(encoding="utf-8")
    assert "do-not-persist" not in raw
    assert "private prompt" not in raw
