from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agents.documentation_agent.agent import DocumentationAgent
from agents.planner_agent.agent import PlannerAgent
from agents.research_agent.agent import ResearchAgent
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
    assert VoiceAgent._is_complex_mission_request(
        "Run a complete multi-agent research and analysis task. Compare three local models."
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


def test_planner_dependency_graph_validator_accepts_valid_graph() -> None:
    steps, error = VoiceAgent._validate_planner_steps([
        {"id": 1, "title": "Research", "depends_on": []},
        {"id": 2, "title": "Implement", "depends_on": [1]},
        {"id": 3, "title": "Verify", "depends_on": [2]},
    ])
    assert error is None
    assert [step["id"] for step in steps] == ["1", "2", "3"]
    assert steps[2]["depends_on"] == ["2"]


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([{"id": 1, "title": "A", "depends_on": [99]}], "unknown dependencies"),
        ([{"id": 1, "title": "A", "depends_on": [2]}, {"id": 2, "title": "B", "depends_on": [1]}], "cycle"),
        ([{"id": 1, "title": "A", "depends_on": [1]}], "self dependency"),
        ([{"id": 1, "title": "A", "depends_on": "2"}], "must be a list"),
        ([{"id": 1, "title": ""}], "non-empty title"),
        ([{"id": 1, "title": "A"}, {"id": 1, "title": "Duplicate"}], "duplicate ID"),
    ],
)
def test_planner_dependency_graph_validator_rejects_invalid_graphs(steps, expected) -> None:
    normalized, error = VoiceAgent._validate_planner_steps(steps)
    assert normalized == []
    assert expected in error


def test_planner_dependency_graph_validator_rejects_non_list_and_empty_steps() -> None:
    assert VoiceAgent._validate_planner_steps(None)[1]
    assert VoiceAgent._validate_planner_steps([])[1]


def test_planner_assigns_specialists_only_from_available_capabilities() -> None:
    planner = PlannerAgent()
    assignments = [
        {"agent_id": "research.athena", "capabilities": ["research.deep", "research.synthesize"]},
        {"agent_id": "coding.forge", "capabilities": ["code.write", "code.test"]},
    ]
    assert planner._assign_specialist("Research the evidence", assignments) == {
        "agent_id": "research.athena", "capability": "research.deep"
    }
    assert planner._assign_specialist("Implement the feature", assignments) == {
        "agent_id": "coding.forge", "capability": "code.write"
    }
    assert planner._assign_specialist("Discuss project goals", assignments) is None


def test_planner_dependency_assignment_is_validated_and_executable() -> None:
    steps, error = VoiceAgent._validate_planner_steps([
        {"id": "research", "title": "Research evidence", "depends_on": [],
         "agent_id": "research.athena", "capability": "research.deep"},
        {"id": "build", "title": "Implement feature", "depends_on": ["research"],
         "agent_id": "coding.forge", "capability": "code.write"},
    ])
    assert error is None
    assert steps[1]["depends_on"] == ["research"]
    assert steps[1]["agent_id"] == "coding.forge"


@pytest.mark.asyncio
async def test_complex_mission_translates_planner_edges_into_workstream_dependencies() -> None:
    registry = AgentRegistry()
    runtime = AgentRuntime(registry=registry, bus=EventBus(), max_delegation_depth=6)
    voice = VoiceAgent()
    runtime.register_agent(_spec("voice.jarvis", "JARVIS", ("conversation.handle", "task.coordinate")), voice)

    class AssignedPlanner:
        async def __call__(self, task, context):
            return {"summary": "Assigned plan", "steps": [
                {"id": "r", "title": "Research evidence", "depends_on": [],
                 "agent_id": "research.athena", "capability": "research.deep"},
                {"id": "c", "title": "Implement feature", "depends_on": ["r"],
                 "agent_id": "coding.forge", "capability": "code.write"},
            ]}

    runtime.register_agent(_spec("planning.planner", "Planner", ("planning.breakdown",)), AssignedPlanner())
    order: list[str] = []

    async def athena(task, context):
        order.append("research")
        return {"summary": "verified research output"}

    async def forge(task, context):
        order.append("forge")
        deps = task.context.get("dependency_results", {})
        assert any("verified research output" in str(value) for value in deps.values())
        return {"summary": "implemented from research"}

    runtime.register_agent(_spec("research.athena", "Athena", ("research.deep",), ("research", "evidence")), athena)
    runtime.register_agent(_spec("coding.forge", "Forge", ("code.write", "code.test"), ("implement", "build", "code")), forge)
    result = await runtime.run_task(TaskRequest(
        prompt="Orchestrate this complex end-to-end mission: research the evidence, then implement the feature.",
        target_agent_id="voice.jarvis",
    ))
    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    assert order == ["research", "forge"]
    assert "workstream_1" in str(result.output)
    assert "workstream_2" in str(result.output)


