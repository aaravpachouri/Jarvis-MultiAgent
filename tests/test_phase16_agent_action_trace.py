"""Phase 16: concrete action spans and two-way task handoff trace."""
from __future__ import annotations
import asyncio
from types import SimpleNamespace

from core.contracts import AgentSpec, AgentMessage, TaskEvent, TaskRequest
from core.registry import AgentRegistry
from core.runtime import AgentRuntime
from dashboard_server import DashboardApplication


def _spec(agent_id: str, capability: str) -> AgentSpec:
    name = agent_id.split(".")[-1].title()
    return AgentSpec(agent_id=agent_id, name=name, role="test specialist", description="test", capabilities=(capability,))


def test_action_scope_emits_start_and_finish_with_safe_metadata() -> None:
    runtime = AgentRuntime(registry=AgentRegistry())
    async def worker(task, context):
        async with context.action("test.provider.fetch", action_type="tool", metadata={
            "tool_name": "fake_search", "provider": "unit_test", "query": "must not leak",
        }):
            await asyncio.sleep(0.002)
        return {"summary": "done"}
    runtime.register_agent(_spec("test.worker", "test.execute"), worker)

    async def run():
        subscription = runtime.bus.subscribe(queue_size=100)
        result = await runtime.run_task(TaskRequest(prompt="private task prompt", target_agent_id="test.worker"))
        items = []
        while not subscription.queue.empty():
            items.append(subscription.get_nowait())
        runtime.bus.unsubscribe(subscription)
        return result, items

    result, items = asyncio.run(run())
    actions = [item for item in items if isinstance(item, TaskEvent) and item.event_type.startswith("agent.action.")]
    assert result.succeeded
    assert [item.event_type for item in actions] == ["agent.action.started", "agent.action.succeeded"]
    assert actions[0].payload["action_id"] == actions[1].payload["action_id"]
    assert actions[1].payload["duration_ms"] >= 0
    assert "query" not in actions[0].payload
    serialized = DashboardApplication._serialize_runtime_event(actions[0])
    assert serialized["payload"]["tool_name"] == "fake_search"
    assert "must not leak" not in str(serialized)
    assert "private task prompt" not in str(serialized)


def test_delegation_publishes_request_and_real_result_return_message() -> None:
    runtime = AgentRuntime(registry=AgentRegistry())
    async def child(task, context):
        return {"summary": "child finished", "private_output": "do not publish"}
    async def parent(task, context):
        result = await context.delegate("child work prompt", agent_id="test.child", capabilities=("child.execute",))
        return {"child_status": result.status.value}
    runtime.register_agent(_spec("test.child", "child.execute"), child)
    runtime.register_agent(_spec("test.parent", "parent.execute"), parent)

    async def run():
        subscription = runtime.bus.subscribe(queue_size=200)
        result = await runtime.run_task(TaskRequest(prompt="parent task", target_agent_id="test.parent"))
        items = []
        while not subscription.queue.empty():
            items.append(subscription.get_nowait())
        runtime.bus.unsubscribe(subscription)
        return result, items

    result, items = asyncio.run(run())
    messages = [item for item in items if isinstance(item, AgentMessage)]
    request_message = next(item for item in messages if item.message_type == "agent.task.delegated")
    result_message = next(item for item in messages if item.message_type == "agent.task.result")
    assert result.succeeded
    assert request_message.from_agent_id == "test.parent"
    assert request_message.to_agent_id == "test.child"
    assert result_message.from_agent_id == "test.child"
    assert result_message.to_agent_id == "test.parent"
    assert result_message.task_id == request_message.task_id
    assert result_message.payload["parent_task_id"] == request_message.payload["parent_task_id"]
    assert result_message.payload["status"] == "succeeded"
    assert "private_output" not in str(result_message.payload)
    assert "child work prompt" not in str(result_message.payload)


def test_search_agent_emits_real_provider_action_without_network(monkeypatch) -> None:
    import agents.search_agent.agent as search_module
    from agents.search_agent.agent import SearchAgent
    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(search_module, "_fetch_html", lambda query, *, timeout: "<html></html>")
    runtime = AgentRuntime(registry=AgentRegistry())
    runtime.register_agent(_spec("search.scout", "web.search"), SearchAgent(result_count=2))

    async def run():
        subscription = runtime.bus.subscribe(queue_size=100)
        result = await runtime.run_task(TaskRequest(prompt="Search the web for test source", target_agent_id="search.scout"))
        items = []
        while not subscription.queue.empty():
            items.append(subscription.get_nowait())
        runtime.bus.unsubscribe(subscription)
        return result, items

    result, items = asyncio.run(run())
    actions = [item for item in items if isinstance(item, TaskEvent) and item.event_type.startswith("agent.action.")]
    assert result.succeeded
    assert [item.event_type for item in actions] == ["agent.action.started", "agent.action.succeeded"]
    assert actions[0].payload["tool_name"] == "web_search"
    assert actions[0].payload["provider"] == "duckduckgo"


def test_dashboard_event_serialization_keeps_action_and_message_metadata_only() -> None:
    action = TaskEvent("agent.action.failed", "task-1", "coding.forge", {
        "action_id": "a-1", "action_name": "forge.model.generate", "action_type": "model",
        "status": "failed", "duration_ms": 21, "error_type": "TimeoutError",
        "prompt": "private", "output": "private", "token": "secret",
    })
    record = DashboardApplication._serialize_runtime_event(action)
    assert record["payload"] == {
        "action_id": "a-1", "action_name": "forge.model.generate", "action_type": "model",
        "status": "failed", "duration_ms": 21, "error_type": "TimeoutError",
    }
    assert "private" not in str(record)
    assert "secret" not in str(record)


