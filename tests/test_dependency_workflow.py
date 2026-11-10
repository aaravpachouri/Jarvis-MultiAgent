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



def test_checkpoint_resume_reuses_successful_steps_without_repeating_them(tmp_path):
    from core.checkpoint_store import WorkflowCheckpointStore

    store = WorkflowCheckpointStore(tmp_path / "checkpoints.sqlite3")
    calls = {"research": 0, "implementation": 0}

    async def research(ctx):
        calls["research"] += 1
        return {"finding": "verified"}

    async def implementation(ctx):
        calls["implementation"] += 1
        assert ctx["dependencies"]["research"] == {"finding": "verified"}
        if calls["implementation"] == 1:
            raise RuntimeError("temporary failure")
        return "complete"

    def build():
        return DependencyWorkflow([
            WorkflowStep("research", research),
            WorkflowStep("implementation", implementation, ("research",)),
        ])

    first = asyncio.run(build().run(checkpoint_store=store, workflow_id="mission-1"))
    assert first.status is WorkflowStepStatus.FAILED
    assert calls == {"research": 1, "implementation": 1}

    # A failed step is deliberately not retried automatically; use a new
    # workflow ID after explicit operator review/authorization.
    resumed = asyncio.run(build().run(checkpoint_store=store, workflow_id="mission-1"))
    assert resumed.status is WorkflowStepStatus.FAILED
    assert calls == {"research": 1, "implementation": 1}
    assert resumed.steps["research"].output == {"finding": "verified"}


def test_checkpoint_resume_does_not_repeat_successful_work_and_new_id_allows_reviewed_retry(tmp_path):
    from core.checkpoint_store import WorkflowCheckpointStore

    store = WorkflowCheckpointStore(tmp_path / "checkpoints.sqlite3")
    calls = {"first": 0, "second": 0}

    async def first(ctx):
        calls["first"] += 1
        return "durable output"

    async def second(ctx):
        calls["second"] += 1
        assert ctx["dependencies"]["first"] == "durable output"
        return "done"

    def build():
        return DependencyWorkflow([
            WorkflowStep("first", first),
            WorkflowStep("second", second, ("first",)),
        ])

    one = asyncio.run(build().run(checkpoint_store=store, workflow_id="mission-2"))
    two = asyncio.run(build().run(checkpoint_store=store, workflow_id="mission-2"))
    assert one.succeeded and two.succeeded
    assert calls == {"first": 1, "second": 1}


def test_checkpoint_interrupted_step_is_not_silently_reexecuted(tmp_path):
    from core.checkpoint_store import WorkflowCheckpointStore
    import hashlib
    import json

    store = WorkflowCheckpointStore(tmp_path / "checkpoints.sqlite3")
    graph = [
        {"step_id": "side_effect", "depends_on": [], "label": ""},
        {"step_id": "downstream", "depends_on": ["side_effect"], "label": ""},
    ]
    fingerprint = hashlib.sha256(
        json.dumps(graph, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    store.save("mission-3", fingerprint, "side_effect", "running")
    calls = []

    async def side_effect(ctx):
        calls.append("side_effect")
        return "should not happen"

    async def downstream(ctx):
        calls.append("downstream")
        return "should not happen"

    result = asyncio.run(DependencyWorkflow([
        WorkflowStep("side_effect", side_effect),
        WorkflowStep("downstream", downstream, ("side_effect",)),
    ]).run(checkpoint_store=store, workflow_id="mission-3"))
    assert result.status is WorkflowStepStatus.FAILED
    assert result.steps["side_effect"].status is WorkflowStepStatus.FAILED
    assert result.steps["downstream"].status is WorkflowStepStatus.BLOCKED
    assert calls == []


def test_checkpoint_rejects_changed_graph_and_requires_store_and_id_together(tmp_path):
    from core.checkpoint_store import WorkflowCheckpointStore

    store = WorkflowCheckpointStore(tmp_path / "checkpoints.sqlite3")

    async def noop(ctx):
        return "ok"

    first = DependencyWorkflow([WorkflowStep("one", noop)])
    asyncio.run(first.run(checkpoint_store=store, workflow_id="mission-4"))
    changed = DependencyWorkflow([WorkflowStep("one", noop, label="changed")])
    with pytest.raises(ValueError, match="graph mismatch"):
        asyncio.run(changed.run(checkpoint_store=store, workflow_id="mission-4"))
    with pytest.raises(ValueError, match="must be supplied together"):
        asyncio.run(first.run(checkpoint_store=store))
