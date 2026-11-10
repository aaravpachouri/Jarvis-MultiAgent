from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.atlas_agent.agent import AtlasAgent, create_handler
from core.bootstrap import create_runtime
from core.contracts import TaskRequest, TaskStatus
from core.router import CapabilityRouter


class Context:
    pass


def test_atlas_factory_returns_executable_handler():
    assert isinstance(create_handler(), AtlasAgent)


def test_atlas_builds_integration_plan_from_supplied_evidence():
    task = TaskRequest(
        prompt="Design the CRM to billing API integration",
        target_agent_id="systems.atlas",
        context={
            "systems": ["CRM", "Billing API"],
            "integrations": ["Create customer", "Sync invoice status"],
            "constraints": ["No production writes during testing"],
            "success_criteria": ["Invoices reconcile exactly once"],
            "available_adapters": ["approved CRM sandbox adapter"],
        },
    )
    result = asyncio.run(AtlasAgent()(task, Context()))
    assert result["status"] == "plan_ready_for_review"
    assert result["systems"] == ["CRM", "Billing API"]
    assert len(result["stages"]) == 6
    assert all(stage["acceptance_criteria"] for stage in result["stages"])
    assert all(stage["status"] == "not_started" for stage in result["stages"])
    assert result["execution"]["external_api_calls"] == 0
    assert result["execution"]["credentials_accessed"] is False
    assert result["execution"]["files_changed"] is False
    assert result["execution"]["infrastructure_changed"] is False
    assert result["execution"]["deployment_performed"] is False


def test_atlas_surfaces_missing_information_instead_of_inventing_it():
    result = asyncio.run(
        AtlasAgent()(SimpleNamespace(prompt="Integrate my services", context={}), Context())
    )
    assert result["status"] == "needs_input"
    assert len(result["evidence_gaps"]) == 4
    assert result["systems"] == []
    assert result["integrations"] == []


def test_atlas_rejects_empty_prompt():
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(AtlasAgent()(SimpleNamespace(prompt=" ", context={}), Context()))


def test_atlas_is_registered_and_executable(tmp_path):
    runtime = create_runtime(registry_path=tmp_path / "registry.json")
    assert runtime.has_handler("systems.atlas")
    spec = runtime.registry.get("systems.atlas")
    assert spec.metadata["profile_only"] is False
    matches = CapabilityRouter(runtime.registry).route(
        "Design an API integration between two services",
        executable_ids=runtime.executable_agent_ids(),
    )
    assert any(match.agent_id == "systems.atlas" and match.executable for match in matches)


@pytest.mark.asyncio
async def test_atlas_runs_through_runtime_truthfully(tmp_path):
    runtime = create_runtime(registry_path=tmp_path / "registry.json")
    result = await runtime.run_task(TaskRequest(
        prompt="Plan a service integration",
        target_agent_id="systems.atlas",
        context={"systems": ["Source", "Destination"], "integrations": ["Sync records"]},
    ))
    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["execution"]["external_api_calls"] == 0
    assert result.output["execution"]["deployment_performed"] is False
