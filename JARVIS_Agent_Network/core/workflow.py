"""Dependency-aware execution for JARVIS multi-agent workflows.

A small standard-library DAG scheduler. Independent ready steps execute in
parallel; dependent steps receive the real outputs of their prerequisites.
Failed prerequisites block descendants rather than allowing fabricated inputs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping


class WorkflowStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    step_id: str
    run: Callable[[Mapping[str, Any]], Awaitable[Any]]
    depends_on: tuple[str, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        if not self.step_id or not self.step_id.strip():
            raise ValueError("Workflow step_id cannot be empty.")
        if not callable(self.run):
            raise TypeError("Workflow step run must be an async callable.")
        if self.step_id in self.depends_on:
            raise ValueError(f"Workflow step {self.step_id!r} cannot depend on itself.")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f"Workflow step {self.step_id!r} has duplicate dependencies.")


@dataclass(slots=True)
class WorkflowStepResult:
    step_id: str
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    output: Any = None
    error: str | None = None
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "depends_on": list(self.depends_on),
        }


@dataclass(slots=True)
class WorkflowResult:
    status: WorkflowStepStatus
    steps: dict[str, WorkflowStepResult] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status is WorkflowStepStatus.SUCCEEDED

    @property
    def outputs(self) -> dict[str, Any]:
        return {key: result.output for key, result in self.steps.items()
                if result.status is WorkflowStepStatus.SUCCEEDED}

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value,
                "steps": {key: result.to_dict() for key, result in self.steps.items()}}


class WorkflowGraphError(ValueError):
    """The workflow graph is malformed or contains a cycle."""


class DependencyWorkflow:
    """Execute an acyclic graph, parallelizing independent ready steps."""

    def __init__(self, steps: list[WorkflowStep], *, max_parallel: int = 4) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1.")
        if not steps:
            raise WorkflowGraphError("Workflow must contain at least one step.")
        self.steps = {step.step_id: step for step in steps}
        if len(self.steps) != len(steps):
            raise WorkflowGraphError("Workflow step IDs must be unique.")
        self.max_parallel = max_parallel
        for step in steps:
            missing = set(step.depends_on) - self.steps.keys()
            if missing:
                raise WorkflowGraphError(
                    f"Step {step.step_id!r} references missing dependencies: {', '.join(sorted(missing))}."
                )
        self._validate_acyclic()

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise WorkflowGraphError(f"Workflow contains a dependency cycle at {step_id!r}.")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in self.steps[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in self.steps:
            visit(step_id)

    async def run(self, initial_context: Mapping[str, Any] | None = None) -> WorkflowResult:
        results = {
            step_id: WorkflowStepResult(step_id=step_id, depends_on=step.depends_on)
            for step_id, step in self.steps.items()
        }
        semaphore = asyncio.Semaphore(self.max_parallel)
        context = dict(initial_context or {})

        async def execute(step: WorkflowStep) -> None:
            result = results[step.step_id]
            dependency_outputs = {dep: results[dep].output for dep in step.depends_on}
            step_context = {**context, "dependencies": dependency_outputs,
                            "completed_outputs": {
                                key: item.output for key, item in results.items()
                                if item.status is WorkflowStepStatus.SUCCEEDED
                            }}
            result.status = WorkflowStepStatus.RUNNING
            try:
                async with semaphore:
                    result.output = await step.run(step_context)
                result.status = WorkflowStepStatus.SUCCEEDED
            except asyncio.CancelledError:
                result.status = WorkflowStepStatus.FAILED
                result.error = "Cancelled"
                raise
            except Exception as exc:  # surfaced in result; descendants are blocked
                result.status = WorkflowStepStatus.FAILED
                result.error = f"{type(exc).__name__}: {exc}"

        pending = set(self.steps)
        while pending:
            # If a prerequisite failed/blocked, mark descendants blocked without running.
            blocked_now = []
            for step_id in pending:
                deps = self.steps[step_id].depends_on
                if any(results[dep].status in {WorkflowStepStatus.FAILED, WorkflowStepStatus.BLOCKED}
                       for dep in deps):
                    blocked_now.append(step_id)
            for step_id in blocked_now:
                results[step_id].status = WorkflowStepStatus.BLOCKED
                results[step_id].error = "A required dependency did not succeed."
                pending.remove(step_id)

            ready = [step_id for step_id in self.steps
                     if step_id in pending and all(
                         results[dep].status is WorkflowStepStatus.SUCCEEDED
                         for dep in self.steps[step_id].depends_on)]
            if not ready:
                if pending:
                    raise WorkflowGraphError("Workflow scheduler could not find a ready step; graph state is inconsistent.")
                break
            batch = ready[:self.max_parallel]
            await asyncio.gather(*(execute(self.steps[step_id]) for step_id in batch))
            pending.difference_update(batch)

        statuses = {item.status for item in results.values()}
        if statuses == {WorkflowStepStatus.SUCCEEDED}:
            overall = WorkflowStepStatus.SUCCEEDED
        elif WorkflowStepStatus.FAILED in statuses:
            overall = WorkflowStepStatus.FAILED
        else:
            overall = WorkflowStepStatus.BLOCKED
        return WorkflowResult(status=overall, steps=results)
