from __future__ import annotations

import pytest

from core.contracts import AgentSpec, TaskRequest, TaskStatus, NoCapableAgentError
from core.runtime import AgentRuntime


def spec(agent_id: str, capabilities: tuple[str, ...], *, enabled: bool = True) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        name=agent_id,
        role="test specialist",
        description="Test specialist for dynamic discovery",
        capabilities=capabilities,
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_executable_discovery_excludes_profiles_without_handlers():
    runtime = AgentRuntime()
    runtime.registry.register(spec("research.athena", ("research.verify",)))
    runtime.register_agent(spec("search.scout", ("research.verify",)), lambda task, context: "verified")

    identities = runtime.discover(("research.verify",))
    executable = runtime.discover_executable(("research.verify",))

    assert {item.agent_id for item in identities} == {"research.athena", "search.scout"}
    assert [item.agent_id for item in executable] == ["search.scout"]


@pytest.mark.asyncio
async def test_executable_discovery_respects_disabled_and_excluded_agents():
    runtime = AgentRuntime()
    runtime.register_agent(spec("search.scout", ("web.search",)), lambda task, context: "scout")
    runtime.register_agent(spec("search.backup", ("web.search",)), lambda task, context: "backup")
    runtime.registry.register(spec("search.disabled", ("web.search",), enabled=False))

    results = runtime.discover_executable(("web.search",), exclude=("search.scout",))
    assert [item.agent_id for item in results] == ["search.backup"]


@pytest.mark.asyncio
async def test_capability_dispatch_uses_only_executable_specialists():
    runtime = AgentRuntime()
    runtime.registry.register(spec("research.athena", ("research.verify",)))
    runtime.register_agent(spec("search.scout", ("research.verify",)), lambda task, context: "live result")

    result = await runtime.run_task(TaskRequest(prompt="verify this", capabilities=("research.verify",)))

    assert result.status is TaskStatus.SUCCEEDED
    assert result.agent_id == "search.scout"
    assert result.output == "live result"


@pytest.mark.asyncio
async def test_capability_dispatch_fails_truthfully_when_only_profiles_match():
    runtime = AgentRuntime()
    runtime.registry.register(spec("research.athena", ("research.verify",)))

    with pytest.raises(NoCapableAgentError, match="No executable agent"):
        await runtime.run_task(TaskRequest(prompt="verify this", capabilities=("research.verify",)))

@pytest.mark.asyncio
async def test_task_result_contains_parent_and_correlation_lineage():
    runtime = AgentRuntime()
    runtime.register_agent(spec("search.scout", ("web.search",)), lambda task, context: "source results")
    request = TaskRequest(
        prompt="search source",
        capabilities=("web.search",),
        parent_task_id="parent-123",
        correlation_id="correlation-456",
        delegation_depth=2,
        requested_by="research.athena",
    )
    result = await runtime.run_task(request)
    assert result.metadata["parent_task_id"] == "parent-123"
    assert result.metadata["correlation_id"] == "correlation-456"
    assert result.metadata["delegation_depth"] == 2
    assert result.metadata["requested_by"] == "research.athena"
    assert result.metadata["capabilities_requested"] == ["web.search"]


@pytest.mark.asyncio
async def test_structured_search_failure_is_not_marked_succeeded():
    runtime = AgentRuntime()
    failure = {
        "summary": "Scout couldn't retrieve search results.",
        "query": "research on aqi levels in india",
        "results": [],
        "provider": "DuckDuckGo HTML search",
        "error": "The search request failed: timed out",
        "error_code": "network_error",
        "retryable": True,
    }
    runtime.register_agent(
        spec("search.scout", ("web.search",)),
        lambda task, context: failure,
    )

    result = await runtime.run_task(TaskRequest(
        prompt="research on aqi levels in india",
        capabilities=("web.search",),
        target_agent_id="search.scout",
    ))

    assert result.status is TaskStatus.FAILED
    assert result.succeeded is False
    assert result.output == failure
    assert result.error == failure["error"]
    assert result.metadata["error_code"] == "network_error"
    assert result.metadata["retryable"] is True