def test_live_activity_surfaces_tool_spans_delegation_returns_and_agent_messages() -> None:
    js = ( __import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'assets' / 'app.js').read_text(encoding='utf-8')
    for required in (
        "type.startsWith('agent.action.')",
        "type.startsWith('agent.message.')",
        "type === 'agent.task.delegated'",
        "type === 'agent.task.result'",
        'OBSERVED ACTION SPAN',
        'OBSERVED RETURN MESSAGE',
        'OBSERVED AGENT MESSAGE',
        'duration_ms',
        'state.seenEventIds.has(key)',
    ):
        assert required in js


def test_network_uses_real_action_scopes_and_only_live_mission_handoffs_animate() -> None:
    js = ( __import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'assets' / 'app.js').read_text(encoding='utf-8')
    css = ( __import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'assets' / 'styles.css').read_text(encoding='utf-8')
    assert "event.type === 'agent.action.started'" in js
    assert "activeActions.set(payload.action_id, event)" in js
    assert "activeTaskIds.has(item.payload?.parent_task_id || '')" in js
    assert '.mk-handoff-link.is-live-handoff' in css


def test_planner_athena_and_memory_instrument_their_real_workflows(tmp_path) -> None:
    from agents.memory_agent.agent import MemoryNotesAgent
    from agents.planner_agent.agent import PlannerAgent
    from agents.research_agent.agent import ResearchAgent

    runtime = AgentRuntime(registry=AgentRegistry())
    runtime.register_agent(_spec("planning.planner", "planning.breakdown"), PlannerAgent())
    runtime.register_agent(_spec("research.athena", "research.deep"), ResearchAgent())
    runtime.register_agent(_spec("memory.notes", "memory.save"), MemoryNotesAgent(store_path=tmp_path / "notes.json"))

    async def run():
        subscription = runtime.bus.subscribe(queue_size=300)
        results = []
        results.append(await runtime.run_task(TaskRequest(
            prompt="Build a plan to test a dashboard", target_agent_id="planning.planner")))
        results.append(await runtime.run_task(TaskRequest(
            prompt="Analyze the supplied notes and list supported findings",
            target_agent_id="research.athena",
            context={"source_material": ["The dashboard polls runtime events."]})))
        results.append(await runtime.run_task(TaskRequest(
            prompt="Remember that Phase 16 traces real agent actions", target_agent_id="memory.notes")))
        items = []
        while not subscription.queue.empty():
            items.append(subscription.get_nowait())
        runtime.bus.unsubscribe(subscription)
        return results, items

    results, items = asyncio.run(run())
    action_names = {
        item.payload.get("action_name")
        for item in items
        if isinstance(item, TaskEvent) and item.event_type == "agent.action.started"
    }
    assert all(result.succeeded for result in results)
    assert {"planner.plan.build", "athena.research.workflow", "memory.note.save"}.issubset(action_names)


def test_event_polling_deduplicates_by_event_identity_and_keeps_cursor_moving() -> None:
    js = (__import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'assets' / 'app.js').read_text(encoding='utf-8')
    assert "const rawIncoming = Array.isArray(data.events) ? data.events : []" in js
    assert "state.seenEventIds.has(key)" in js
    assert "state.eventCursor = Math.max(state.eventCursor, Number(data.cursor || 0))" in js
    assert "state.liveEvents = [...state.liveEvents, ...incoming].slice(-800)" in js


def test_agent_message_is_delivered_received_and_returned_without_exposing_payload() -> None:
    """The visible collaboration trace must represent actual inbox delivery."""
    runtime = AgentRuntime(registry=AgentRegistry())

    async def child(task, context):
        await context.send_message(
            "agent.message.progress",
            {"phase": "verification_complete", "private_note": "must stay out of telemetry"},
            to_agent_id="test.parent",
        )
        return {"summary": "child result", "private_output": "not an event field"}

    async def parent(task, context):
        await context.delegate("private child task", agent_id="test.child", capabilities=("child.execute",))
        message = await context.receive_message(timeout=0.5)
        return {"received_type": message.message_type, "phase": message.payload.get("phase")}

    runtime.register_agent(_spec("test.child", "child.execute"), child)
    runtime.register_agent(_spec("test.parent", "parent.execute"), parent)

    async def run():
        subscription = runtime.bus.subscribe(queue_size=200)
        result = await runtime.run_task(TaskRequest(prompt="private parent task", target_agent_id="test.parent"))
        items = []
        while not subscription.queue.empty():
            items.append(subscription.get_nowait())
        runtime.bus.unsubscribe(subscription)
        return result, items

    result, items = asyncio.run(run())
    messages = [item for item in items if isinstance(item, AgentMessage)]
    progress = next(item for item in messages if item.message_type == "agent.message.progress")
    received = next(item for item in messages if item.message_type == "agent.message.received")
    assert result.succeeded
    assert result.output["received_type"] == "agent.message.progress"
    assert progress.from_agent_id == "test.child" and progress.to_agent_id == "test.parent"
    assert received.from_agent_id == "test.child" and received.to_agent_id == "test.parent"
    assert received.payload["source_message_id"] == progress.message_id
    assert received.payload["delivery"] == "inbox_received"
    serialized = DashboardApplication._serialize_runtime_event(progress)
    assert "private_note" not in str(serialized)
    assert "must stay out of telemetry" not in str(serialized)
    assert "private child task" not in str(serialized)
