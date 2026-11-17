from __future__ import annotations

from typing import Any

import pytest

from agents.research_agent.agent import ResearchAgent
from agents.voice_agent.agent import VoiceAgent
from core.contracts import AgentSpec, TaskRequest, TaskStatus
from core.event_bus import EventBus
from core.policy import explicitly_excluded_agent_ids
from core.registry import AgentRegistry
from core.runtime import AgentRuntime


def _spec(agent_id: str, name: str, capabilities: tuple[str, ...]) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        name=name,
        role=f"{name} specialist",
        description=f"Test {name}",
        capabilities=capabilities,
    )


def test_explicit_exclusions_resolve_from_registry_names_without_hardcoded_ids() -> None:
    agents = [
        _spec("coding.forge", "Forge", ("code.inspect",)),
        _spec("memory.notes", "Memory & Notes", ("memory.search",)),
        _spec("deployment.launch", "Launch", ("deployment.plan",)),
        _spec("research.athena", "Athena", ("research.deep",)),
    ]
    excluded = explicitly_excluded_agent_ids(
        "Do NOT invoke Forge, Memory & Notes or Launch. Athena must perform research.",
        agents,
    )
    assert excluded == ("coding.forge", "deployment.launch", "memory.notes")


@pytest.mark.asyncio
async def test_runtime_blocks_direct_execution_of_explicitly_excluded_agent() -> None:
    runtime = AgentRuntime(registry=AgentRegistry(), bus=EventBus())
    calls: list[str] = []

    async def forge(task: Any, context: Any) -> str:
        calls.append("forge")
        return "should never run"

    runtime.register_agent(_spec("coding.forge", "Forge", ("code.inspect",)), forge)
    result = await runtime.run_task(TaskRequest(
        prompt="Do not invoke Forge for this research-only task.",
        target_agent_id="coding.forge",
        capabilities=("code.inspect",),
    ))
    assert result.status is TaskStatus.FAILED
    assert result.metadata.get("policy_denied") is True
    assert calls == []


@pytest.mark.asyncio
async def test_nested_delegation_inherits_user_exclusions_without_emitting_handoff() -> None:
    bus = EventBus()
    subscription = bus.subscribe(event_types=("agent.task.delegated",))
    runtime = AgentRuntime(registry=AgentRegistry(), bus=bus, max_delegation_depth=4)
    calls: list[str] = []

    async def forge(task: Any, context: Any) -> str:
        calls.append("forge")
        return "should never run"

    async def parent(task: Any, context: Any) -> dict[str, Any]:
        result = await context.delegate(
            "Inspect this repository.",
            agent_id="coding.forge",
            capabilities=("code.inspect",),
            allow_fallback=True,
        )
        return {"child_status": result.status.value, "policy_denied": result.metadata.get("policy_denied")}

    runtime.register_agent(_spec("test.parent", "Parent", ("task.coordinate",)), parent)
    runtime.register_agent(_spec("coding.forge", "Forge", ("code.inspect",)), forge)
    result = await runtime.run_task(TaskRequest(
        prompt="Do not invoke Forge, Memory & Notes or Launch. Only research.",
        target_agent_id="test.parent",
    ))
    assert result.succeeded
    assert result.output == {"child_status": "failed", "policy_denied": True}
    assert calls == []
    assert subscription.queue.empty()


def test_agent_name_mentions_do_not_trigger_local_agent_network_analysis() -> None:
    prompt = (
        "Compare Qwen, Llama and Gemma. Required workflow: Planner → Athena → Scout → Scribe. "
        "Do not invoke Forge, Memory & Notes or Launch."
    )
    assert not VoiceAgent._is_local_agent_network_analysis(prompt)
    assert VoiceAgent._is_local_agent_network_analysis(
        "Audit the JARVIS agent network architecture, its specialist roster, benefits and risks."
    )


def test_external_research_takes_precedence_over_ambient_conversation_context() -> None:
    prompt = "Research current model specifications and compare the official sources."
    context = {
        "source_material": ["old conversation notes"],
        "conversation_history": [{"role": "user", "content": "Compare our agent network."}],
    }
    assert not ResearchAgent._is_context_analysis_request(prompt, context)


def test_context_analysis_requires_requested_supplied_material_or_explicit_history_reference() -> None:
    assert ResearchAgent._is_context_analysis_request(
        "Analyze the supplied notes and summarize the discussion.",
        {"source_material": ["A supplied document"]},
    )
    assert ResearchAgent._is_context_analysis_request(
        "Summarize what we discussed in this conversation.",
        {"conversation_history": [{"role": "user", "content": "Earlier context"}]},
    )
    assert not ResearchAgent._is_context_analysis_request(
        "Compare these options.",
        {"conversation_history": [{"role": "user", "content": "Ambient history"}]},
    )


def test_model_comparison_extracts_targets_from_natural_language_heading() -> None:
    prompt = """Compare these models for Windows 11, Python development and local Ollama use:

1. Qwen 3 4B
2. Llama 3.2 3B
3. Gemma 3 4B

REQUIREMENTS:
- Compare reasoning and coding.
"""
    assert ResearchAgent._extract_model_targets(prompt) == [
        "Qwen 3 4B", "Llama 3.2 3B", "Gemma 3 4B"
    ]


def test_explicit_workflow_sequence_resolves_registry_names_in_declared_order() -> None:
    agents = [
        _spec("documentation.scribe", "Scribe", ("writing.report",)),
        _spec("search.scout", "Scout", ("web.search",)),
        _spec("research.athena", "Athena", ("research.deep",)),
        _spec("planning.planner", "Planner", ("planning.breakdown",)),
    ]
    prompt = "Required workflow: Planner → Athena → Scout → Scribe\nDo not use Forge."
    assert VoiceAgent._explicit_workflow_agent_ids(prompt, agents) == [
        "planning.planner", "research.athena", "search.scout", "documentation.scribe"
    ]
