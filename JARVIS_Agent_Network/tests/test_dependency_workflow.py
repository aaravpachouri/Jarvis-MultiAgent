from __future__ import annotations

import asyncio

import pytest

from core.workflow import (
    DependencyWorkflow,
    WorkflowGraphError,
    WorkflowStep,
    WorkflowStepStatus,
)


def test_dag_parallel_independent_steps_then_handoff_outputs():
    events = []

    async def research(ctx):
        events.append("research")
        return {"finding": "local-first"}

    async def inventory(ctx):
        events.append("inventory")
        return ["memory.notes", "planning.planner"]

    async def plan(ctx):
        events.append("plan")
        assert ctx["dependencies"]["research"]["finding"] == "local-first"
        assert ctx["dependencies"]["inventory"] == ["memory.notes", "planning.planner"]
        return "plan-output"

    async def report(ctx):
        events.append("report")
        assert ctx["dependencies"]["plan"] == "plan-output"
        return "final-report"

    workflow = DependencyWorkflow([
        WorkflowStep("research", research),
        WorkflowStep("inventory", inventory),
        WorkflowStep("plan", plan, ("research", "inventory")),
        WorkflowStep("report", report, ("plan",)),
    ], max_parallel=2)
    result = asyncio.run(workflow.run({"request": "test"}))

    assert result.succeeded
    assert result.outputs["report"] == "final-report"
    assert events.index("research") < events.index("plan")
    assert events.index("inventory") < events.index("plan")
    assert events.index("plan") < events.index("report")


def test_failed_dependency_blocks_descendants_but_independent_branch_runs():
    events = []

    async def fail(ctx):
        events.append("fail")
        raise RuntimeError("expected failure")

    async def should_not_run(ctx):
        events.append("downstream")
        return "wrong"

    async def independent(ctx):
        events.append("independent")
        return "still ran"

    workflow = DependencyWorkflow([
        WorkflowStep("failed", fail),
        WorkflowStep("downstream", should_not_run, ("failed",)),
        WorkflowStep("independent", independent),
    ])
    result = asyncio.run(workflow.run())

    assert result.status is WorkflowStepStatus.FAILED
    assert result.steps["failed"].status is WorkflowStepStatus.FAILED
    assert result.steps["downstream"].status is WorkflowStepStatus.BLOCKED
    assert result.steps["independent"].status is WorkflowStepStatus.SUCCEEDED
    assert "downstream" not in events
    assert "independent" in events


def test_workflow_rejects_missing_dependencies_and_cycles():
    async def noop(ctx):
        return None

    with pytest.raises(WorkflowGraphError, match="missing dependencies"):
        DependencyWorkflow([WorkflowStep("a", noop, ("missing",))])
    with pytest.raises(WorkflowGraphError, match="cycle"):
        DependencyWorkflow([
            WorkflowStep("a", noop, ("b",)),
            WorkflowStep("b", noop, ("a",)),
        ])


def test_workflow_requires_unique_step_ids():
    async def noop(ctx):
        return None

    with pytest.raises(WorkflowGraphError, match="unique"):
        DependencyWorkflow([WorkflowStep("same", noop), WorkflowStep("same", noop)])
