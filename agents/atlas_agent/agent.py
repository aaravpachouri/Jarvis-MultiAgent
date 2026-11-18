"""Atlas: read-only systems and API integration planning specialist.

Atlas turns a requested integration into a reviewable implementation plan.
It does not call external APIs, access credentials, change infrastructure, or
claim that integrations have been executed.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.action_scope import action_scope


class AtlasAgent:
    """Produce evidence-bounded integration designs and execution gates."""

    MAX_ITEMS = 40

    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        prompt = str(getattr(task, "prompt", "") or "").strip()
        if not prompt:
            raise ValueError("Atlas requires a non-empty systems or integration request.")

        raw_context = getattr(task, "context", {}) or {}
        if not isinstance(raw_context, Mapping):
            raise TypeError("Atlas task context must be a mapping.")

        supplied = dict(raw_context.get("atlas", {})) if isinstance(raw_context.get("atlas"), Mapping) else {}
        supplied.update({key: value for key, value in raw_context.items() if key != "atlas"})

        async with action_scope(
            context,
            "atlas.integration.plan",
            action_type="workflow",
            metadata={"operation": "read_only_integration_design"},
        ):
            return self._build_plan(prompt, supplied)

    def _build_plan(self, prompt: str, supplied: Mapping[str, Any]) -> dict[str, Any]:
        systems = self._items(supplied.get("systems") or supplied.get("services"))
        integrations = self._items(supplied.get("integrations"))
        constraints = self._items(supplied.get("constraints"))
        success_criteria = self._items(supplied.get("success_criteria"))
        available_adapters = self._items(supplied.get("available_adapters"))

        evidence_gaps: list[str] = []
        if not systems:
            evidence_gaps.append("Source and destination systems are not identified.")
        if not integrations:
            evidence_gaps.append("No concrete API operations or data flows were supplied.")
        if not available_adapters:
            evidence_gaps.append("No installed or approved API adapters were declared; no external calls can be made.")
        if not success_criteria:
            evidence_gaps.append("No measurable integration acceptance criteria were supplied.")

        stages = [
            self._stage(
                1, "Confirm systems, ownership and environments",
                "A verified inventory of source, destination, owners and dev/test/production boundaries",
                ["Each system has an owner and purpose", "Environment boundaries are documented"],
                "Review the supplied system inventory; mark unknown owners or environments as unresolved",
            ),
            self._stage(
                2, "Define API contracts and data mapping",
                "Versioned request/response contracts and field-level mapping",
                ["Required fields, types and validation rules are explicit", "Unknown fields are not guessed"],
                "Validate sample payloads against the documented schemas",
            ),
            self._stage(
                3, "Specify authentication and permission boundaries",
                "An authentication design with least-privilege scopes and secret-handling requirements",
                ["No secrets are included in the plan", "Required scopes and approval owners are named"],
                "Review scopes against the provider's official documentation before implementation",
            ),
            self._stage(
                4, "Design retries, idempotency and failure handling",
                "A failure-mode matrix covering timeouts, rate limits, partial failures and duplicate requests",
                ["Retries are limited to explicitly safe/idempotent operations", "Unknown outcomes are surfaced for reconciliation"],
                "Exercise simulated timeout, throttling and duplicate-delivery cases",
            ),
            self._stage(
                5, "Define observability and data protection",
                "A telemetry and retention plan that avoids logging secrets or unnecessary personal data",
                ["Correlation IDs connect related operations", "Logs redact credentials and sensitive payloads"],
                "Inspect representative logs and verify redaction and traceability",
            ),
            self._stage(
                6, "Specify integration tests and rollout gates",
                "A test matrix and staged release checklist",
                ["Contract, negative, recovery and end-to-end tests are listed", "Production release requires explicit approval"],
                "Run tests in a non-production environment and record evidence before requesting release approval",
            ),
        ]

        if integrations:
            for stage in stages:
                stage["integration_scope"] = integrations
        else:
            for stage in stages:
                stage["integration_scope"] = ["To be confirmed from the user's system/API details"]

        return {
            "agent": "systems.atlas",
            "status": "plan_ready_for_review" if not evidence_gaps else "needs_input",
            "goal": prompt,
            "systems": systems,
            "integrations": integrations,
            "constraints": constraints,
            "success_criteria": success_criteria,
            "available_adapters": available_adapters,
            "stages": stages,
            "evidence_gaps": evidence_gaps,
            "execution": {
                "mode": "read_only_planning",
                "external_api_calls": 0,
                "credentials_accessed": False,
                "files_changed": False,
                "infrastructure_changed": False,
                "deployment_performed": False,
                "production_approval_required": True,
            },
            "summary": (
                "Atlas prepared a reviewable integration plan. No external systems were contacted "
                "and no changes were made."
            ),
        }

    @classmethod
    def _items(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple, set)) else [value]
        cleaned: list[str] = []
        for item in values:
            if isinstance(item, (str, int, float)):
                text = str(item).strip()
                if text and text not in cleaned:
                    cleaned.append(text[:500])
            if len(cleaned) >= cls.MAX_ITEMS:
                break
        return cleaned

    @staticmethod
    def _stage(
        number: int,
        title: str,
        deliverable: str,
        acceptance_criteria: list[str],
        verification: str,
    ) -> dict[str, Any]:
        return {
            "id": number,
            "title": title,
            "deliverable": deliverable,
            "acceptance_criteria": acceptance_criteria,
            "verification": verification,
            "status": "not_started",
        }


def create_handler() -> AtlasAgent:
    return AtlasAgent()
