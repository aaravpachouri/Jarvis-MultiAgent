"""Runtime approval policy for actions with meaningful side effects.

Approval is represented explicitly in TaskRequest.context:
- requested_actions: actions the coordinator intends this task to perform
- approved_actions: actions explicitly approved by the caller/UI

This is a local-runtime policy gate, not an authentication boundary. Applications
that accept untrusted remote clients must populate approvals only from a trusted UI
or server-side authorization layer, never directly from user-supplied JSON.
"""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import AgentSpec, TaskRequest

# Capability -> approval token. Keep this table conservative and topic-agnostic.
CAPABILITY_APPROVALS: dict[str, str] = {
    "filesystem.write": "filesystem.write",
    "artifact.save": "filesystem.write",
    "filesystem.delete": "filesystem.delete",
    "desktop.automation": "desktop.automation",
    "system.shutdown": "system.shutdown",
    "external_send": "external_send",
    "external_publish": "external_publish",
    "production_write": "production_write",
    "paid_resource_create": "paid_resource_create",
    "deployment.release": "production_release",
    "deployment.rollback": "production_rollback",
    "trading.live_order": "live_order",
    "trading.brokerage_write": "brokerage_write",
}

KNOWN_APPROVAL_ACTIONS = frozenset(CAPABILITY_APPROVALS.values()) | frozenset({
    "production_release", "production_rollback", "live_order", "brokerage_write",
})


def _tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def required_approvals(spec: AgentSpec, request: TaskRequest) -> tuple[str, ...]:
    """Return approval tokens required by requested capabilities and action intents."""
    required: set[str] = set()
    for capability in request.capabilities:
        token = CAPABILITY_APPROVALS.get(capability.strip().lower())
        if token:
            required.add(token)

    context: Mapping[str, Any] = request.context
    # Explicit action intents let coordinators gate operations even when a task is
    # addressed to a specialist with no separate capability field.
    requested_actions = _tokens(context.get("requested_actions"))
    declared = set(spec.requires_approval_for)
    required.update(requested_actions & declared)
    required.update(requested_actions & KNOWN_APPROVAL_ACTIONS)
    return tuple(sorted(required))


def missing_approvals(spec: AgentSpec, request: TaskRequest) -> tuple[str, ...]:
    required = set(required_approvals(spec, request))
    approved = _tokens(request.context.get("approved_actions"))
    return tuple(sorted(required - approved))



def explicitly_excluded_agent_ids(
    prompts: str | list[str] | tuple[str, ...],
    agents: list[AgentSpec] | tuple[AgentSpec, ...],
    inherited: Any = None,
) -> tuple[str, ...]:
    """Resolve explicit natural-language agent prohibitions against the live registry.

    This is registry-driven: adding an agent does not require changing a router
    allow/deny list. Exclusions are policy metadata, not keyword-routing hints.
    """
    if isinstance(prompts, str):
        prompt_values = [prompts]
    else:
        prompt_values = [str(value) for value in prompts or ()]
    inherited_ids = set(_tokens(inherited))
    excluded = set(inherited_ids)
    for prompt in prompt_values:
        text = str(prompt or "")
        clauses: list[str] = []
        # Action-based prohibitions: "do not invoke A, B or C".
        for match in re.finditer(
            r"\b(?:do\s+not|don't|dont|never|avoid)\b([^.!?\n]{0,280})",
            text,
            re.IGNORECASE,
        ):
            tail = match.group(1)
            action = re.search(
                r"\b(?:invoke|involve|use|delegate(?:\s+to)?|send(?:\s+to)?|pass(?:\s+to)?|"
                r"hand\s+off(?:\s+to)?|route(?:\s+to)?|call)\b",
                tail,
                re.IGNORECASE,
            )
            if action:
                clauses.append(tail[action.end():])
        # "without A/B/C" is an explicit exclusion without an action verb.
        for match in re.finditer(r"\bwithout\b([^.!?\n]{0,240})", text, re.IGNORECASE):
            clauses.append(match.group(1))

        for spec in agents:
            aliases = {
                spec.name.strip(),
                spec.agent_id.strip(),
                spec.agent_id.rsplit(".", 1)[-1].replace("_", " "),
            }
            metadata_aliases = spec.metadata.get("aliases", ())
            if isinstance(metadata_aliases, str):
                metadata_aliases = (metadata_aliases,)
            if isinstance(metadata_aliases, (list, tuple, set)):
                aliases.update(str(value).strip() for value in metadata_aliases if str(value).strip())
            patterns = []
            for alias in aliases:
                if not alias:
                    continue
                escaped = re.escape(alias)
                escaped = escaped.replace(r"\ ", r"\s+")
                escaped = escaped.replace(r"\&", r"(?:&|and)")
                patterns.append(re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE))
            if any(pattern.search(clause) for clause in clauses for pattern in patterns):
                excluded.add(spec.agent_id)
    return tuple(sorted(excluded))

def manifest_policy_issues(spec: AgentSpec) -> list[str]:
    """Return policy mismatches in a manifest, without exposing secret values."""
    issues: list[str] = []
    approvals = set(spec.requires_approval_for)
    for capability, token in sorted(CAPABILITY_APPROVALS.items()):
        if capability in spec.capabilities and token not in approvals:
            issues.append(
                f"{spec.agent_id}: capability '{capability}' requires approval token '{token}'"
            )
    if spec.metadata.get("profile_only") is True and spec.metadata.get("execution_mode") not in (None, "profile_only"):
        # Profile-only and active execution claims should not contradict each other.
        issues.append(f"{spec.agent_id}: profile_only metadata conflicts with execution_mode")
    return issues
