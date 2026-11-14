"""Phase 14 tests for live runtime telemetry and asynchronous dashboard missions."""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from core import AgentMessage, TaskEvent, TaskResult, TaskStatus
from dashboard_server import DashboardApplication

ROOT = Path(__file__).resolve().parents[1]


def test_task_event_serialization_exposes_lifecycle_metadata_not_prompts_or_output() -> None:
    event = TaskEvent(
        "task.started", "task-123", "coding.forge",
        {
            "prompt": "private user prompt",
            "parent_task_id": "root-1",
            "correlation_id": "corr-1",
            "delegation_depth": 1,
            "output": "private model output",
            "error": "might contain user content",
        },
    )

    record = DashboardApplication._serialize_runtime_event(event)

    assert record["type"] == "task.started"
    assert record["task_id"] == "task-123"
    assert record["agent_id"] == "coding.forge"
    assert record["payload"] == {
        "parent_task_id": "root-1",
        "correlation_id": "corr-1",
        "delegation_depth": 1,
    }
    assert "private user prompt" not in str(record)
    assert "private model output" not in str(record)


def test_delegation_message_preserves_real_sender_recipient_and_parent_link() -> None:
    message = AgentMessage(
        message_type="agent.task.delegated",
        from_agent_id="voice.jarvis",
        to_agent_id="coding.forge",
        task_id="child-1",
        payload={"parent_task_id": "root-1", "capabilities": ["code.write"], "prompt": "do work"},
    )

    record = DashboardApplication._serialize_runtime_event(message)

    assert record["type"] == "agent.task.delegated"
    assert record["from_agent_id"] == "voice.jarvis"
    assert record["to_agent_id"] == "coding.forge"
    assert record["payload"]["parent_task_id"] == "root-1"
    assert "do work" not in str(record)


def test_event_cursor_returns_only_new_items_and_reports_observer_connection() -> None:
    app = DashboardApplication.__new__(DashboardApplication)
    app.event_lock = threading.Lock()
    app.live_events = deque(maxlen=8)
    app.live_event_sequence = 0
    app.event_subscription = None
    app.append_live_event({"type": "task.started", "task_id": "a", "agent_id": "coding.forge", "payload": {}})
    app.append_live_event({"type": "task.succeeded", "task_id": "a", "agent_id": "coding.forge", "payload": {"status": "succeeded"}})

    result = app.events_since(1)

    assert result["cursor"] == 2
    assert [item["type"] for item in result["events"]] == ["task.succeeded"]
    assert result["connected"] is False
    assert result["truncated"] is False


class _SuccessfulRuntime:
    def has_handler(self, agent_id: str) -> bool:
        return agent_id == "coding.forge"

    async def run_task(self, request):
        await asyncio.sleep(0.025)
        return TaskResult(
            task_id=request.task_id,
            agent_id=request.target_agent_id or "voice.jarvis",
            status=TaskStatus.SUCCEEDED,
            output="verified runtime result",
            summary="Stub mission completed.",
            started_at=datetime.now(timezone.utc).isoformat(),
        )


def test_async_mission_records_queued_running_and_terminal_lifecycle() -> None:
    app = DashboardApplication.__new__(DashboardApplication)
    app.runtime = _SuccessfulRuntime()
    app.loop = asyncio.new_event_loop()
    app.loop_thread = threading.Thread(target=lambda: (asyncio.set_event_loop(app.loop), app.loop.run_forever()), daemon=True)
    app.task_lock = threading.Lock()
    app.job_lock = threading.Lock()
    app.event_lock = threading.Lock()
    app.recent_results = []
    app.jobs = {}
    app.job_futures = {}
    app.live_events = deque(maxlen=32)
    app.live_event_sequence = 0
    app.event_subscription = None
    app.loop_thread.start()
    try:
        accepted = app.start_mission("Test async mission", "coding.forge")
        assert accepted["status"] in {"queued", "running", "succeeded"}
        deadline = time.monotonic() + 2
        current = app.mission_status(accepted["task_id"])
        while current and current["status"] not in {"succeeded", "failed", "timed_out", "cancelled"} and time.monotonic() < deadline:
            time.sleep(0.02)
            current = app.mission_status(accepted["task_id"])
        assert current is not None
        assert current["status"] == "succeeded"
        assert current["result"]["output"] == "verified runtime result"
        event_types = [item["type"] for item in app.events_since(0)["events"]]
        assert event_types[0] == "dashboard.mission.queued"
        assert "dashboard.mission.running" in event_types
        assert event_types[-1] == "dashboard.mission.result"
        assert app.recent_results[0]["task_id"] == accepted["task_id"]
    finally:
        app.loop.call_soon_threadsafe(app.loop.stop)
        app.loop_thread.join(timeout=1)
        app.loop.close()


def test_frontend_polls_real_runtime_events_and_uses_async_mission_endpoints() -> None:
    js = (ROOT / "dashboard" / "assets" / "app.js").read_text(encoding="utf-8")
    server = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    assert "api(`/api/events?after=${encodeURIComponent(state.eventCursor)}`)" in js
    assert "api('/api/missions'" in js
    assert "api(`/api/missions/${encodeURIComponent(taskId)}`)" in js
    assert 'path == "/api/events"' in server
    assert 'path == "/api/missions"' in server
    assert 'agent.task.delegated' in js
    assert "OBSERVED ACTION SPAN" in js
    assert "OBSERVED RETURN MESSAGE" in js
    assert "state.seenEventIds.has(key)" in js
