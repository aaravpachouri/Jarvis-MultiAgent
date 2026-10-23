"""Asynchronous execution runtime with bounded delegation and traceable handoffs."""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from threading import Lock
from dataclasses import replace
from typing import Any, Mapping, Protocol

from .event_bus import EventBus
from .contracts import (
    AgentMessage,
    AgentNotFoundError,
    AgentNetworkError,
    AgentSpec,
    AgentStatus,
    DelegationLimitError,
    NoCapableAgentError,
    TaskEvent,
    TaskRequest,
    TaskResult,
    TaskStatus,
    utc_now,
)
from .registry import AgentRegistry
from .policy import missing_approvals


class AgentHandler(Protocol):
    async def __call__(self, task: TaskRequest, context: "AgentContext") -> Any: ...


class AgentContext:
    """Per-invocation context; delegation keeps parent/correlation identifiers."""

    def __init__(self, runtime: "AgentRuntime", spec: AgentSpec, task: TaskRequest) -> None:
        self.runtime = runtime
        self.agent = spec
        self.task = task

    async def delegate(
        self,
        prompt: str,
        *,
        capabilities: tuple[str, ...] | list[str] = (),
        agent_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
        requested_by: str | None = None,
        max_attempts: int = 1,
        retry_safe: bool = False,
        allow_fallback: bool = False,
    ) -> TaskResult:
        """Delegate a task with bounded, opt-in recovery.

        Retries are disabled by default. Callers must explicitly mark a task as
        retry-safe before a failed handler can be invoked more than once. A
        capability fallback is attempted only when requested and only among
        enabled agents with executable handlers matching the declared capabilities.
        Timeouts and cancellations are never automatically retried because the
        original operation may still have side effects.
        """
        if self.task.delegation_depth >= self.runtime.max_delegation_depth:
            raise DelegationLimitError(
                f"Delegation depth limit ({self.runtime.max_delegation_depth}) reached "
                f"while {self.agent.agent_id} handled task {self.task.task_id}."
            )
        if max_attempts < 1 or max_attempts > 4:
            raise ValueError("max_attempts must be between 1 and 4.")
        if max_attempts > 1 and not retry_safe:
            raise ValueError("Retries require retry_safe=True to avoid duplicate side effects.")

        combined_context = dict(self.task.context)
        if context:
            combined_context.update(context)
        normalized_capabilities = tuple(capabilities)
        base_agent_id = agent_id.strip().lower() if agent_id else None

        async def execute(target_agent_id: str | None, attempt: int) -> TaskResult:
            request_context = dict(combined_context)
            request_context["recovery"] = {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "retry_safe": retry_safe,
                "fallback_allowed": allow_fallback,
            }
            request = TaskRequest(
                prompt=prompt,
                capabilities=normalized_capabilities,
                target_agent_id=target_agent_id,
                context=request_context,
                requested_by=requested_by or self.agent.agent_id,
                priority=self.task.priority,
                timeout_seconds=timeout_seconds,
                parent_task_id=self.task.task_id,
                delegation_depth=self.task.delegation_depth + 1,
                correlation_id=self.task.correlation_id,
            )
            message = AgentMessage(
                message_type="agent.task.delegated",
                from_agent_id=self.agent.agent_id,
                to_agent_id=target_agent_id,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
                payload={
                    "parent_task_id": self.task.task_id,
                    "prompt": prompt,
                    "capabilities": list(request.capabilities),
                    "attempt": attempt,
                    "fallback_allowed": allow_fallback,
                },
            )
            # Delegation executes directly through run_task; publish the envelope
            # for observers without filling an inbox that no worker consumes.
            self.runtime.bus.publish(message)
            try:
                return await self.runtime.run_task(request)
            except Exception as exc:
                # Invocation errors (for example a registered identity without a
                # handler) become explicit failed results so an opted-in fallback
                # can proceed. Cancellation is not caught here.
                return TaskResult(
                    task_id=request.task_id,
                    agent_id=target_agent_id or "unresolved.agent",
                    status=TaskStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    summary="Delegated agent could not be invoked.",
                    started_at=utc_now(),
                    metadata={
                        "parent_task_id": self.task.task_id,
                        "correlation_id": request.correlation_id,
                        "delegation_depth": request.delegation_depth,
                        "requested_by": request.requested_by,
                        "capabilities_requested": list(request.capabilities),
                        "recovery_attempt": attempt,
                    },
                )

        # Keep the explicit target first. Capability fallback is opt-in and does
        # not silently replace a target when the caller supplied no capabilities.
        candidates: list[str | None] = [base_agent_id]
        if allow_fallback and normalized_capabilities:
            candidates.extend(
                spec.agent_id for spec in self.runtime.discover_executable(
                    normalized_capabilities, match_all=True,
                    exclude=(base_agent_id,) if base_agent_id else (),
                )
            )
        elif base_agent_id is None and normalized_capabilities:
            candidates = [spec.agent_id for spec in self.runtime.discover_executable(
                normalized_capabilities, match_all=True
            )]

        # Preserve order while removing duplicate IDs/None entries.
        ordered: list[str | None] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate or ""
            if key not in seen:
                ordered.append(candidate)
                seen.add(key)
        if not ordered:
            # Preserve runtime's standard, truthful NoCapableAgentError behavior.
            return await execute(base_agent_id, 1)

        last_result: TaskResult | None = None
        for candidate_index, candidate in enumerate(ordered):
            attempts_here = max_attempts if retry_safe and candidate_index == 0 else 1
            for attempt in range(1, attempts_here + 1):
                last_result = await execute(candidate, attempt)
                if last_result.succeeded:
                    return last_result
                # Do not retry timeouts/cancellation: the original operation may
                # not have stopped cleanly, and repeating it could duplicate work.
                if last_result.status is not TaskStatus.FAILED:
                    break
                if attempt < attempts_here:
                    self.runtime.bus.publish(TaskEvent(
                        "task.retry_scheduled", last_result.task_id, candidate,
                        {"next_attempt": attempt + 1, "correlation_id": self.task.correlation_id},
                    ))
            if not allow_fallback:
                break
        assert last_result is not None
        return last_result

    async def delegate_many(self, tasks: list[Mapping[str, Any]]) -> list[TaskResult]:
        """Run independent delegated tasks concurrently and preserve input order.

        Each item accepts prompt plus capabilities or agent_id, and optional
        context/timeout_seconds. One result is returned per delegated task.
        """
        if self.task.delegation_depth >= self.runtime.max_delegation_depth:
            raise DelegationLimitError(
                f"Delegation depth limit ({self.runtime.max_delegation_depth}) reached "
                f"while {self.agent.agent_id} handled task {self.task.task_id}."
            )
        if not tasks:
            return []
        coroutines = []
        for item in tasks:
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                raise ValueError("Every delegated task must include a non-empty prompt.")
            capabilities = item.get("capabilities", ())
            if isinstance(capabilities, str):
                capabilities = (capabilities,)
            coroutines.append(self.delegate(
                prompt,
                capabilities=tuple(capabilities),
                agent_id=item.get("agent_id"),
                context=item.get("context"),
                timeout_seconds=item.get("timeout_seconds"),
            ))
        return list(await asyncio.gather(*coroutines))

    async def send_message(self, message_type: str, payload: Mapping[str, Any], *, to_agent_id: str) -> None:
        message = AgentMessage(
            message_type=message_type,
            from_agent_id=self.agent.agent_id,
            to_agent_id=to_agent_id,
            task_id=self.task.task_id,
            correlation_id=self.task.correlation_id,
            payload=payload,
        )
        await self.runtime.send_message(message)

    async def receive_message(self, *, timeout: float | None = None) -> AgentMessage:
        return await self.runtime.receive_message(self.agent.agent_id, timeout=timeout)

    def publish(self, message_type: str, payload: Mapping[str, Any], *, to_agent_id: str | None = None) -> None:
        self.runtime.bus.publish(
            AgentMessage(
                message_type=message_type,
                from_agent_id=self.agent.agent_id,
                to_agent_id=to_agent_id,
                task_id=self.task.task_id,
                correlation_id=self.task.correlation_id,
                payload=payload,
            )
        )


