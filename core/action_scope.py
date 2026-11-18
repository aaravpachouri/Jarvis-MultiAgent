"""Small adapter that lets agents instrument real work without hard coupling to runtime tests."""
from __future__ import annotations
from typing import Any, Mapping


class _NoopActionScope:
    """Context-manager-compatible no-op used when an agent is called standalone."""
    metadata: dict[str, Any] = {}
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, traceback): return False
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, traceback): return False
    def set_outcome(self, status: str, *, reason_code: str | None = None) -> None: return None


def action_scope(
    context: Any,
    name: str,
    *,
    action_type: str = "tool",
    metadata: Mapping[str, Any] | None = None,
):
    """Return an AgentContext action scope, or a no-op scope for standalone use.

    Agent classes are also unit-tested directly without a running runtime. The
    no-op keeps that supported use intact while runtime-invoked agents emit real
    start/finish events around the same operation.
    """
    factory = getattr(context, "action", None)
    if callable(factory):
        return factory(name, action_type=action_type, metadata=metadata)
    return _NoopActionScope()