_MULTI_AGENT_MODEL_RESEARCH_PROMPT = """
Run a complete multi-agent research and analysis task.

OBJECTIVE:
Recommend the best local AI model setup for a student developer using Windows 11, Python and Ollama.

Compare:
1. Qwen 3 4B
2. Llama 3.2 3B
3. Gemma 3 4B

REQUIREMENTS:
- Research current model information using reliable official sources.
- Compare reasoning, coding, speed, memory requirements, context length, licensing and Ollama compatibility.
- Distinguish verified facts from estimates.
- Include source links and explain any missing evidence.

COORDINATION:
- Use the appropriate research and search specialists.
- Have the planning specialist structure the evaluation.
- Have the documentation specialist produce the final report.
- Delegate subtasks where useful.
- Show actual agent handoffs and returned results.
- Do not invent agent participation or successful handoffs.
- If a specialist fails, report the failure honestly.

OUTPUT:
1. Executive recommendation.
2. Comparison table.
3. Verified sources.
4. Best model for each use case.
5. Limitations and uncertainties.

IMPORTANT:
Do not install models, modify files, change settings, or execute commands. This is a research-only test.
"""


def _model_sources():
    content = (
        "The model publishes reasoning benchmark results including MMLU and GPQA. "
        "Its coding performance includes code generation and HumanEval benchmark results. "
        "Published inference speed is measured in tokens per second on stated hardware. "
        "Memory requirements describe RAM, VRAM and quantization options for local inference. "
        "The model card documents the context window, context length and supported token limits. "
        "The model licensing terms identify the applicable license and usage conditions. "
        "Ollama compatibility is documented for local model execution."
    )
    return {
        "Qwen 3 4B": ("https://qwen.ai/models/qwen3", content),
        "Llama 3.2 3B": ("https://llama.com/docs/model-cards/llama3_2", content),
        "Gemma 3 4B": ("https://ai.google.dev/gemma/docs/core", content),
    }


def _register_model_research_runtime(athena_handler, *, scout_calls=None, scribe_calls=None):
    runtime = AgentRuntime(registry=AgentRegistry(), bus=EventBus(), max_delegation_depth=6)
    runtime.register_agent(
        _spec("voice.jarvis", "JARVIS", ("conversation.handle", "task.coordinate")),
        VoiceAgent(),
    )
    runtime.register_agent(
        _spec("planning.planner", "Planner", ("planning.breakdown",), ("planning specialist", "planning")),
        PlannerAgent(),
    )
    runtime.register_agent(
        _spec(
            "research.athena", "Athena",
            ("research.deep", "research.compare", "research.synthesize", "research.verify"),
            ("research", "compare options", "evidence"),
        ),
        athena_handler,
    )

    async def scout(task, context):
        query = task.context.get("research_query", "")
        if scout_calls is not None:
            scout_calls.append(query)
        target = next((name for name in _model_sources() if name.casefold() in query.casefold()), None)
        if not target:
            return {"results": [], "error": f"No model target found in Scout query: {query}"}
        url, page_content = _model_sources()[target]
        return {
            "provider": "Fixture Search Provider",
            "retrieved_at": "2026-11-14T12:00:00+00:00",
            "retrieval_status": "complete",
            "partial": False,
            "results": [{
                "title": f"{target} official model information",
                "url": url,
                "domain": url.split("/")[2],
                "snippet": f"Official {target} source discovery result.",
                "page_content": page_content,
            }],
        }

    runtime.register_agent(
        _spec("search.scout", "Scout", ("web.search",), ("search specialists", "search")),
        scout,
    )

    async def scribe(task, context):
        if scribe_calls is not None:
            scribe_calls.append(task.context)
        return await DocumentationAgent()(task, context)

    runtime.register_agent(
        _spec(
            "documentation.scribe", "Scribe",
            ("writing.report", "documentation.write"),
            ("documentation specialist", "final report", "report"),
        ),
        scribe,
    )
    return runtime


