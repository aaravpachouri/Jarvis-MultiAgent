"""Project bootstrapping and filesystem-based agent discovery."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from .contracts import AgentSpec
from .registry import AgentRegistry
from .runtime import AgentRuntime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ROOT = PROJECT_ROOT / "agents"
DATA_ROOT = PROJECT_ROOT / "data"


def _spec_from_manifest(path: Path) -> AgentSpec:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    fields = ("capabilities", "routing_keywords", "tags", "requires_approval_for")
    for field_name in fields:
        payload[field_name] = tuple(payload.get(field_name, ()))
    return AgentSpec(**payload)


def discover_manifests(agents_root: Path = AGENTS_ROOT) -> list[tuple[Path, AgentSpec]]:
    """Discover agent identities from each agent folder's manifest.json."""
    discovered: list[tuple[Path, AgentSpec]] = []
    seen: set[str] = set()
    for manifest in sorted(agents_root.glob("*/manifest.json")):
        spec = _spec_from_manifest(manifest)
        if spec.agent_id in seen:
            raise ValueError(f"Duplicate agent ID declared in manifests: {spec.agent_id}")
        seen.add(spec.agent_id)
        discovered.append((manifest.parent, spec))
    return discovered


def create_runtime(
    *,
    project_root: Path = PROJECT_ROOT,
    registry_path: Path | None = None,
) -> AgentRuntime:
    """Load agent manifests, persist identities, and activate implemented handlers.

    Adding a new agent folder with a manifest and an ``agent.py`` module is enough
    for its identity to appear in the registry. A runtime handler is activated
    only when that module's ``create_handler()`` returns a callable.
    """
    agents_root = project_root / "agents"
    if registry_path is None:
        registry_path = project_root / "data" / "agent_registry.json"
    registry = AgentRegistry(storage_path=registry_path)

    folders = discover_manifests(agents_root)
    # Manifests are authoritative for checked-in agent identities and policy.
    # Refresh existing manifest-backed entries so stale persisted metadata cannot
    # silently undo approval requirements or profile-only readiness declarations.
    persisted_ids = {spec.agent_id for spec in registry.list()}
    for folder, manifest_spec in folders:
        registry.register(manifest_spec, replace=manifest_spec.agent_id in persisted_ids)

    runtime = AgentRuntime(registry=registry, audit_log_path=project_root / "data" / "task_history.jsonl")
    # Import agent handlers only after the identity registry is populated.
    for folder, manifest_spec in folders:
        module_name = f"agents.{folder.name}.agent"
        module = importlib.import_module(module_name)
        create_handler = getattr(module, "create_handler", None)
        if not callable(create_handler):
            continue
        handler = create_handler()
        if handler is None:
            continue
        runtime.register_agent(manifest_spec, handler, replace=True)
    return runtime
