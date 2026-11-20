"""Scribe: evidence-grounded technical documentation draft generation.

Scribe intentionally returns draft content rather than writing files or claiming
that a README/API document was saved. Filesystem writing is reserved for an
explicitly authorized tool/agent.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


class DocumentationAgent:
    """Generate documentation drafts only from supplied implementation evidence."""

    _TYPE_PATTERNS = (
        ("email", re.compile(r"\b(email|e-mail|draft an email|write to)\b", re.I)),
        ("meeting notes", re.compile(r"\b(meeting notes|minutes of (?:the )?meeting|meeting minutes)\b", re.I)),
        ("report", re.compile(r"\b(report|status report|progress report|analysis report)\b", re.I)),
        ("proposal", re.compile(r"\b(proposal|project proposal)\b", re.I)),
        ("readme", re.compile(r"\b(readme|project overview)\b", re.I)),
        ("installation guide", re.compile(r"\b(install|installation|setup guide|getting started)\b", re.I)),
        ("api reference", re.compile(r"\b(api reference|endpoint documentation|api docs)\b", re.I)),
        ("changelog", re.compile(r"\b(changelog|change log)\b", re.I)),
        ("release notes", re.compile(r"\b(release notes|release summary)\b", re.I)),
        ("architecture document", re.compile(r"\b(architecture|system design)\b", re.I)),
        ("notes", re.compile(r"\b(notes|structured document|organize these points)\b", re.I)),
    )

    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        prompt = str(getattr(task, "prompt", "") or "").strip()
        if not prompt:
            raise ValueError("Scribe requires a non-empty documentation request.")
        task_context = getattr(task, "context", {}) or {}
        if not isinstance(task_context, Mapping):
            raise TypeError("Scribe task context must be a mapping.")

        material = dict(task_context.get("documentation", {})) if isinstance(task_context.get("documentation"), Mapping) else {}
        # Top-level context wins, while a nested documentation object is also supported.
        for key, value in task_context.items():
            if key != "documentation":
                material[key] = value

        doc_type = self._document_type(prompt)
        title = self._first_text(material.get("title"), material.get("subject"), material.get("project_name"), doc_type.title())
        if doc_type in {"email", "report", "meeting notes", "proposal", "notes"}:
            return self._draft_general_document(doc_type, title, prompt, material)
        sections: list[str] = []
        missing: list[str] = []
        evidence = self._as_lines(material.get("source_material"))
        summary = self._first_text(material.get("project_summary"), material.get("summary"))
        features = self._as_lines(material.get("features"))
        install = self._as_lines(material.get("installation_steps"))
        usage = self._as_lines(material.get("usage_examples"))
        api_details = self._as_lines(material.get("api_details"))
        changed = self._as_lines(material.get("files_changed"))
        tests = self._as_lines(material.get("test_results"))
        limitations = self._as_lines(material.get("limitations"))

        sections.append(f"# {title}")
        sections.append("")
        sections.append(f"> Draft type: {doc_type}. Generated from the context supplied with this task.")
        sections.append("> This agent creates draft text only; it does not write files, run commands, or verify claims independently.")

        if summary:
            sections.extend(["", "## Overview", "", summary])
        else:
            missing.append("project_summary")

        if features:
            sections.extend(["", "## Features", ""] + [f"- {item}" for item in features])
        elif doc_type in {"readme", "architecture document"}:
            missing.append("features")

        if install or doc_type == "installation guide":
            sections.extend(["", "## Installation", ""])
            if install:
                sections.extend(f"{i}. {item}" for i, item in enumerate(install, 1))
            else:
                sections.append("Installation steps were not supplied. Add verified environment requirements and commands here.")
                missing.append("installation_steps")

        if usage:
            sections.extend(["", "## Usage", ""] + [f"- {item}" for item in usage])
        elif doc_type in {"readme", "installation guide"}:
            missing.append("usage_examples")

        if api_details or doc_type == "api reference":
            sections.extend(["", "## API Reference", ""])
            if api_details:
                sections.extend(f"- {item}" for item in api_details)
            else:
                sections.append("No endpoint signatures or schemas were supplied. Add verified routes, parameters, responses, and errors here.")
                missing.append("api_details")

        if changed or tests or doc_type in {"changelog", "release notes"}:
            sections.extend(["", "## Changes and Verification", ""])
            if changed:
                sections.extend(f"- Changed: `{item}`" for item in changed)
            if tests:
                sections.extend(f"- Test evidence supplied: {item}" for item in tests)
            else:
                sections.append("No test results were supplied; test status has not been verified by Scribe.")
                if doc_type in {"changelog", "release notes"}:
                    missing.append("test_results")

        if evidence:
            sections.extend(["", "## Source Material", ""] + [f"- {item}" for item in evidence])
        elif not summary and not features and not install and not usage and not api_details and not changed:
            missing.append("source_material")

        if limitations:
            sections.extend(["", "## Limitations", ""] + [f"- {item}" for item in limitations])

        missing = list(dict.fromkeys(missing))
        sections.extend(["", "## Draft Status", "", "- File written: No.", "- Commands executed: No.", "- Independent fact-check performed: No."])
        if missing:
            sections.append("- Missing context: " + ", ".join(f"`{item}`" for item in missing) + ".")
        else:
            sections.append("- Required context fields detected for this draft; claims still require human review.")

        return {
            "document_type": doc_type,
            "title": title,
            "content": "\n".join(sections).strip() + "\n",
            "draft_only": True,
            "written_files": [],
            "commands_executed": [],
            "independently_verified": False,
            "missing_context": missing,
            "source_material_count": len(evidence),
            "summary": f"Scribe generated a {doc_type} draft; no files were written.",
        }

    @classmethod
    def _draft_general_document(
        cls, doc_type: str, title: str, prompt: str, material: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Draft common writing formats using only supplied fields and evidence."""
        summary = cls._first_text(material.get("purpose"), material.get("project_summary"), material.get("summary"), "")
        body = cls._first_text(material.get("body"), material.get("message"), material.get("content"), "")
        key_points = cls._as_lines(material.get("key_points", material.get("features")))
        action_items = cls._as_lines(material.get("action_items", material.get("tasks")))
        decisions = cls._as_lines(material.get("decisions"))
        attendees = cls._as_lines(material.get("attendees"))
        limitations = cls._as_lines(material.get("limitations"))
        missing: list[str] = []
        lines: list[str] = []

        if doc_type == "email":
            recipient = cls._first_text(material.get("recipient"), material.get("to"), "[Recipient]")
            subject = cls._first_text(material.get("subject"), "[Subject]")
            lines.extend([f"To: {recipient}", f"Subject: {subject}", "", cls._first_text(material.get("salutation"), f"Hello {recipient},"), ""])
            if summary:
                lines.extend([summary, ""])
            if body:
                lines.extend([body, ""])
            if key_points:
                lines.extend(["Key points:", ""] + [f"- {item}" for item in key_points] + [""])
            if not summary and not body and not key_points:
                lines.extend([
                    "I'm reaching out regarding [topic/purpose].",
                    "",
                    "[Add the key message, relevant context, and any requested action or deadline.]",
                    "",
                ])
                missing.append("purpose_or_body_or_key_points")
            lines.extend([cls._first_text(material.get("closing"), "Best regards,"), cls._first_text(material.get("sender"), "[Your name]")])
        else:
            lines.extend([f"# {title}", "", f"> Draft type: {doc_type}. Generated only from supplied context; not independently verified."])
            if summary:
                lines.extend(["", "## Purpose / Summary", "", summary])
            else:
                missing.append("purpose_or_summary")
            if doc_type == "meeting notes":
                meeting_date = cls._first_text(material.get("date"), material.get("meeting_date"), "")
                if meeting_date:
                    lines.extend(["", f"**Date:** {meeting_date}"])
                if attendees:
                    lines.extend(["", "## Attendees", ""] + [f"- {item}" for item in attendees])
            if key_points:
                lines.extend(["", "## Key Points", ""] + [f"- {item}" for item in key_points])
            if decisions:
                lines.extend(["", "## Decisions", ""] + [f"- {item}" for item in decisions])
            if action_items:
                lines.extend(["", "## Action Items", ""] + [f"- [ ] {item}" for item in action_items])
            if body:
                lines.extend(["", "## Details", "", body])
            if not any((summary, key_points, decisions, action_items, body)):
                missing.append("source_material_or_key_points")
        if limitations:
            lines.extend(["", "## Limitations", ""] + [f"- {item}" for item in limitations])
        lines.extend(["", "---", "Draft only: Scribe did not write files, execute commands, or independently verify claims."])
        if missing:
            lines.append("Missing context: " + ", ".join(f"`{item}`" for item in dict.fromkeys(missing)) + ".")
        return {
            "document_type": doc_type,
            "title": title,
            "content": "\n".join(lines).strip() + "\n",
            "draft_only": True,
            "written_files": [],
            "commands_executed": [],
            "independently_verified": False,
            "missing_context": list(dict.fromkeys(missing)),
            "source_material_count": len(cls._as_lines(material.get("source_material"))),
            "summary": f"Scribe generated a {doc_type} draft; no files were written.",
        }

    @classmethod
    def _document_type(cls, prompt: str) -> str:
        for name, pattern in cls._TYPE_PATTERNS:
            if pattern.search(prompt):
                return name
        return "technical documentation"

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
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = [value]
        return [str(item).strip() for item in values if str(item).strip()]


def create_handler() -> DocumentationAgent:
    """Return a real, safe, executable documentation-drafting handler."""
    return DocumentationAgent()
