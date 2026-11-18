"""Typed contracts shared by every JARVIS X agent.

This module deliberately uses only the Python standard library so it can be
introduced into the existing desktop application without dependency churn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4
import re


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AgentNetworkError(RuntimeError):
    """Base exception for the JARVIS agent network."""


class DuplicateAgentError(AgentNetworkError):
    """Raised when an agent ID is already registered."""


class AgentNotFoundError(AgentNetworkError):
    """Raised when a requested agent does not exist or cannot execute."""


class NoCapableAgentError(AgentNetworkError):
    """Raised when no enabled agent matches the requested capabilities."""


class DelegationLimitError(AgentNetworkError):
    """Raised when an agent tries to delegate too deeply."""


class AgentStatus(str, Enum):
    REGISTERED = "registered"
    READY = "ready"
    BUSY = "busy"
    OFFLINE = "offline"
    DISABLED = "disabled"
    ERROR = "error"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")


def _clean_tokens(values: tuple[str, ...] | list[str] | str, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value).strip().lower()
        if not token:
            continue
        if token not in seen:
            cleaned.append(token)
            seen.add(token)
    if not cleaned and field_name == "capabilities":
        raise ValueError("An agent must declare at least one capability.")
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Persistent identity and discovery metadata for one specialist agent."""

    agent_id: str
    name: str
    role: str
    description: str
    capabilities: tuple[str, ...]
    routing_keywords: tuple[str, ...] = ()
    version: str = "1.0.0"
    enabled: bool = True
    max_concurrency: int = 2
    timeout_seconds: float = 120.0
    tags: tuple[str, ...] = ()
    requires_approval_for: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        agent_id = self.agent_id.strip().lower()
        if not _AGENT_ID_RE.fullmatch(agent_id):
            raise ValueError(
                "agent_id must start with a letter and contain 2-80 lowercase "
                "letters, digits, dots, underscores or hyphens."
            )
        if not self.name.strip():
            raise ValueError("Agent name cannot be empty.")
        if not self.role.strip():
            raise ValueError("Agent role cannot be empty.")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "capabilities", _clean_tokens(self.capabilities, "capabilities"))
        object.__setattr__(self, "routing_keywords", _clean_tokens(self.routing_keywords, "routing_keywords"))
        object.__setattr__(self, "tags", _clean_tokens(self.tags, "tags"))
        object.__setattr__(self, "requires_approval_for", _clean_tokens(self.requires_approval_for, "approval"))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "routing_keywords": list(self.routing_keywords),
            "version": self.version,
            "enabled": self.enabled,
            "max_concurrency": self.max_concurrency,
            "timeout_seconds": self.timeout_seconds,
            "tags": list(self.tags),
            "requires_approval_for": list(self.requires_approval_for),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """A traceable task that can be run by or delegated to an agent."""

    prompt: str
    capabilities: tuple[str, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: str(uuid4()))
    target_agent_id: str | None = None
    requested_by: str = "user"
    priority: int = 50
    timeout_seconds: float | None = None
    parent_task_id: str | None = None
    delegation_depth: int = 0
    correlation_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("Task prompt cannot be empty.")
        object.__setattr__(self, "capabilities", _clean_tokens(self.capabilities, "task capabilities"))
        if self.priority < 0 or self.priority > 100:
            raise ValueError("priority must be between 0 and 100.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when specified.")
        if self.delegation_depth < 0:
            raise ValueError("delegation_depth cannot be negative.")
        object.__setattr__(self, "context", dict(self.context))
        if self.target_agent_id:
            object.__setattr__(self, "target_agent_id", self.target_agent_id.strip().lower())


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    agent_id: str
    status: TaskStatus
    output: Any = None
    summary: str = ""
    artifacts: tuple[str, ...] = ()
    error: str | None = None
    started_at: str | None = None
    finished_at: str = field(default_factory=utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status is TaskStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "status": self.status.value,
            "output": self.output,
            "summary": self.summary,
            "artifacts": list(self.artifacts),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class AgentMessage:
    """Typed agent-to-agent message envelope."""

    message_type: str
    from_agent_id: str
    payload: Mapping[str, Any]
    to_agent_id: str | None = None
    task_id: str | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    message_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.message_type.strip():
            raise ValueError("message_type cannot be empty.")
        if not self.from_agent_id.strip():
            raise ValueError("from_agent_id cannot be empty.")
        object.__setattr__(self, "from_agent_id", self.from_agent_id.strip().lower())
        if self.to_agent_id:
            object.__setattr__(self, "to_agent_id", self.to_agent_id.strip().lower())
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True, slots=True)
class TaskEvent:
    event_type: str
    task_id: str
    agent_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("event_type cannot be empty.")
        object.__setattr__(self, "payload", dict(self.payload))
