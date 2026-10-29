"""Phase 7 integration acceptance tests for the real coordinator/research/search runtime.

External search is deterministic and network-free; the real SearchAgent, ResearchAgent,
VoiceAgent, AgentRuntime, contracts, delegation and task lineage are exercised. Forge is
represented by a deterministic handler so the test never writes to the user's project.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agents.research_agent.agent import ResearchAgent
from agents.search_agent import agent as search_module
from agents.search_agent.agent import SearchAgent
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
        description=f"Phase 7 deterministic integration handler for {name}",
        capabilities=capabilities,
        timeout_seconds=20,
        max_concurrency=4,
    )


@pytest.fixture
def integrated_runtime(monkeypatch):
    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "phase7-test-key-not-a-real-secret")

    provider_calls: list[str] = []

    def fake_tavily(query: str, *, timeout: float, limit: int, include_domains=None):
        provider_calls.append(query)
        return [{
            "title": "Flask Official Documentation",
            "url": "https://flask.palletsprojects.com/en/stable/",
            "domain": "flask.palletsprojects.com",
            "snippet": "Official Flask documentation for application setup and deployment.",
        }]

    monkeypatch.setattr(search_module, "_fetch_tavily_results", fake_tavily)

    registry = AgentRegistry()
    runtime = AgentRuntime(registry=registry, bus=EventBus(), max_delegation_depth=6)
    voice = VoiceAgent()
    athena = ResearchAgent()
    scout = SearchAgent(timeout_seconds=2, result_count=3)
    forge_calls: list[dict] = []

    async def forge_handler(task, context):
        forge_calls.append({
            "task": task,
            "research_sources": list(task.context.get("research_sources", [])),
            "expected_files": list(task.context.get("expected_files", [])),
            "requested_by": task.requested_by,
        })
        expected = task.context.get("expected_files", [])
        files = ", ".join(expected) if expected else "flask_service/app.py"
        return (
            f"Files written and verified: {files}. Tests: 2 passed. "
            "pytest output: 2 passed. Exit code: 0. Verification: PASSED."
        )

    runtime.register_agent(_spec(
        "voice.jarvis", "JARVIS", "coordinator", ("conversation.handle", "task.coordinate")
    ), voice)
    runtime.register_agent(_spec(
        "research.athena", "Athena", "research", ("research.deep", "research.synthesize")
    ), athena)
    runtime.register_agent(_spec(
        "search.scout", "Scout", "search", ("web.search",)
    ), scout)
    runtime.register_agent(_spec(
        "coding.forge", "Forge", "engineering", ("code.write", "code.test")
    ), forge_handler)

    return runtime, provider_calls, forge_calls


def test_real_runtime_executes_jarvis_athena_scout_forge_chain(integrated_runtime):
    runtime, provider_calls, forge_calls = integrated_runtime
    prompt = (
        "Research Flask official documentation and deployment requirements, then use Forge to create "
        "flask_service/app.py and flask_service/test_app.py and run the tests."
    )
    root = TaskRequest(
        prompt=prompt,
        capabilities=("conversation.handle",),
        target_agent_id="voice.jarvis",
        requested_by="user",
        context={"integration_test": True},
    )

    result = asyncio.run(runtime.run_task(root))

    assert result.status is TaskStatus.SUCCEEDED, result.error or result.output
    assert "workflow completed" in str(result.output).casefold()
    assert "https://flask.palletsprojects.com/en/stable/" in str(result.output)
    assert len(provider_calls) == 3, "Athena should perform three distinct Scout searches"
    assert len(forge_calls) == 1, "Forge must run only after Athena returns usable sources"
    assert forge_calls[0]["expected_files"] == [
        "flask_service/app.py", "flask_service/test_app.py"
    ]
    assert forge_calls[0]["research_sources"]

    # Every child task created by this root must preserve its correlation ID and
    # parent linkage. This checks runtime-produced records, not fake context calls.
    results = list(runtime._task_results.values())
    correlated = [item for item in results if item.metadata.get("correlation_id") == root.correlation_id]
    assert len(correlated) >= 5, "Expected root, Athena, three Scout tasks, and Forge"
    assert all(item.metadata.get("correlation_id") == root.correlation_id for item in correlated)
    assert all(item.metadata.get("parent_task_id") for item in correlated if item.task_id != root.task_id)
    scout_results = [item for item in correlated if item.agent_id == "search.scout"]
    assert len(scout_results) == 3
    assert all(item.succeeded for item in scout_results)
    forge_result = next(item for item in correlated if item.agent_id == "coding.forge")
    assert forge_result.metadata.get("requested_by") == "voice.jarvis"


def test_failed_scout_prevents_forge_and_is_not_reported_as_success(integrated_runtime, monkeypatch):
    runtime, provider_calls, forge_calls = integrated_runtime

    def empty_tavily(query: str, *, timeout: float, limit: int, include_domains=None):
        provider_calls.append(query)
        return []

    monkeypatch.setattr(search_module, "_fetch_tavily_results", empty_tavily)
    root = TaskRequest(
        prompt=(
            "Research Flask official documentation and deployment requirements, then use Forge to create "
            "flask_service/app.py and flask_service/test_app.py and run the tests."
        ),
        capabilities=("conversation.handle",),
        target_agent_id="voice.jarvis",
        requested_by="user",
    )

    result = asyncio.run(runtime.run_task(root))

    assert result.status is TaskStatus.FAILED
    assert "workflow stopped" in str(result.error).casefold() or "workflow stopped" in str(result.output).casefold()
    assert forge_calls == [], "Forge must not run when Athena has no usable source evidence"


def test_runtime_task_records_have_real_parent_and_correlation_metadata(integrated_runtime):
    runtime, _, _ = integrated_runtime
    root = TaskRequest(
        prompt=(
            "Research Flask documentation and deployment requirements, then use Forge to create "
            "flask_service/app.py and flask_service/test_app.py and run the tests."
        ),
        capabilities=("conversation.handle",),
        target_agent_id="voice.jarvis",
        requested_by="user",
    )
    result = asyncio.run(runtime.run_task(root))
    assert result.metadata.get("correlation_id") == root.correlation_id
    assert result.task_id == root.task_id
    for child in runtime._task_results.values():
        if child.task_id == root.task_id:
            continue
        assert child.metadata.get("correlation_id") == root.correlation_id
        assert child.metadata.get("parent_task_id")
        assert child.metadata.get("delegation_depth", 0) >= 1
