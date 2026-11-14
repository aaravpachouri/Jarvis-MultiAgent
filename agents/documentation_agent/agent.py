"""Scribe: evidence-grounded drafts plus narrowly authorized report persistence.

Scribe remains draft-only by default. One coordinator-authorized collaboration
workflow may create a new Markdown report under data/reports; this path is
restricted to a fixed filename pattern and uses exclusive creation to avoid
overwriting existing files. Arbitrary file writes remain unsupported.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from core.action_scope import action_scope


class DocumentationAgent:
    """Generate evidence-grounded drafts and a narrowly scoped diagnostic report."""

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
        doc_type = self._document_type(prompt) if prompt else "unknown"
        async with action_scope(context, "scribe.document.draft", action_type="workflow", metadata={"operation": "draft", "format": doc_type}) as action:
            result = await self._draft(task, context)
            if isinstance(result, dict) and result.get("error"):
                action.set_outcome("failed", reason_code="draft_error")
                return result

            # Scribe is draft-only by default. A coordinator may set this narrow
            # flag only for an explicitly authorized workflow. In that case,
            # Scribe can create a NEW Markdown file under data/reports and cannot
            # overwrite files or write to any other directory.
            task_context = getattr(task, "context", {}) or {}
            if isinstance(task_context, Mapping) and task_context.get("persist_report") is True:
                try:
                    written_path = self._persist_authorized_report(task_context, result, context)
                except Exception as exc:
                    action.set_outcome("failed", reason_code="report_persist_failed")
                    result["file_written"] = False
                    result["persist_error"] = type(exc).__name__
                    result["summary"] = "Scribe drafted the report but could not persist it safely."
                    result["draft_only"] = True
                    return result
                result["written_files"] = [written_path]
                result["file_written"] = True
                result["draft_only"] = False
                result["summary"] = f"Scribe wrote the authorized diagnostic report to {written_path}."
            return result

    @staticmethod
    def _persist_authorized_report(task_context: Mapping[str, Any], result: dict[str, Any], context: Any) -> str:
        """Persist one new diagnostic Markdown file under data/reports only."""
        filename = str(task_context.get("persist_report_filename", "")).strip()
        allowed_name = re.fullmatch(
            r"(?:agent_collaboration_test(?:-[0-9]{1,3})?|local_project_diagnostic_[a-z0-9_-]{1,60})\.md",
            filename,
            re.IGNORECASE,
        )
        if not allowed_name:
            raise ValueError("Only a specifically named local diagnostic Markdown report may be persisted.")
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Scribe returned no report content to persist.")

        runtime = getattr(context, "runtime", None)
        audit_log_path = getattr(runtime, "audit_log_path", None)
        if audit_log_path:
            project_root = Path(audit_log_path).expanduser().resolve().parent.parent
        else:
            project_root = Path.cwd().resolve()
        report_dir = (project_root / "data" / "reports").resolve()
        report_dir.mkdir(parents=True, exist_ok=True)

        base = Path(filename).stem
        # Exclusive creation prevents accidental overwrite, even if a task ID or
        # filename is reused. A small numeric suffix handles collisions safely.
        for index in range(0, 1000):
            candidate_name = f"{base}{'-' + str(index) if index else ''}.md"
            target = (report_dir / candidate_name).resolve()
            if target.parent != report_dir:
                raise ValueError("Report path escaped data/reports.")
            relative_path = target.relative_to(project_root).as_posix()
            final_content = content.replace(
                "Draft only: Scribe did not write files, execute commands, or independently verify claims.",
                "Persisted by Scribe to the authorized local report path. Scribe did not execute commands or independently verify claims.",
            )
            final_content = final_content.replace("- File written: No.", f"- File written: Yes (`{relative_path}`).")
            try:
                with action_scope(context, "scribe.report.persist", action_type="file", metadata={"operation": "create", "format": "markdown", "file_count": 1}):
                    with target.open("x", encoding="utf-8", newline="\n") as handle:
                        handle.write(final_content if final_content.endswith("\n") else final_content + "\n")
                result["content"] = final_content
                return relative_path
            except FileExistsError:
                continue
        raise FileExistsError("Could not find an unused collaboration report filename.")

    async def _draft(self, task: Any, context: Any) -> dict[str, Any]:
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
