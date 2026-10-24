from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agents.planner_agent.agent import PlannerAgent
from agents.voice_agent.agent import VoiceAgent
from core.contracts import AgentSpec, TaskRequest, TaskStatus
from core.event_bus import EventBus
from core.registry import AgentRegistry
from core.runtime import AgentRuntime


def _spec(agent_id: str, name: str, capabilities: tuple[str, ...], keywords: tuple[str, ...] = ()) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        name=name,
        role=f"{name} test specialist",
        description=f"Test handler for {name}",
        capabilities=capabilities,
        routing_keywords=keywords,
        timeout_seconds=5,
        max_concurrency=3,
    )


def test_complex_mission_intent_is_generic_not_a_single_mission_title() -> None:
    assert VoiceAgent._is_complex_mission_request(
        "Please orchestrate this end-to-end software task across multiple workstreams, research, implement, test, and document the result."
    )
    assert VoiceAgent._is_complex_mission_request(
        "This is a complex mission: research alternatives, build a prototype, verify it, and document the outcome."
    )
    assert not VoiceAgent._is_complex_mission_request("Hey JARVIS, what time is it?")
    assert not VoiceAgent._is_complex_mission_request("Please write a short email to my teacher.")


def test_complex_workstream_capability_prioritizes_implementation_when_code_and_tests_requested() -> None:
    spec = _spec(
        "coding.forge", "Forge",
        ("code.inspect", "code.write", "code.debug", "code.test"),
    )
    assert VoiceAgent._complex_workstream_capability(
        spec, "Implement a dashboard and run the tests"
    ) == "code.write"
    assert VoiceAgent._complex_workstream_capability(
        spec, "Run pytest tests and verify the suite"
    ) == "code.test"


@pytest.mark.asyncio
async def test_complex_mission_uses_real_planner_dag_workstreams_and_final_scribe() -> None:
    registry = AgentRegistry()
    runtime = AgentRuntime(registry=registry, bus=EventBus(), max_delegation_depth=6)
    voice = VoiceAgent()
    calls: list[dict[str, Any]] = []
    runtime.register_agent(_spec("voice.jarvis", "JARVIS", ("conversation.handle", "task.coordinate")), voice)
    runtime.register_agent(_spec("planning.planner", "Planner", ("planning.breakdown",), ("planning", "plan")), PlannerAgent())

    async def forge(task, context):
        calls.append({"agent": context.agent.agent_id, "task_id": task.task_id, "parent": task.parent_task_id,
                      "capability": task.capabilities, "plan": task.context.get("planning_result"),
                      "deps": task.context.get("dependency_results"), "prompt": task.prompt})
        return {"summary": "Implemented requested code and ran its supported checks.", "verified": True}

    async def athena(task, context):
        calls.append({"agent": context.agent.agent_id, "task_id": task.task_id, "parent": task.parent_task_id,
                      "capability": task.capabilities, "plan": task.context.get("planning_result"),
                      "deps": task.context.get("dependency_results"), "prompt": task.prompt})
        return {"summary": "Reviewed supporting evidence and limitations.", "sources": ["local fixture evidence"]}

    async def scribe(task, context):
        calls.append({"agent": context.agent.agent_id, "task_id": task.task_id, "parent": task.parent_task_id,
                      "capability": task.capabilities, "plan": task.context.get("planning_result"),
                      "deps": task.context.get("dependency_results"), "prompt": task.prompt})
        return "Report draft: Forge implementation and Athena evidence review results included."

    runtime.register_agent(_spec("coding.forge", "Forge", ("code.inspect", "code.write", "code.test", "repository.analyze"),
                                  ("implement", "build", "code", "pytest", "tests")), forge)
    runtime.register_agent(_spec("research.athena", "Athena", ("research.deep", "research.synthesize"),
                                  ("research", "evidence", "investigate")), athena)
    runtime.register_agent(_spec("documentation.scribe", "Scribe", ("writing.report", "documentation.write"),
                                  ("write a report", "report", "documentation")), scribe)

    prompt = (
        "Please orchestrate this complex end-to-end mission: research the evidence, then implement a small safe code change, "
        "run tests, and write a report. Include actual workstream results and limitations."
    )
    delegation_events = runtime.bus.subscribe(event_types={"agent.task.delegated"})
    result = await runtime.run_task(TaskRequest(prompt=prompt, target_agent_id="voice.jarvis"))

    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    rendered = str(result.output)
    assert "COMPLEX MISSION — SUCCEEDED" in rendered
    assert "planning.planner" in rendered
    assert "coding.forge" in rendered
    assert "research.athena" in rendered
    assert "documentation.scribe" in rendered
    assert len({item["task_id"] for item in calls}) == 3
    plan_contexts = [item["plan"] for item in calls if item["agent"] != "documentation.scribe"]
    assert all(isinstance(value, dict) and value.get("summary") for value in plan_contexts)
    forge_call = next(item for item in calls if item["agent"] == "coding.forge")
    assert "workstream" in forge_call["prompt"].casefold()
    assert forge_call["parent"] == result.task_id
    # Explicit "then" language must pass the actual upstream research result to Forge.
    forge_dependencies = forge_call["deps"]
    assert isinstance(forge_dependencies, dict) and forge_dependencies
    assert any(
        "Reviewed supporting evidence and limitations." in str(value.get("output", ""))
        for value in forge_dependencies.values()
        if isinstance(value, dict)
    ), "Forge must receive Athena's real upstream workstream output, not only a dependency label"
    # Explicit "then" language creates the dependency edge from evidence work to coding.
    assert "workstream_" in rendered
    observed_delegations = []
    while not delegation_events.queue.empty():
        observed_delegations.append(delegation_events.get_nowait())
    assert len(observed_delegations) >= 4  # planner + two child workstreams + report
    assert all(event.task_id for event in observed_delegations)


@pytest.mark.asyncio
async def test_complex_mission_reports_failed_specialist_as_parent_failure() -> None:
    registry = AgentRegistry()
    runtime = AgentRuntime(registry=registry, bus=EventBus(), max_delegation_depth=6)
    voice = VoiceAgent()
    runtime.register_agent(_spec("voice.jarvis", "JARVIS", ("conversation.handle", "task.coordinate")), voice)
    runtime.register_agent(_spec("planning.planner", "Planner", ("planning.breakdown",)), PlannerAgent())

    async def failing_forge(task, context):
        raise RuntimeError("fixture specialist failure")

    async def athena(task, context):
        return {"summary": "Independent research returned."}

    runtime.register_agent(_spec("coding.forge", "Forge", ("code.write", "code.test"), ("implement", "build", "code", "tests")), failing_forge)
    runtime.register_agent(_spec("research.athena", "Athena", ("research.deep",), ("research", "evidence")), athena)
    prompt = "Orchestrate this complex end-to-end mission: research the evidence, then implement the feature and run tests across workstreams."
    result = await runtime.run_task(TaskRequest(prompt=prompt, target_agent_id="voice.jarvis"))
    assert result.status is TaskStatus.FAILED
    assert "coding.forge=failed" in str(result.error)
    assert "PARTIAL/FAILED" in str(result.output)
