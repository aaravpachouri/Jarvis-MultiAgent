"""Shared runtime and communication infrastructure for all JARVIS agents."""
from .bootstrap import create_runtime, discover_manifests
from .contracts import (
    AgentMessage, AgentNetworkError, AgentNotFoundError, AgentSpec, AgentStatus,
    DelegationLimitError, DuplicateAgentError, NoCapableAgentError, TaskEvent,
    TaskRequest, TaskResult, TaskStatus,
)
from .event_bus import EventBus, EventSubscription
from .registry import AgentRegistry
from .router import CapabilityRouter, RouteMatch
from .runtime import AgentContext, AgentHandler, AgentRuntime

__all__ = [
    "AgentContext", "AgentHandler", "AgentMessage", "AgentNetworkError", "AgentNotFoundError",
    "AgentRegistry", "AgentRuntime", "AgentSpec", "AgentStatus", "CapabilityRouter",
    "DelegationLimitError", "DuplicateAgentError", "EventBus", "EventSubscription",
    "NoCapableAgentError", "RouteMatch", "TaskEvent", "TaskRequest", "TaskResult", "TaskStatus",
    "create_runtime", "discover_manifests",
]
