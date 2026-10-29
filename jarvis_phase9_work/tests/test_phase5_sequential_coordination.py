from __future__ import annotations

import asyncio

from agents.documentation_agent.agent import DocumentationAgent
from agents.planner_agent.agent import PlannerAgent
from agents.voice_agent.agent import VoiceAgent
from core.contracts import AgentSpec, TaskRequest, TaskStatus
from core.event_bus import EventBus
from core.registry import AgentRegistry
from core.runtime import AgentRuntime


def _spec(agent_id: str, name: str, role: str, capabilities: tuple[str, ...]) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        name=name,
        role=role,
        description=f"Phase 5 test agent: {name}",
        capabilities=capabilities,
        timeout_seconds=10,
        max_concurrency=2,
    )


def _runtime(*, fail_research: bool = False):
    events: list[tuple[str, dict]] = []
    runtime = AgentRuntime(registry=AgentRegistry(), bus=EventBus(), max_delegation_depth=6)
    voice = VoiceAgent()
    planner = PlannerAgent()
    scribe = DocumentationAgent()

    async def athena_handler(task, context):
        events.append(("athena", dict(task.context)))
        if fail_research:
            raise RuntimeError("test research failure")
        return {
            "summary": "Research finding: local JSON storage can persist notes between CLI invocations.",
            "question": task.prompt,
            "sources": [{
                "title": "Local storage test evidence",
                "url": "https://example.invalid/local-storage",
                "snippet": "Deterministic test source; not a real external research claim.",
            }],
            "evidence_level": "deterministic_test_fixture",
        }

    async def planner_handler(task, context):
        events.append(("planner", dict(task.context)))
        assert "Research finding: local JSON storage" in task.context["research_findings"]
        return await planner(task, context)

    async def scribe_handler(task, context):
        events.append(("scribe", dict(task.context)))
        material = " ".join(str(item) for item in task.context.get("source_material", []))
        assert "Research finding: local JSON storage" in material
        assert "Planner output:" in material
        return await scribe(task, context)

    runtime.register_agent(_spec("voice.jarvis", "JARVIS", "coordinator", ("conversation.handle", "task.coordinate")), voice)
    runtime.register_agent(_spec("research.athena", "Athena", "research", ("research.deep", "research.synthesize")), athena_handler)
    runtime.register_agent(_spec("planning.planner", "Planner", "planning", ("planning.breakdown", "planning.checklist")), planner_handler)
    runtime.register_agent(_spec("documentation.scribe", "Scribe", "documentation", ("documentation.write", "writing.report")), scribe_handler)
    return runtime, events


def _request(prompt: str) -> TaskRequest:
    return TaskRequest(
        prompt=prompt,
        capabilities=("conversation.handle",),
        target_agent_id="voice.jarvis",
        requested_by="user",
        context={"phase5_test": True},
    )


def test_three_stage_workflow_detection_is_specific():
    assert VoiceAgent._is_research_plan_report_request(
        "Research secure local notes storage, then create a step-by-step plan, and finally write a report."
    )
    assert not VoiceAgent._is_research_plan_report_request("Research secure local notes storage")
    assert not VoiceAgent._is_research_plan_report_request("Create a plan and write a report")


def test_phase5_runs_athena_then_planner_then_scribe_with_real_handoffs():
    runtime, events = _runtime()
    prompt = "Research secure local notes storage, then create a step-by-step plan, and finally write a report."
    root = _request(prompt)
    result = asyncio.run(runtime.run_task(root))

    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    output = str(result.output)
    assert "Athena -> Planner -> Scribe" in output
    assert "Research finding: local JSON storage" in output
    assert "## Plan" in output and "## Report draft" in output
    assert [name for name, _ in events] == ["athena", "planner", "scribe"]
    assert events[1][1]["stage"] == 2
    assert events[2][1]["stage"] == 3
    assert "Research finding: local JSON storage" in events[1][1]["research_findings"]
    assert any("Planner output:" in str(item) for item in events[2][1]["source_material"])

    child_results = [item for item in runtime._task_results.values() if item.task_id != root.task_id]
    assert {item.agent_id for item in child_results} >= {
        "research.athena", "planning.planner", "documentation.scribe"
    }
    assert all(item.metadata.get("parent_task_id") == root.task_id for item in child_results)
    assert all(item.metadata.get("correlation_id") == root.correlation_id for item in child_results)


def test_phase5_stops_downstream_stages_when_research_fails():
    runtime, events = _runtime(fail_research=True)
    root = _request("Research secure local notes storage, then create a step-by-step plan, and finally write a report.")
    result = asyncio.run(runtime.run_task(root))

    assert result.status is TaskStatus.SUCCEEDED  # Coordinator reports the failed child honestly.
    assert "Sequential workflow stopped at Athena research" in str(result.output)
    assert "Later stages were not run" in str(result.output)
    assert [name for name, _ in events] == ["athena"]
