"""Low-latency keyword/capability routing for the expandable agent network."""
from __future__ import annotations

from dataclasses import dataclass
import re
from .registry import AgentRegistry

_WORD_RE = re.compile(r"[a-z0-9_+#.-]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RouteMatch:
    agent_id: str
    name: str
    score: float
    matched_terms: tuple[str, ...]
    capabilities: tuple[str, ...]
    executable: bool = False


class CapabilityRouter:
    """Fast deterministic routing; expensive model reasoning is not required for clear tasks."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def route(
        self,
        prompt: str,
        *,
        limit: int = 5,
        minimum_score: float = 1.0,
        exclude: tuple[str, ...] | list[str] = (),
        executable_ids: set[str] | None = None,
    ) -> list[RouteMatch]:
        # Normalize punctuation that commonly separates words in natural-language
        # requests (e.g. "step-by-step" vs the manifest phrase "step by step").
        # Keep the original request untouched; this is routing-only normalization.
        text = re.sub(r"[-–—_/]+", " ", str(prompt or "").casefold()).strip()
        text = re.sub(r"\s+", " ", text)
        if not text or limit < 1:
            return []
        excluded = {str(item).strip().lower() for item in exclude}
        words = set(_WORD_RE.findall(text))
        matches: list[RouteMatch] = []
        for spec in self.registry.list(enabled_only=True):
            if spec.agent_id in excluded:
                continue
            score = 0.0
            matched: list[str] = []
            for keyword in spec.routing_keywords:
                phrase = re.sub(r"[-–—_/]+", " ", keyword.casefold().strip())
                phrase = re.sub(r"\s+", " ", phrase)
                if not phrase:
                    continue
                if " " in phrase and phrase in text:
                    score += 3.0
                    matched.append(phrase)
                elif " " not in phrase and phrase in words:
                    score += 1.0
                    matched.append(phrase)
            capability_tokens: set[str] = set()
            for capability in spec.capabilities:
                capability_tokens.update(
                    _WORD_RE.findall(capability.replace(".", " ").replace("_", " "))
                )
            overlap = capability_tokens & words
            if overlap:
                score += min(1.5, 0.75 * len(overlap))
                matched.extend(sorted(overlap))
            if score >= minimum_score:
                matches.append(RouteMatch(
                    agent_id=spec.agent_id,
                    name=spec.name,
                    score=score,
                    matched_terms=tuple(dict.fromkeys(matched)),
                    capabilities=spec.capabilities,
                    executable=spec.agent_id in (executable_ids or set()),
                ))
        matches.sort(key=lambda item: (-item.score, not item.executable, item.name.casefold(), item.agent_id))
        return matches[:limit]

    @staticmethod
    def top_agent(matches: list[RouteMatch]) -> RouteMatch | None:
        return matches[0] if matches else None
