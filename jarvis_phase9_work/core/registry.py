"""Thread-safe, optionally persistent registry of JARVIS specialist identities."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterable
from uuid import uuid4

from .contracts import AgentSpec, DuplicateAgentError, AgentNotFoundError


class AgentRegistry:
    """Stores stable identities and supports fast capability discovery.

    Pass ``storage_path`` to persist dynamic agent registrations across process
    restarts. Updates are written atomically so a crash cannot leave half a JSON
    registry file. The default is in-memory storage for tests and embedding.
    """

    def __init__(self, storage_path: str | os.PathLike[str] | None = None) -> None:
        self._agents: dict[str, AgentSpec] = {}
        self._lock = RLock()
        self._storage_path = Path(storage_path).expanduser() if storage_path else None
        if self._storage_path and self._storage_path.exists():
            self._load_from_disk()

    def register(self, spec: AgentSpec, *, replace: bool = False) -> AgentSpec:
        with self._lock:
            existing = self._agents.get(spec.agent_id)
            if existing is not None and not replace:
                raise DuplicateAgentError(f"Agent ID already registered: {spec.agent_id}")
            self._agents[spec.agent_id] = spec
            try:
                self._persist_locked()
            except Exception:
                if existing is None:
                    self._agents.pop(spec.agent_id, None)
                else:
                    self._agents[spec.agent_id] = existing
                raise
            return spec

    def unregister(self, agent_id: str) -> AgentSpec:
        key = agent_id.strip().lower()
        with self._lock:
            try:
                removed = self._agents.pop(key)
            except KeyError as exc:
                raise AgentNotFoundError(f"Unknown agent: {key}") from exc
            try:
                self._persist_locked()
            except Exception:
                self._agents[key] = removed
                raise
            return removed

    def get(self, agent_id: str) -> AgentSpec:
        key = agent_id.strip().lower()
        with self._lock:
            try:
                return self._agents[key]
            except KeyError as exc:
                raise AgentNotFoundError(f"Unknown agent: {key}") from exc

    def list(self, *, enabled_only: bool = False) -> list[AgentSpec]:
        with self._lock:
            values = list(self._agents.values())
        if enabled_only:
            values = [agent for agent in values if agent.enabled]
        return sorted(values, key=lambda agent: (agent.name.casefold(), agent.agent_id))

    def find_by_capabilities(
        self,
        capabilities: Iterable[str],
        *,
        match_all: bool = True,
        limit: int | None = None,
    ) -> list[AgentSpec]:
        if isinstance(capabilities, str):
            capabilities = (capabilities,)
        required = {str(item).strip().lower() for item in capabilities if str(item).strip()}
        if not required:
            return []
        candidates: list[tuple[int, AgentSpec]] = []
        for spec in self.list(enabled_only=True):
            available = set(spec.capabilities)
            matches = len(required & available)
            if (match_all and required.issubset(available)) or (not match_all and matches > 0):
                candidates.append((matches, spec))
        candidates.sort(key=lambda item: (-item[0], item[1].name.casefold(), item[1].agent_id))
        results = [spec for _, spec in candidates]
        return results if limit is None else results[:max(0, limit)]

    def supports(self, agent_id: str, capability: str) -> bool:
        return capability.strip().lower() in self.get(agent_id).capabilities

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def _load_from_disk(self) -> None:
        assert self._storage_path is not None
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("Registry JSON root must be a list.")
            loaded: dict[str, AgentSpec] = {}
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError("Every registry entry must be an object.")
                values = dict(item)
                for field_name in ("capabilities", "routing_keywords", "tags", "requires_approval_for"):
                    values[field_name] = tuple(values.get(field_name, ()))
                spec = AgentSpec(**values)
                if spec.agent_id in loaded:
                    raise ValueError(f"Duplicate agent ID in persisted registry: {spec.agent_id}")
                loaded[spec.agent_id] = spec
            self._agents = loaded
        except Exception as exc:
            raise ValueError(f"Could not load agent registry at {self._storage_path}: {exc}") from exc

    def _persist_locked(self) -> None:
        if self._storage_path is None:
            return
        path = self._storage_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            payload = [agent.to_dict() for agent in self._agents.values()]
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temp_path, path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
