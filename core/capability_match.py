"""Registry-driven lexical matching between task steps and declared capabilities.

The matcher intentionally has no domain-specific task categories. Agent manifests
supply capability descriptions, roles, descriptions and routing keywords; ambiguous
matches remain unassigned rather than being guessed.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "into", "is", "it", "of", "on", "or", "the", "this", "to", "with",
    "user", "task", "work", "step", "result", "output",
})


def _tokens(value: Any) -> set[str]:
    normalized: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", str(value or "").casefold()):
        if token.endswith("ies") and len(token) > 5:
            token = token[:-3] + "y"
        elif token.endswith("ing") and len(token) > 6:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 5:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        if len(token) > 1 and token not in _STOP_WORDS:
            normalized.add(token)
    return normalized


def _capability_descriptions(agent: Mapping[str, Any]) -> dict[str, str]:
    functions = agent.get("functions", [])
    if not isinstance(functions, list):
        return {}
    return {
        str(item.get("capability")): str(item.get("description") or "")
        for item in functions
        if isinstance(item, Mapping) and item.get("capability")
    }


def rank_agent_capabilities(
    task_description: str,
    agents: list[Mapping[str, Any]],
    *,
    minimum_score: float = 0.12,
) -> list[tuple[float, str, str]]:
    """Return scored (score, agent_id, capability) matches from manifest metadata."""
    query_text = str(task_description or "").casefold()
    query_sequence = re.findall(r"[a-z0-9]+", query_text)
    query_positions = {token: index for index, token in enumerate(query_sequence)}
    query_tokens = _tokens(task_description)
    if not query_tokens:
        return []
    query_weight = sum(1.5 if len(token) >= 6 else 1.0 for token in query_tokens)
    ranked: list[tuple[float, str, str]] = []
    for agent in agents:
        agent_id = str(agent.get("agent_id") or "").strip()
        capabilities = agent.get("capabilities", [])
        if not agent_id or not isinstance(capabilities, (list, tuple)):
            continue
        descriptions = _capability_descriptions(agent)
        declared_action_positions = {
            capability: query_positions.get(next(iter(re.findall(r"[a-z0-9]+", description.casefold())), ""), 10**9)
            for capability, description in descriptions.items()
        }
        earliest_declared_action = min(declared_action_positions.values(), default=10**9)
        agent_context = " ".join([
            str(agent.get("name") or ""),
            str(agent.get("role") or ""),
            str(agent.get("description") or ""),
            " ".join(str(item) for item in agent.get("routing_keywords", []) if item),
        ])
        for raw_capability in capabilities:
            capability = str(raw_capability).strip()
            if not capability:
                continue
            evidence_tokens = _tokens(" ".join((
                capability,
                descriptions.get(capability, ""),
                agent_context,
            )))
            overlap = query_tokens & evidence_tokens
            if not overlap:
                continue
            weighted_overlap = sum(1.5 if len(token) >= 6 else 1.0 for token in overlap)
            evidence_weight = sum(1.5 if len(token) >= 6 else 1.0 for token in evidence_tokens)
            coverage = weighted_overlap / max(1.0, query_weight)
            precision = weighted_overlap / max(1.0, evidence_weight)
            score = (0.8 * coverage) + (0.2 * precision)
            # The step's leading action verb is a useful, domain-neutral signal
            # for the primary capability (e.g. "implement" vs "test").
            first_action = next(iter(re.findall(r"[a-z0-9]+", str(task_description or "").casefold())), "")
            if first_action and first_action in evidence_tokens:
                score += 0.2
            action_position = declared_action_positions.get(capability, 10**9)
            if action_position == earliest_declared_action and action_position < 10**9:
                score += 0.1
            if score >= minimum_score:
                ranked.append((score, agent_id, capability))
    return sorted(ranked, key=lambda item: (-item[0], item[1], item[2]))


def best_capability_for_agent(
    task_description: str,
    agent: Mapping[str, Any],
    *,
    minimum_score: float = 0.12,
    ambiguity_margin: float = 0.035,
) -> str:
    """Return one confident capability for an agent, or an empty string if ambiguous."""
    matches = [
        item for item in rank_agent_capabilities(task_description, [agent], minimum_score=minimum_score)
    ]
    if not matches:
        return ""
    # Multiple capabilities on one agent are not an agent-selection ambiguity;
    # use the strongest stable declaration when their semantic scores are close.
    return matches[0][2]
