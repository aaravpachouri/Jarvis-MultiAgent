"""Planner: deterministic, offline-capable goal decomposition.

This handler creates a transparent plan from the user's goal and supplied
context. It does not pretend to execute tasks or require a model/search service.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


class PlannerAgent:
    """Build a structured action plan with dependencies and verification gates."""

    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        prompt = str(getattr(task, "prompt", "") or "").strip()
        if not prompt:
            raise ValueError("Planner requires a non-empty goal or planning request.")

        raw_context = getattr(task, "context", {}) or {}
        if not isinstance(raw_context, Mapping):
            raise TypeError("Planner task context must be a mapping.")
        supplied = dict(raw_context.get("planning", {})) if isinstance(raw_context.get("planning"), Mapping) else {}
        supplied.update({key: value for key, value in raw_context.items() if key != "planning"})

        goal = self._first_text(supplied.get("goal"), supplied.get("objective"), prompt)
        constraints = self._as_lines(supplied.get("constraints"))
        resources = self._as_lines(supplied.get("available_resources"))
        success_criteria = self._as_lines(supplied.get("success_criteria"))
        deadline = self._first_text(supplied.get("deadline"), supplied.get("due_date"), "")
        explicit_milestones = self._as_lines(supplied.get("milestones"))
        explicit_tasks = self._as_lines(supplied.get("tasks"))

        raw_steps = explicit_tasks or explicit_milestones or self._extract_requested_steps(prompt)
        if raw_steps:
            step_titles = raw_steps
            plan_basis = "user_supplied_tasks" if explicit_tasks else (
                "user_supplied_milestones" if explicit_milestones else "steps_extracted_from_request"
            )
        else:
            step_titles = self._default_steps(goal)
            plan_basis = "goal_decomposition"

        steps: list[dict[str, Any]] = []
        for index, title in enumerate(step_titles, start=1):
            dependencies = [index - 1] if index > 1 else []
            steps.append({
                "id": index,
                "title": title,
                "action": title if title.endswith((".", "!", "?")) else title + ".",
                "depends_on": dependencies,
                "deliverable": self._deliverable_for(title),
                "verification": self._verification_for(title),
                "status": "not_started",
            })

        checklist = [
            f"[ ] {step['title']}" for step in steps
        ]
        risks = self._risks(constraints, deadline, resources)
        assumptions = []
        if not success_criteria:
            assumptions.append("No explicit success criteria were supplied; define measurable completion conditions before execution.")
        if not deadline:
            assumptions.append("No deadline was supplied; order is based on dependencies rather than calendar dates.")
        if not resources:
            assumptions.append("Available resources were not specified; confirm ownership, tools and access before execution.")

        return {
            "goal": goal,
            "plan_basis": plan_basis,
            "steps": steps,
            "checklist": checklist,
            "constraints": constraints,
            "available_resources": resources,
            "deadline": deadline or None,
            "success_criteria": success_criteria,
            "risks": risks,
            "assumptions": assumptions,
            "execution_status": "plan_only_not_executed",
            "summary": f"Planner created a {len(steps)}-step plan for: {goal}",
        }

    @staticmethod
    def _extract_requested_steps(prompt: str) -> list[str]:
        # Prefer explicit numbered/bulleted actions supplied by the user.
        explicit = re.findall(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s+(.+)", prompt)
        if len(explicit) >= 2:
            return [item.strip().rstrip(".; ") for item in explicit if item.strip()]
        # Parse simple sequential requests without pretending to understand arbitrary prose perfectly.
        body = re.sub(r"^(?:please\s+)?(?:make|create|build|give me|prepare)\s+(?:me\s+)?(?:a\s+)?(?:plan|roadmap|checklist)\s*(?:for|to|of)?\s*", "", prompt, flags=re.I)
        parts = re.split(r"\s*(?:;|\n|\bthen\b|\bafter that\b)\s*", body, flags=re.I)
        cleaned = [part.strip(" .;:-") for part in parts if part.strip(" .;:-")]
        return cleaned if len(cleaned) >= 2 else []

    @staticmethod
    def _default_steps(goal: str) -> list[str]:
        normalized = goal.casefold()
        if any(term in normalized for term in ("agent network", "multi-agent", "multi agent")) and any(
            term in normalized for term in ("test", "verify", "validate", "quality")
        ):
            return [
                "Run the full automated test suite and record the exact pass/fail summary",
                "Check the agent registry and confirm which specialist handlers are actually executable",
                "Run routing smoke tests for Scribe email drafting, Planner test planning and Athena context analysis",
                "Execute one request per specialist and inspect the returned specialist content, not only the parent JARVIS task status",
                "Inspect parent/child task records to confirm each request was delegated to the expected specialist",
                "Test safe failure behavior with external search unavailable and confirm failures are not reported as completed research",
                "Run Python compilation and regression tests, then package the verified project without changing Forge's implementation",
            ]
        return [
            f"Define the outcome and acceptance criteria for {goal}",
            "Inventory prerequisites, dependencies, access and available resources",
            "Break the work into small deliverables and sequence them by dependency",
            "Complete the highest-priority independent task and record its result",
            "Validate each deliverable against the acceptance criteria and resolve gaps",
            "Review remaining risks, document the final state and identify follow-up work",
        ]

    @staticmethod
    def _deliverable_for(title: str) -> str:
        text = title.casefold()
        if any(word in text for word in ("define", "clarify", "criteria", "scope")):
            return "Written goal, scope and acceptance criteria"
        if any(word in text for word in ("inventory", "identify", "prerequisite", "dependency", "resource")):
            return "Dependency and resource list"
        if any(word in text for word in ("validate", "test", "review", "verify")):
            return "Recorded verification results and unresolved gaps"
        if any(word in text for word in ("document", "record", "close", "follow-up")):
            return "Completion notes and follow-up list"
        return "A reviewable output or recorded result for this step"

    @staticmethod
    def _verification_for(title: str) -> str:
        text = title.casefold()
        if any(word in text for word in ("test", "validate", "verify", "review")):
            return "Record the check performed, expected result, actual result and any remaining failure."
        if any(word in text for word in ("define", "clarify", "criteria", "scope")):
            return "Confirm the goal and measurable completion conditions with the requester."
        return "Confirm the deliverable exists, meets its stated purpose and has no blocking dependency."

    @staticmethod
    def _risks(constraints: list[str], deadline: str, resources: list[str]) -> list[str]:
        risks = ["A dependency may block later steps; verify prerequisites before committing to dates."]
        if deadline:
            risks.append("The deadline may be at risk if scope or dependencies change; reserve time for verification.")
        if constraints:
            risks.append("Validate each deliverable against the supplied constraints before marking it complete.")
        if not resources:
            risks.append("Ownership, required access and tooling are unspecified and may delay execution.")
        return risks

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _as_lines(value: Any) -> list[str]:
        if value is None:
            return []
        values = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple, set)) else [value]
        return [str(item).strip() for item in values if str(item).strip()]


def create_handler() -> PlannerAgent:
    return PlannerAgent()