@pytest.mark.asyncio
async def test_complete_multi_agent_research_runs_planner_athena_nested_scout_and_scribe():
    scout_calls: list[str] = []
    scribe_calls: list[dict[str, Any]] = []
    runtime = _register_model_research_runtime(
        ResearchAgent(), scout_calls=scout_calls, scribe_calls=scribe_calls
    )

    result = await runtime.run_task(TaskRequest(
        prompt=_MULTI_AGENT_MODEL_RESEARCH_PROMPT,
        target_agent_id="voice.jarvis",
    ))

    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    assert result.metadata["workstream_count"] == 1
    assert len(scout_calls) == 3
    assert any("Qwen 3 4B" in query for query in scout_calls)
    assert any("Llama 3.2 3B" in query for query in scout_calls)
    assert any("Gemma 3 4B" in query for query in scout_calls)
    assert len(scribe_calls) == 1
    assert "Planner: planning.planner" in str(result.output)
    assert "research.athena" in str(result.output)
    assert "documentation.scribe" in str(result.output)
    assert "Qwen 3 4B" in str(result.output)
    assert "SUCCEEDED" in str(result.output)


@pytest.mark.asyncio
async def test_read_only_research_mission_excludes_forge_and_memory_and_scribe_gets_full_findings():
    scout_calls: list[str] = []
    scribe_calls: list[dict[str, Any]] = []
    runtime = _register_model_research_runtime(
        ResearchAgent(), scout_calls=scout_calls, scribe_calls=scribe_calls
    )
    forbidden_calls: list[str] = []

    async def forbidden_specialist(task, context):
        forbidden_calls.append(context.agent.agent_id)
        return {"summary": "This specialist must not run for a research-only request."}

    # These route matches are intentional: "coding performance" and "memory
    # requirements" are comparison dimensions, not executable requests.
    runtime.register_agent(
        _spec(
            "coding.forge", "Forge",
            ("code.inspect", "code.write", "code.debug", "code.test"),
            ("coding", "code", "implement"),
        ),
        forbidden_specialist,
    )
    runtime.register_agent(
        _spec("memory.notes", "Memory & Notes", ("memory.save",), ("memory", "notes")),
        forbidden_specialist,
    )

    assert VoiceAgent._is_read_only_mission_request(_MULTI_AGENT_MODEL_RESEARCH_PROMPT)
    assert not VoiceAgent._is_code_mutation_request(_MULTI_AGENT_MODEL_RESEARCH_PROMPT)
    assert not VoiceAgent._is_explicit_memory_request(_MULTI_AGENT_MODEL_RESEARCH_PROMPT)

    result = await runtime.run_task(TaskRequest(
        prompt=_MULTI_AGENT_MODEL_RESEARCH_PROMPT,
        target_agent_id="voice.jarvis",
    ))

    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    assert result.metadata["workstream_count"] == 1
    assert forbidden_calls == []
    assert len(scout_calls) == 3
    assert len(scribe_calls) == 1
    assert "Qwen 3 4B" in str(scribe_calls[0].get("body", ""))
    assert "Llama 3.2 3B" in str(scribe_calls[0].get("body", ""))
    assert "Gemma 3 4B" in str(scribe_calls[0].get("body", ""))
    assert "Actual specialist findings" in str(scribe_calls[0].get("body", ""))
    assert "Qwen 3 4B" in str(result.output)
    assert "Llama 3.2 3B" in str(result.output)
    assert "Gemma 3 4B" in str(result.output)
    assert "documentation.scribe" in str(result.output)


@pytest.mark.asyncio
async def test_incomplete_athena_output_fails_mission_quality_gate_but_still_gets_reported():
    scribe_calls: list[dict[str, Any]] = []

    async def incomplete_athena(task, context):
        return {
            "summary": "Athena could not verify enough official model evidence.",
            "research_status": "insufficient_evidence",
            "complete": False,
            "sources": [],
        }

    runtime = _register_model_research_runtime(
        incomplete_athena, scout_calls=[], scribe_calls=scribe_calls
    )
    result = await runtime.run_task(TaskRequest(
        prompt=_MULTI_AGENT_MODEL_RESEARCH_PROMPT,
        target_agent_id="voice.jarvis",
    ))

    assert result.status is TaskStatus.FAILED
    assert "quality_gate_failed" in str(result.output)
    assert "insufficient_evidence" in str(result.output)
    assert "documentation.scribe" in str(result.output)
    assert len(scribe_calls) == 1
    assert "Research quality gate failed" in str(scribe_calls[0].get("limitations"))
