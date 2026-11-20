"""Offline planning specialist tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.planner_agent.agent import PlannerAgent, create_handler
from core.bootstrap import create_runtime
from core.contracts import TaskRequest
from core.router import CapabilityRouter


class NoNetworkContext:
    async def delegate(self, **kwargs):
        raise AssertionError("Planner must not delegate or require network services")


def test_planner_factory_returns_executable_handler():
    assert isinstance(create_handler(), PlannerAgent)
    assert callable(create_handler())


def test_planner_returns_dependency_aware_plan_without_delegation():
    task = TaskRequest(
        prompt="Plan my app launch",
        target_agent_id="planning.planner",
        context={
            "goal": "Launch the app",
            "constraints": ["Keep costs low"],
            "deadline": "2026-11-15",
            "success_criteria": ["Public landing page works", "Smoke tests pass"],
            "available_resources": ["One developer", "Existing repository"],
        },
    )
    result = asyncio.run(PlannerAgent()(task, NoNetworkContext()))
    assert result["goal"] == "Launch the app"
    assert len(result["steps"]) >= 4
    assert result["steps"][0]["depends_on"] == []
    assert result["steps"][1]["depends_on"] == [1]
    assert result["steps"][-1]["status"] == "not_started"
    assert result["deadline"] == "2026-11-15"
    assert result["success_criteria"] == ["Public landing page works", "Smoke tests pass"]
    assert result["execution_status"] == "plan_only_not_executed"
    assert len(result["checklist"]) == len(result["steps"])


def test_planner_preserves_user_provided_tasks_in_order():
    task = SimpleNamespace(prompt="Organize these tasks", context={"tasks": ["Create outline", "Draft content", "Review draft"]})
    result = asyncio.run(PlannerAgent()(task, NoNetworkContext()))
    assert [step["title"] for step in result["steps"]] == ["Create outline", "Draft content", "Review draft"]
    assert result["plan_basis"] == "user_supplied_tasks"


def test_planner_rejects_empty_goal():
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(PlannerAgent()(SimpleNamespace(prompt="  ", context={}), NoNetworkContext()))


def test_planner_is_discovered_and_routes_as_executable(tmp_path):
    runtime = create_runtime(registry_path=tmp_path / "registry.json")
    assert runtime.has_handler("planning.planner")
    matches = CapabilityRouter(runtime.registry).route(
        "Make a step by step action plan", executable_ids=runtime.executable_agent_ids()
    )
    assert matches
    assert matches[0].agent_id == "planning.planner"
    assert matches[0].executable


def test_planner_creates_agent_network_specific_test_plan() -> None:
    task = SimpleNamespace(prompt="Create a step-by-step plan to test the JARVIS agent network", context={})
    result = asyncio.run(PlannerAgent()(task, NoNetworkContext()))
    titles = [step["title"] for step in result["steps"]]
    assert any("automated test suite" in title.casefold() for title in titles)
    assert any("specialist" in title.casefold() for title in titles)
    assert any("Forge's implementation" in title for title in titles)
    assert result["execution_status"] == "plan_only_not_executed"