class AgentRuntime:
    """Registers executable specialists and routes work by stable IDs/capabilities."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        bus: EventBus | None = None,
        *,
        max_delegation_depth: int = 4,
        audit_log_path: str | Path | None = None,
    ) -> None:
        if max_delegation_depth < 0:
            raise ValueError("max_delegation_depth cannot be negative.")
        self.registry = registry or AgentRegistry()
        self.bus = bus or EventBus()
        self.max_delegation_depth = max_delegation_depth
        self.audit_log_path = Path(audit_log_path).expanduser() if audit_log_path else None
        self._audit_lock = Lock()
        self._audit_errors = 0
        self._handlers: dict[str, AgentHandler] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._active_counts: dict[str, int] = {}
        self._task_results: dict[str, TaskResult] = {}
        self._inboxes: dict[str, asyncio.Queue[AgentMessage]] = {}

    @property
    def audit_errors(self) -> int:
        """Number of audit writes that failed; task execution never depends on the audit sink."""
        return self._audit_errors

    def _append_audit_record(
        self,
        event_type: str,
        request: TaskRequest,
        agent_id: str,
        status: str,
        *,
        error_type: str | None = None,
    ) -> None:
        """Append lifecycle metadata and a safe exception class, never error text."""
        if self.audit_log_path is None:
            return
        record = {
            "timestamp": utc_now(),
            "event": event_type,
            "task_id": request.task_id,
            "parent_task_id": request.parent_task_id,
            "correlation_id": request.correlation_id,
            "requested_by": request.requested_by,
            "agent_id": agent_id,
            "status": status,
            "delegation_depth": request.delegation_depth,
        }
        # Exception class names help diagnose failures without persisting prompts,
        # provider responses, filesystem paths, or potentially secret-bearing text.
        if error_type and error_type.isidentifier() and len(error_type) <= 80:
            record["error_type"] = error_type
        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._audit_lock:
                self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.audit_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError:
            # Observability must not cause an otherwise valid agent task to fail.
            self._audit_errors += 1

    def register_agent(self, spec: AgentSpec, handler: AgentHandler, *, replace: bool = False) -> AgentSpec:
        if not callable(handler):
            raise TypeError("handler must be callable and accept (TaskRequest, AgentContext).")
        if spec.agent_id in self._handlers and not replace:
            # Avoid modifying the registry before duplicate validation succeeds.
            from .contracts import DuplicateAgentError
            raise DuplicateAgentError(f"Executable handler already registered: {spec.agent_id}")
        if replace and self._active_counts.get(spec.agent_id, 0) > 0:
            raise AgentNetworkError(f"Cannot replace active agent {spec.agent_id}; wait for its tasks to finish.")
        registered_ids = {item.agent_id for item in self.registry.list()}
        if spec.agent_id in registered_ids:
            # A seeded identity profile may receive its executable handler later.
            # Existing profiles can be bound without requiring a fake identity.
            self.registry.register(spec, replace=True)
        else:
            self.registry.register(spec, replace=False)
        self._handlers[spec.agent_id] = handler
        self._semaphores[spec.agent_id] = asyncio.Semaphore(spec.max_concurrency)
        self._active_counts.setdefault(spec.agent_id, 0)
        self._inboxes.setdefault(spec.agent_id, asyncio.Queue(maxsize=128))
        self.bus.publish(TaskEvent("agent.registered", "", spec.agent_id, {"name": spec.name, "capabilities": list(spec.capabilities)}))
        return spec

    def unregister_agent(self, agent_id: str) -> None:
        key = agent_id.strip().lower()
        if self._active_counts.get(key, 0) > 0:
            raise AgentNetworkError(f"Cannot unregister active agent {key}; wait for its tasks to finish.")
        self.registry.unregister(key)
        self._handlers.pop(key, None)
        self._semaphores.pop(key, None)
        self._active_counts.pop(key, None)
        self._inboxes.pop(key, None)
        self.bus.publish(TaskEvent("agent.unregistered", "", key, {}))

    def has_handler(self, agent_id: str) -> bool:
        """Return whether an identity is backed by an executable handler."""
        return str(agent_id).strip().lower() in self._handlers

    def executable_agent_ids(self) -> set[str]:
        """Snapshot the IDs currently backed by executable handlers."""
        return set(self._handlers)

    def agent_status(self, agent_id: str) -> AgentStatus:
        spec = self.registry.get(agent_id)
        if not spec.enabled:
            return AgentStatus.DISABLED
        if agent_id not in self._handlers:
            return AgentStatus.REGISTERED
        if self._active_counts.get(agent_id, 0) > 0:
            return AgentStatus.BUSY
        return AgentStatus.READY

    def list_agents(self) -> list[dict[str, Any]]:
        output = []
        for spec in self.registry.list():
            output.append({**spec.to_dict(), "status": self.agent_status(spec.agent_id).value})
        return output

    def discover(self, capabilities: tuple[str, ...] | list[str], *, match_all: bool = True, limit: int | None = None) -> list[AgentSpec]:
        """Discover registered agent identities, including profiles not executable yet.

        This is intentionally an identity-level query. Use ``discover_executable``
        when the caller needs agents that can actually receive work right now.
        """
        return self.registry.find_by_capabilities(capabilities, match_all=match_all, limit=limit)

    def discover_executable(
        self,
        capabilities: tuple[str, ...] | list[str],
        *,
        match_all: bool = True,
        limit: int | None = None,
        exclude: tuple[str, ...] | list[str] = (),
    ) -> list[AgentSpec]:
        """Return enabled, handler-backed agents matching the requested capabilities.

        The registry remains the source of truth for capabilities; handler presence
        is checked at query time so newly activated or removed specialists are
        reflected without hardcoded agent names or a stale readiness cache.
        """
        excluded = {str(item).strip().lower() for item in exclude}
        candidates = self.registry.find_by_capabilities(
            capabilities, match_all=match_all, limit=None
        )
        ready = [
            spec for spec in candidates
            if spec.enabled and spec.agent_id in self._handlers and spec.agent_id not in excluded
        ]
        if limit is not None:
            return ready[:max(0, limit)]
        return ready

    async def send_message(self, message: AgentMessage) -> bool:
        """Deliver targeted messages to an agent inbox and publish an event copy."""
        if message.to_agent_id:
            inbox = self._inboxes.get(message.to_agent_id)
            if inbox is None or message.to_agent_id not in self._handlers:
                raise AgentNotFoundError(f"No executable agent registered as {message.to_agent_id}.")
            if inbox.full():
                from .contracts import AgentNetworkError
                raise AgentNetworkError(f"Inbox full for agent {message.to_agent_id}; message not delivered.")
            inbox.put_nowait(message)
            self.bus.publish(message)
            return True
        return self.bus.publish(message) > 0

    async def receive_message(self, agent_id: str, *, timeout: float | None = None) -> AgentMessage:
        key = agent_id.strip().lower()
        inbox = self._inboxes.get(key)
        if inbox is None:
            raise AgentNotFoundError(f"No executable agent registered as {key}.")
        if timeout is None:
            return await inbox.get()
        return await asyncio.wait_for(inbox.get(), timeout=timeout)

    async def run_task(self, request: TaskRequest) -> TaskResult:
        spec = self._resolve_agent(request)
        handler = self._handlers.get(spec.agent_id)
        if handler is None:
            raise AgentNotFoundError(
                f"Agent {spec.name} ({spec.agent_id}) is registered but has no executable handler yet."
            )
        if not spec.enabled:
            raise AgentNotFoundError(f"Agent {spec.agent_id} is disabled.")

        # Reject capability mismatches for explicit-target tasks. A target ID must
        # not bypass the declared capability contract.
        if request.capabilities:
            unsupported = sorted(set(request.capabilities) - set(spec.capabilities))
            if unsupported:
                return self._policy_failure(
                    request, spec.agent_id,
                    "Agent does not declare requested capabilities: " + ", ".join(unsupported),
                    policy_denied=True,
                )

        missing = missing_approvals(spec, request)
        if missing:
            return self._policy_failure(
                request, spec.agent_id,
                "Explicit approval required for action(s): " + ", ".join(missing),
                policy_denied=True,
                missing_approvals=list(missing),
            )

        started_at = utc_now()
        self.bus.publish(TaskEvent("task.started", request.task_id, spec.agent_id, {
            "prompt": request.prompt,
            "parent_task_id": request.parent_task_id,
            "delegation_depth": request.delegation_depth,
            "correlation_id": request.correlation_id,
        }))
        self._append_audit_record("task.started", request, spec.agent_id, TaskStatus.RUNNING.value)
        semaphore = self._semaphores[spec.agent_id]
        timeout = request.timeout_seconds or spec.timeout_seconds
        async def invoke() -> Any:
            async with semaphore:
                self._active_counts[spec.agent_id] = self._active_counts.get(spec.agent_id, 0) + 1
                try:
                    context = AgentContext(self, spec, request)
                    # Calling a synchronous handler directly could stall Qt/async
                    # routing. Run sync callables off-loop; await coroutine handlers.
                    if inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(getattr(handler, "__call__", None)):
                        return await handler(request, context)
                    output_value = await asyncio.to_thread(handler, request, context)
                    if inspect.isawaitable(output_value):
                        return await output_value
                    return output_value
                finally:
                    self._active_counts[spec.agent_id] = max(0, self._active_counts.get(spec.agent_id, 1) - 1)

        audit_error_type: str | None = None
        try:
            # The timeout includes time waiting for the agent's concurrency slot.
            output = await asyncio.wait_for(invoke(), timeout=timeout)
            if isinstance(output, TaskResult):
                result = replace(output, task_id=request.task_id, agent_id=spec.agent_id, started_at=output.started_at or started_at)
            else:
                result = TaskResult(
                    task_id=request.task_id,
                    agent_id=spec.agent_id,
                    status=TaskStatus.SUCCEEDED,
                    output=output,
                    summary=output if isinstance(output, str) else "Task completed.",
                    started_at=started_at,
                )
        except asyncio.TimeoutError:
            audit_error_type = "TimeoutError"
            result = TaskResult(
                task_id=request.task_id,
                agent_id=spec.agent_id,
                status=TaskStatus.TIMED_OUT,
                error=f"Agent exceeded the {timeout:g}-second task timeout.",
                summary="Task timed out.",
                started_at=started_at,
            )
        except asyncio.CancelledError:
            audit_error_type = "CancelledError"
            result = TaskResult(
                task_id=request.task_id,
                agent_id=spec.agent_id,
                status=TaskStatus.CANCELLED,
                error="Task was cancelled.",
                summary="Task cancelled.",
                started_at=started_at,
            )
            self._task_results[request.task_id] = result
            self.bus.publish(TaskEvent("task.cancelled", request.task_id, spec.agent_id, result.to_dict()))
            self._append_audit_record(
                "task.cancelled", request, spec.agent_id, result.status.value, error_type=audit_error_type
            )
            raise
        except Exception as exc:
            audit_error_type = type(exc).__name__
            result = TaskResult(
                task_id=request.task_id,
                agent_id=spec.agent_id,
                status=TaskStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                summary="Agent execution failed.",
                started_at=started_at,
            )
        # Every terminal result carries its execution lineage, regardless of
        # whether the handler returned a string, a TaskResult, or failed.
        result = replace(
            result,
            metadata={
                **dict(result.metadata),
                "parent_task_id": request.parent_task_id,
                "correlation_id": request.correlation_id,
                "delegation_depth": request.delegation_depth,
                "requested_by": request.requested_by,
                "capabilities_requested": list(request.capabilities),
            },
        )
        self._task_results[request.task_id] = result
        event_type = "task.succeeded" if result.succeeded else f"task.{result.status.value}"
        self.bus.publish(TaskEvent(event_type, request.task_id, spec.agent_id, result.to_dict()))
        self._append_audit_record(
            event_type, request, spec.agent_id, result.status.value, error_type=audit_error_type
        )
        return result

    def _policy_failure(
        self,
        request: TaskRequest,
        agent_id: str,
        message: str,
        *,
        policy_denied: bool,
        missing_approvals: list[str] | None = None,
    ) -> TaskResult:
        """Record a blocked request as a failed terminal task without invoking a handler."""
        result = TaskResult(
            task_id=request.task_id,
            agent_id=agent_id,
            status=TaskStatus.FAILED,
            error=message,
            summary="Task blocked by runtime policy.",
            metadata={
                "policy_denied": policy_denied,
                "missing_approvals": list(missing_approvals or []),
                "parent_task_id": request.parent_task_id,
                "correlation_id": request.correlation_id,
                "delegation_depth": request.delegation_depth,
                "requested_by": request.requested_by,
                "capabilities_requested": list(request.capabilities),
            },
        )
        self._task_results[request.task_id] = result
        self.bus.publish(TaskEvent("task.failed", request.task_id, agent_id, result.to_dict()))
        self._append_audit_record(
            "task.failed", request, agent_id, result.status.value, error_type="PolicyFailure"
        )
        return result

    def get_task_result(self, task_id: str) -> TaskResult | None:
        return self._task_results.get(task_id)

    def _resolve_agent(self, request: TaskRequest) -> AgentSpec:
        if request.target_agent_id:
            return self.registry.get(request.target_agent_id)
        candidates = self.discover_executable(request.capabilities, match_all=True, limit=10)
        if not candidates:
            raise NoCapableAgentError(
                "No executable agent matches all requested capabilities: "
                + ", ".join(request.capabilities)
            )
        # Registry ordering is stable. This is an initial deterministic dispatcher;
        # later phases can add live latency/load-aware selection without changing contracts.
        return min(
            candidates,
            key=lambda candidate: (
                self._active_counts.get(candidate.agent_id, 0) / candidate.max_concurrency,
                candidate.timeout_seconds,
                candidate.agent_id,
            ),
        )
