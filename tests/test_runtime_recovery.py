from __future__ import annotations

import pytest

from core.contracts import AgentSpec, TaskRequest, TaskStatus
from core.runtime import AgentRuntime


def _spec(agent_id: str, capabilities: tuple[str, ...]) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        name=agent_id,
        role="recovery test specialist",
        description="Agent used to test bounded recovery",
        capabilities=capabilities,
        max_concurrency=1,
    )


@pytest.mark.asyncio
async def test_retry_safe_delegation_retries_failed_handler_once():
    runtime = AgentRuntime()
    calls = {"count": 0}

    async def flaky(task, context):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary read-only provider failure")
        return "recovered"

    async def coordinator(task, context):
        result = await context.delegate(
            "read provider status",
            agent_id="research.athena",
            capabilities=("research.verify",),
            retry_safe=True,
            max_attempts=2,
        )
        return {"child_succeeded": result.succeeded, "child_output": result.output, "child_metadata": dict(result.metadata)}

    runtime.register_agent(_spec("research.athena", ("research.verify",)), flaky)
    runtime.register_agent(_spec("voice.jarvis", ("task.coordinate",)), coordinator)
    result = await runtime.run_task(TaskRequest(prompt="coordinate", target_agent_id="voice.jarvis"))

    assert result.succeeded
    assert result.output["child_succeeded"] is True
    assert result.output["child_output"] == "recovered"
    assert calls["count"] == 2
    assert result.output["child_metadata"]["correlation_id"] == result.metadata["correlation_id"]


@pytest.mark.asyncio
async def test_retry_is_rejected_unless_caller_marks_task_safe():
    runtime = AgentRuntime()
    runtime.register_agent(_spec("research.athena", ("research.verify",)), lambda task, context: "ok")

    async def coordinator(task, context):
        try:
            await context.delegate(
                "do task", agent_id="research.athena", max_attempts=2, retry_safe=False
            )
        except ValueError as exc:
            return {"rejected": str(exc)}
        return {"rejected": "not rejected"}

    runtime.register_agent(_spec("voice.jarvis", ("task.coordinate",)), coordinator)
    result = await runtime.run_task(TaskRequest(prompt="coordinate", target_agent_id="voice.jarvis"))
    assert result.succeeded
    assert "retry_safe=True" in result.output["rejected"]


@pytest.mark.asyncio
async def test_fallback_uses_another_executable_capability_match():
    runtime = AgentRuntime()

    async def broken(task, context):
        raise RuntimeError("primary provider failed")

    runtime.register_agent(_spec("research.primary", ("research.verify",)), broken)
    runtime.register_agent(_spec("research.backup", ("research.verify",)), lambda task, context: "backup result")

    async def coordinator(task, context):
        child = await context.delegate(
            "verify sources",
            agent_id="research.primary",
            capabilities=("research.verify",),
            allow_fallback=True,
        )
        return {"succeeded": child.succeeded, "agent_id": child.agent_id, "output": child.output}

    runtime.register_agent(_spec("voice.jarvis", ("task.coordinate",)), coordinator)
    result = await runtime.run_task(TaskRequest(prompt="coordinate", target_agent_id="voice.jarvis"))
    assert result.succeeded
    assert result.output["succeeded"] is True
    assert result.output["agent_id"] == "research.backup"
    assert result.output["output"] == "backup result"


@pytest.mark.asyncio
async def test_fallback_skips_profile_without_handler_and_uses_executable_backup():
    runtime = AgentRuntime()
    runtime.registry.register(_spec("research.profile", ("research.verify",)))
    runtime.register_agent(_spec("research.backup", ("research.verify",)), lambda task, context: "backup result")

    async def coordinator(task, context):
        child = await context.delegate(
            "verify sources",
            agent_id="research.profile",
            capabilities=("research.verify",),
            allow_fallback=True,
        )
        return {"succeeded": child.succeeded, "agent_id": child.agent_id, "output": child.output}

    runtime.register_agent(_spec("voice.jarvis", ("task.coordinate",)), coordinator)
    result = await runtime.run_task(TaskRequest(prompt="coordinate", target_agent_id="voice.jarvis"))
    assert result.succeeded
    assert result.output["succeeded"] is True
    assert result.output["agent_id"] == "research.backup"
    assert result.output["output"] == "backup result"
