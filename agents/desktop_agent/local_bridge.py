"""Local Windows desktop companion with risk-based approval for JARVIS Atlas.

Routine tasks run directly. Sensitive actions pause for a native Windows approval
dialog. The service remains loopback-only and accepts structured actions only.
"""
from __future__ import annotations

import hmac
import io
import json
import os
import platform
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse, quote_plus


HOST = "127.0.0.1"
PORT = 8766
MAX_BODY_BYTES = 16_384
MAX_TASK_CHARS = 3_000
MAX_ACTIONS = 8
APPROVAL_TIMEOUT_SECONDS = 120
DEFAULT_ORIGINS = {
    "https://jarvis-multiagent-dashboard.vercel.app",
    "https://jarvis-multiagent-dashboard-aaravpachouri.vercel.app",
    "https://jarvis-multiagent-dashboard-git-publish-ja-77d6ea-aaravpachouri.vercel.app",
}
APP_ALIASES = {
    "notepad": "notepad",
    "calculator": "calculator",
    "calc": "calculator",
    "file explorer": "file_explorer",
    "explorer": "file_explorer",
    "paint": "paint",
    "mspaint": "paint",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "edge",
    "microsoft edge": "edge",
    "firefox": "firefox",
    "vscode": "vscode",
    "visual studio code": "vscode",
}
APP_PATTERN = "|".join(re.escape(name) for name in sorted(APP_ALIASES, key=len, reverse=True))
SAFE_HOTKEYS = {
    "ctrl+l": ("ctrl", "l"),
    "ctrl+t": ("ctrl", "t"),
    "ctrl+f": ("ctrl", "f"),
    "ctrl+c": ("ctrl", "c"),
    "ctrl+v": ("ctrl", "v"),
    "ctrl+a": ("ctrl", "a"),
    "ctrl+s": ("ctrl", "s"),
    "ctrl+shift+s": ("ctrl", "shift", "s"),
    "ctrl+shift+n": ("ctrl", "shift", "n"),
    "alt+tab": ("alt", "tab"),
    "alt+left": ("alt", "left"),
    "alt+right": ("alt", "right"),
    "enter": ("enter",),
    "tab": ("tab",),
    "escape": ("esc",),
    "esc": ("esc",),
    "space": ("space",),
    "up": ("up",),
    "down": ("down",),
    "left": ("left",),
    "right": ("right",),
    "home": ("home",),
    "end": ("end",),
}
EXAMPLE_TASKS = [
    "open notepad",
    "open calculator",
    "open notepad and type Hello from JARVIS",
    "open https://www.wikipedia.org",
    "search for Python 3.12 release notes",
    "click 620, 420",
    "press ctrl+l",
]


class DesktopAgentError(ValueError):
    """A task is invalid, unsupported, or cannot safely be executed."""


def _normalise_task(value: Any) -> str:
    if not isinstance(value, str):
        raise DesktopAgentError("Task must be text.")
    task = value.strip()
    task = re.sub(r"^(?:(?:hey|hello)\s+)?jarvis[\s,:-]+", "", task, flags=re.IGNORECASE)
    task = re.sub(r"^please\s+", "", task, flags=re.IGNORECASE).strip()
    if not task:
        raise DesktopAgentError("Enter a desktop task first.")
    if len(task) > MAX_TASK_CHARS:
        raise DesktopAgentError("Task is too long. Keep it under 3,000 characters.")
    return task


def validate_url(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("www."):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DesktopAgentError("Only ordinary http or https web addresses can be opened.")
    if parsed.username or parsed.password:
        raise DesktopAgentError("Web addresses containing embedded credentials are not allowed.")
    if parsed.hostname.casefold() in {"localhost", "127.0.0.1", "::1"}:
        raise DesktopAgentError("Opening a local/private web service from this command is disabled.")
    if any(ord(ch) < 32 for ch in value):
        raise DesktopAgentError("The web address contains an invalid control character.")
    return value


def _parse_one(task: str, app_context: bool = False) -> list[dict[str, Any]]:
    text = task.strip().strip(" .")
    if not text:
        raise DesktopAgentError("One of the steps is empty.")

    match = re.fullmatch(
        rf"(?:open|launch|start)\s+({APP_PATTERN})(?:\s+(?:and\s+then|then|and)\s+(?:type|write|enter)\s+(.+))?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        app = APP_ALIASES[match.group(1).casefold()]
        actions: list[dict[str, Any]] = [{"type": "open_app", "app": app}]
        typed = match.group(2)
        if typed is not None:
            typed = typed.strip()
            if len(typed) >= 2 and typed[0] == typed[-1] and typed[0] in {"'", '"'}:
                typed = typed[1:-1]
            if not typed:
                raise DesktopAgentError("There is no text to type.")
            actions.append({"type": "type_text", "text": typed})
        return actions

    search_match = re.fullmatch(
        r"(?:search(?:\s+the\s+web)?\s+for|google)\s+(.+)",
        text,
        flags=re.IGNORECASE,
    )
    if search_match:
        query = search_match.group(1).strip().strip("'\"")
        if not query:
            raise DesktopAgentError("Enter a search query.")
        if app_context:
            return [{"type": "smart_type", "text": query, "target": "search"}]
        return [{"type": "open_url", "url": "https://www.google.com/search?q=" + quote_plus(query)}]

    search_field_type = re.fullmatch(
        r"(?:type|write|enter)(?:\s+text)?\s+(.+?)\s+(?:into|in)\s+(?:the\s+)?(?:search\s+bar|search\s+box|search\s+field|search\s+input)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if search_field_type:
        value = search_field_type.group(1).strip().strip("'\"")
        if not value:
            raise DesktopAgentError("Enter the text to search for.")
        return [{"type": "smart_type", "text": value, "target": "search"}]

    search_field_focus = re.fullmatch(
        r"(?:focus|select|go\s+to|open|click|tap)(?:\s+on)?\s+(?:the\s+)?(?:search\s+bar|search\s+box|search\s+field|search\s+input)",
        text, re.IGNORECASE,
    )
    if search_field_focus:
        return [{"type": "focus_search"}]

    named_result = re.fullmatch(
        r"(?:click|select|open|choose|play|tap)\s+(?:on\s+)?(?:the\s+)?(?:search\s+)?result\s+(.+)",
        text, re.IGNORECASE,
    )
    if named_result:
        target = re.sub(r"^(?:named|called)\s+", "", named_result.group(1).strip(), flags=re.IGNORECASE).strip("'\"")
        if not target:
            raise DesktopAgentError("Name the result you want to select.")
        return [{"type": "click_result", "target": target}]

    first_result = re.fullmatch(
        r"(?:click|select|open|choose|play|tap)\s+(?:on\s+)?(?:the\s+)?(?:(?:first|top|matching)\s+)?(?:search\s+)?result",
        text, re.IGNORECASE,
    )
    if first_result:
        return [{"type": "click_first_result"}]

    url_match = re.fullmatch(r"(?:open|visit|go to|navigate to)\s+(https?://\S+|www\.\S+)", text, re.IGNORECASE)
    if url_match:
        return [{"type": "open_url", "url": validate_url(url_match.group(1))}]

    # Any installed app discoverable through Windows Start can be launched by name.
    generic_open = re.fullmatch(
        r"(?:open|launch|start|switch to|focus|bring up)\s+(.+?)(?:\s+(?:and then|then|and)\s+(?:type|write|enter)\s+(.+))?",
        text, re.IGNORECASE | re.DOTALL,
    )
    if generic_open:
        app, typed = generic_open.group(1).strip().strip("'\""), generic_open.group(2)
        if not app or len(app) > 100 or re.search(r"[\x00-\x1f/\\;&|<>]", app):
            raise DesktopAgentError("Use a simple installed application name, not a command or file path.")
        actions = [{"type": "open_app", "app": APP_ALIASES.get(app.casefold(), app)}]
        if typed is not None:
            typed = typed.strip()
            if len(typed) >= 2 and typed[0] == typed[-1] and typed[0] in {"'", '"'}:
                typed = typed[1:-1]
            if not typed:
                raise DesktopAgentError("There is no text to type.")
            actions.append({"type": "type_text", "text": typed})
        return actions

    type_into_match = re.fullmatch(
        r"(?:type|write|enter)(?:\s+text)?\s+(.+?)\s+into\s+(" + APP_PATTERN + ")",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if type_into_match:
        value = type_into_match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        app = APP_ALIASES[type_into_match.group(2).casefold()]
        if not value:
            raise DesktopAgentError("There is no text to type.")
        return [
            {"type": "open_app", "app": app},
            {"type": "type_text", "text": value},
        ]

    type_into_any = re.fullmatch(
        r"(?:type|write|enter)(?:\s+text)?\s+(.+?)\s+into\s+(.+)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if type_into_any:
        value, app = type_into_any.group(1).strip(), type_into_any.group(2).strip().strip("'\"")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or not app or len(app) > 100 or re.search(r"[\x00-\x1f/\\;&|<>]", app):
            raise DesktopAgentError("Use a simple app name and non-empty text.")
        return [{"type": "open_app", "app": app}, {"type": "type_text", "text": value}]

    type_match = re.fullmatch(r"(?:type|write|enter)(?:\s+text)?\s+(.+)", text, re.IGNORECASE | re.DOTALL)
    if type_match:
        value = type_match.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value:
            raise DesktopAgentError("There is no text to type.")
        return [{"type": "type_text", "text": value}]

    ui_click_match = re.fullmatch(
        r"click\s+(?:the\s+)?(?:(button|link)\s+[\"']?(.+?)[\"']?|(.+?)\s+(button|link))\s+in\s+(" + APP_PATTERN + ")",
        text,
        re.IGNORECASE,
    )
    if ui_click_match:
        control = (ui_click_match.group(1) or ui_click_match.group(4)).casefold()
        label = (ui_click_match.group(2) or ui_click_match.group(3)).strip().strip("'\\\"")
        label = re.sub(r"^the\s+", "", label, flags=re.IGNORECASE)
        app = APP_ALIASES[ui_click_match.group(5).casefold()]
        if not label or len(label) > 100:
            raise DesktopAgentError("UI control names must be between 1 and 100 characters.")
        return [
            {"type": "open_app", "app": app},
            {"type": "click_ui", "control": control, "text": label},
        ]

    double_click = re.fullmatch(r"(?:double[\s-]+click)(?:\s+(?:at|on))?\s+(\d{1,5})\s*(?:,|\s)\s*(\d{1,5})", text, re.IGNORECASE)
    if double_click:
        return [{"type": "double_click", "x": int(double_click.group(1)), "y": int(double_click.group(2))}]

    drag = re.fullmatch(r"drag\s+(\d{1,5})\s*,\s*(\d{1,5})\s+to\s+(\d{1,5})\s*,\s*(\d{1,5})", text, re.IGNORECASE)
    if drag:
        return [{"type": "drag", "x1": int(drag.group(1)), "y1": int(drag.group(2)), "x2": int(drag.group(3)), "y2": int(drag.group(4))}]

    # Generic named controls are available in any foreground app, not only the original app list.
    named_control = re.fullmatch(
        r"click\s+(?:the\s+)?(button|link)\s+(.+?)(?:\s+in\s+(.+))?",
        text, re.IGNORECASE,
    )
    if named_control:
        control = named_control.group(1).casefold()
        label = named_control.group(2).strip().strip("'\"")
        app_name = (named_control.group(3) or "").strip().strip("'\"")
        if not label or len(label) > 100:
            raise DesktopAgentError("UI control names must be between 1 and 100 characters.")
        actions = []
        if app_name:
            if len(app_name) > 100 or re.search(r"[\x00-\x1f/\\;&|<>]", app_name):
                raise DesktopAgentError("Use a simple app name.")
            actions.append({"type": "open_app", "app": APP_ALIASES.get(app_name.casefold(), app_name)})
        actions.append({"type": "click_ui", "control": control, "text": label})
        return actions

    click_match = re.fullmatch(
        r"(?:click(?:\s+(?:at|on))?|right\s+click(?:\s+(?:at|on))?)\s+(\d{1,5})\s*(?:,|\s)\s*(\d{1,5})(?:\s+(left|right))?",
        text, re.IGNORECASE,
    )
    if click_match:
        right_prefix = text.casefold().startswith("right click")
        return [{
            "type": "click",
            "x": int(click_match.group(1)),
            "y": int(click_match.group(2)),
            "button": ("right" if right_prefix else (click_match.group(3) or "left")).casefold(),
        }]

    scroll_match = re.fullmatch(r"scroll\s+(up|down)(?:\s+(\d{1,4}))?", text, re.IGNORECASE)
    if scroll_match:
        amount = int(scroll_match.group(2) or 500)
        if not 50 <= amount <= 1500:
            raise DesktopAgentError("Scroll amount must be between 50 and 1,500.")
        return [{"type": "scroll", "amount": amount if scroll_match.group(1).casefold() == "up" else -amount}]

    if re.fullmatch(r"(?:(?:press|hit|tap)\s+)?(?:the\s+)?enter(?:\s+key)?", text, re.IGNORECASE):
        return [{"type": "hotkey", "keys": list(SAFE_HOTKEYS["enter"])}]

    hotkey_match = re.fullmatch(r"(?:press|hotkey)\s+([a-z0-9+_-]+)", text, re.IGNORECASE)
    if hotkey_match:
        key = hotkey_match.group(1).casefold().replace("control+", "ctrl+")
        if key not in SAFE_HOTKEYS:
            allowed = ", ".join(sorted(SAFE_HOTKEYS))
            raise DesktopAgentError("That keyboard shortcut is not allowed. Supported shortcuts: " + allowed)
        return [{"type": "hotkey", "keys": list(SAFE_HOTKEYS[key])}]

    wait_match = re.fullmatch(r"wait(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)?", text, re.IGNORECASE)
    if wait_match:
        seconds = float(wait_match.group(1))
        if not 0 < seconds <= 5:
            raise DesktopAgentError("Wait steps must be between 0 and 5 seconds.")
        return [{"type": "wait", "seconds": seconds}]

    if re.fullmatch(r"(?:go\s+back|navigate\s+back|back\s+to\s+previous\s+page)", text, re.IGNORECASE):
        return [{"type": "hotkey", "keys": ["alt", "left"]}]
    if re.fullmatch(r"(?:go\s+forward|navigate\s+forward)", text, re.IGNORECASE):
        return [{"type": "hotkey", "keys": ["alt", "right"]}]

    if re.search(r"\b(?:powershell|cmd\.exe|command prompt|run command|shell command|shutdown|restart computer|delete files|uninstall|install software|send email|transfer money|place an order|make a payment)\b", text, re.IGNORECASE):
        raise DesktopAgentError(
            "That request is outside the desktop agent's safe action set. "
            "Shell commands, destructive operations, external sends and payments are not supported."
        )
    raise DesktopAgentError(
        "I couldn't safely map that to a supported desktop action. Try opening an allowlisted app, "
        "typing text, opening a web address, searching the web, clicking screen coordinates, or "
        "using an approved shortcut."
    )


def parse_task(
    value: Any,
    app_context: bool = False,
    last_search_query: str = "",
) -> list[dict[str, Any]]:
    """Translate a command into actions, optionally continuing an existing app workflow."""
    task = _normalise_task(value)
    # Split a natural-language request into sequential commands while leaving words such
    # as "rock and roll" inside search text untouched.
    splitter = r"\s*(?:;|\n+)\s*|\s+(?:and\s+then|then)\s+|,\s*(?=(?:and\s+)?(?:search|find|type|write|enter|press|click|tap|scroll|focus|go\s+to|wait|open|launch|start)\b)|\s+and\s+(?=(?:search|find|type|write|enter|press|click|tap|scroll|focus|go\s+to|wait)\b)"
    segments = [
        re.sub(r"^(?:and\s+then|and)\s+", "", part.strip(" ,\t"), flags=re.IGNORECASE)
        for part in re.split(splitter, task, flags=re.IGNORECASE)
        if part.strip(" ,\t") and re.sub(r"^(?:and\s+then|and)\s+", "", part.strip(" ,\t"), flags=re.IGNORECASE)
    ]
    actions: list[dict[str, Any]] = []
    # A new dashboard submission may continue a prior command. The caller supplies
    # the live desktop context so "search for X" after "open YouTube" stays in that
    # browser/app instead of silently opening a separate Google tab.
    app_context = bool(app_context)
    last_search_query = str(last_search_query or "")
    for segment in segments:
        step_actions = _parse_one(segment, app_context=app_context)
        for action in step_actions:
            if action.get("type") in {"open_app", "open_url"}:
                # After opening a site, subsequent "search for ..." instructions
                # should search inside that page instead of opening a second tab
                # with a separate Google search.
                app_context = True
            elif action.get("type") == "smart_type" and action.get("target") == "search":
                last_search_query = str(action.get("text", ""))
            elif action.get("type") == "click_first_result" and last_search_query:
                action["target"] = last_search_query
        actions.extend(step_actions)
        if len(actions) > MAX_ACTIONS:
            raise DesktopAgentError("A task can contain at most 8 actions.")
    if not actions:
        raise DesktopAgentError("No executable actions were found.")
    if len(actions) > MAX_ACTIONS:
        raise DesktopAgentError("A task can contain at most 8 actions.")
    return actions


def action_summary(actions: list[dict[str, Any]], active_window: str = "") -> str:
    """Build a human-readable approval summary without hiding text entry."""
    rows = []
    for index, action in enumerate(actions, 1):
        kind = action.get("type")
        if kind == "open_app":
            label = "Open application: " + str(action.get("app", "unknown"))
        elif kind == "open_url":
            label = "Open website: " + str(action.get("url", ""))
        elif kind == "type_text":
            value = str(action.get("text", ""))
            preview = value if len(value) <= 420 else value[:420] + " … [text truncated in preview]"
            label = "Type text: " + repr(preview)
        elif kind == "smart_type":
            value = str(action.get("text", ""))
            preview = value if len(value) <= 280 else value[:280] + " … [text truncated in preview]"
            label = "Find the app's search field and type: " + repr(preview)
        elif kind == "focus_search":
            label = "Find and focus the best matching search field in the current app"
        elif kind in {"click_result", "click_first_result"}:
            target = str(action.get("target", "")).strip()
            label = ("Click visible result matching " + repr(target)) if target else "Click the first matching result in the current app"
        elif kind in {"click", "double_click"}:
            label = ("Double-click" if kind == "double_click" else "Click") + " screen position (" + str(action.get("x")) + ", " + str(action.get("y")) + ")"
        elif kind == "drag":
            label = "Drag from (" + str(action.get("x1")) + ", " + str(action.get("y1")) + ") to (" + str(action.get("x2")) + ", " + str(action.get("y2")) + ")"
        elif kind == "scroll":
            amount = int(action.get("amount", 0))
            label = "Scroll " + ("up " if amount > 0 else "down ") + str(abs(amount)) + " units"
        elif kind == "click_ui":
            label = "Click " + str(action.get("control", "button")) + " named " + repr(str(action.get("text", ""))) + " in the foreground app"
        elif kind == "screenshot":
            label = "Capture a screenshot of the current desktop"
        elif kind == "hotkey":
            label = "Press shortcut: " + "+".join(str(key) for key in action.get("keys", []))
        elif kind == "wait":
            label = "Wait " + str(action.get("seconds")) + " seconds"
        else:
            label = "Unsupported action"
        rows.append(str(index) + ". " + label)
    target = "Current foreground window: " + (active_window or "title unavailable")
    return target + "\n\n" + "\n".join(rows) + "\n\nApprove this exact sequence? No action will run unless you approve on this computer."


def approval_reason(task: str, actions: list[dict[str, Any]], active_window: str = "") -> str | None:
    """Classify whether a task needs a native Windows approval prompt."""
    sensitive = re.compile(
        r"\b(?:send|submit|publish|post|purchase|buy|pay|payment|transfer|delete|erase|remove|"
        r"format|shutdown|restart|reboot|uninstall|install software|powershell|command prompt|"
        r"terminal|cmd\.exe|registry|credential|password|passcode|otp|api key|secret|bank|"
        r"credit card|debit card|upload|share|grant access|disable firewall|turn off antivirus)\b",
        re.IGNORECASE,
    )
    sensitive_window = re.compile(
        r"\b(?:powershell|command prompt|windows terminal|terminal|registry|credential|"
        r"windows security|administrator|admin tools|task manager|sign in|login|password|"
        r"checkout|payment|banking|wallet)\b",
        re.IGNORECASE,
    )
    sensitive_control = re.compile(
        r"\b(?:send|submit|publish|post|delete|remove|erase|purchase|buy|pay|checkout|"
        r"transfer|confirm|allow|install|uninstall|share|upload|grant|revoke|reset)\b",
        re.IGNORECASE,
    )
    if any(action.get("type") == "screenshot" for action in actions):
        return "Screen capture can expose private information."
    if sensitive.search(task):
        return "The instruction contains a sensitive or consequential operation."
    if sensitive_window.search(active_window or ""):
        return "The foreground window appears sensitive."
    for action in actions:
        kind = action.get("type")
        if kind == "open_app" and sensitive_window.search(str(action.get("app", ""))):
            return "Opening a system or administrative utility needs approval."
        if kind in {"click", "double_click", "drag"}:
            return "Coordinate input has an ambiguous target and needs approval."
        if kind in {"click_result", "click_first_result"} and sensitive_control.search(str(action.get("target", ""))):
            return "The selected result may trigger a consequential action."
        if kind == "click_ui" and sensitive_control.search(str(action.get("text", ""))):
            return "This named control may perform a consequential action."
        if kind in {"type_text", "smart_type"}:
            value = str(action.get("text", ""))
            if len(value) > 1000 or sensitive.search(value):
                return "Long or sensitive text entry needs approval."
    return None


def _config_dir() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if base:
        return Path(base) / "JARVIS" / "desktop-agent"
    return Path.home() / ".jarvis" / "desktop-agent"


def load_or_create_token() -> str:
    folder = _config_dir()
    path = folder / "pairing-token.txt"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 40:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(48)
    temp = path.with_suffix(".tmp")
    temp.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def allowed_origins() -> set[str]:
    origins = set(DEFAULT_ORIGINS)
    extra = os.getenv("JARVIS_DESKTOP_ALLOWED_ORIGINS", "")
    origins.update(part.strip().rstrip("/") for part in extra.split(",") if part.strip())
    return origins


class DesktopActionExecutor:
    """Execute parsed desktop actions with risk-based approval handled by the bridge."""

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend
        # Preserve search context across separate dashboard submissions in the same session.
        self._last_search_query = ""
        self._last_app_name = ""
        self._last_opened_url = ""
        # The allowed dashboard origin tells Atlas when the foreground tab is the
        # controller itself; target sites must open in a separate tab in that case.
        self._controller_origin = ""
        self._last_target_browser_url = ""
        self._last_target_window_title = ""
        self._last_target_tab_runtime_id: tuple[Any, ...] | None = None
        # Result clicks wait for the interface to react to submission instead of
        # selecting a stale result immediately after Enter.
        self._last_result_snapshot: tuple[Any, ...] | None = None
        self._last_search_submitted_at = 0.0
        self._search_navigation_completed = False

    def _get_backend(self):
        if self._backend is not None:
            return self._backend
        if platform.system() != "Windows":
            raise DesktopAgentError("The desktop companion currently supports Windows 10/11.")
        try:
            import pyautogui
        except ImportError as exc:
            raise DesktopAgentError('Desktop dependencies are missing. Run run_desktop_agent.bat to install them.') from exc
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0
        self._backend = pyautogui
        return self._backend

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": True,
            "platform": platform.system(),
            "host": HOST,
            "port": PORT,
            "desktop_available": False,
            "failsafe": "Move the mouse to the top-left corner to trigger PyAutoGUI's failsafe.",
            "apps": sorted(set(APP_ALIASES.values())),
        }
        if platform.system() != "Windows":
            result["message"] = "This companion must run on the Windows computer you want to control."
            return result
        try:
            backend = self._get_backend()
            size = backend.size()
            result["desktop_available"] = True
            result["screen"] = {"width": int(size.width), "height": int(size.height)}
            try:
                window = backend.getActiveWindow()
                result["active_window"] = str(window.title)[:160] if window else "Unknown"
            except Exception:
                result["active_window"] = "Unknown"
        except Exception as exc:
            result["message"] = str(exc)
        return result

    @staticmethod
    def _find_app(app: str) -> str:
        app = APP_ALIASES.get(str(app).casefold(), str(app))
        simple = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "file_explorer": "explorer.exe",
            "paint": "mspaint.exe",
        }
        if app in simple:
            return simple[app]
        program_files = os.getenv("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.getenv("LOCALAPPDATA", "")
        candidates = {
            "chrome": [
                Path(program_files) / "Google/Chrome/Application/chrome.exe",
                Path(program_files_x86) / "Google/Chrome/Application/chrome.exe",
                Path(local) / "Google/Chrome/Application/chrome.exe",
            ],
            "edge": [
                Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe",
                Path(program_files) / "Microsoft/Edge/Application/msedge.exe",
            ],
            "firefox": [
                Path(program_files) / "Mozilla Firefox/firefox.exe",
                Path(program_files_x86) / "Mozilla Firefox/firefox.exe",
            ],
            "vscode": [
                Path(local) / "Programs/Microsoft VS Code/Code.exe",
                Path(program_files) / "Microsoft VS Code/Code.exe",
            ],
        }
        for candidate in candidates.get(app, []):
            if candidate.is_file():
                return str(candidate)
        binary = {"chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe", "vscode": "Code.exe"}.get(app)
        found = shutil.which(binary) if binary else None
        if found:
            return found
        return None

    @staticmethod
    def _control_text(control: Any, property_name: str) -> str:
        try:
            value = getattr(control.element_info, property_name, "")
            return str(value or "")
        except Exception:
            return ""

    @staticmethod
    def _is_visible_enabled(control: Any) -> bool:
        try:
            if not control.is_visible() or not control.is_enabled():
                return False
        except Exception:
            return False
        try:
            rect = control.rectangle()
            if rect.width() < 8 or rect.height() < 8:
                return False
        except Exception:
            return False
        return True

    def _active_ui_controls(self, allowed_types: set[str]) -> tuple[Any, list[tuple[Any, str, str, str]]]:
        """Return visible UI Automation controls from the active app window."""
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise DesktopAgentError(
                "Adaptive app control needs pywinauto. Run run_desktop_agent.bat to install desktop dependencies."
            ) from exc
        try:
            active = Desktop(backend="uia").get_active()
            wrappers = active.descendants()
        except Exception as exc:
            raise DesktopAgentError("Could not inspect the active app's accessible controls.") from exc

        found: list[tuple[Any, str, str, str]] = []
        for control in wrappers:
            kind = self._control_text(control, "control_type")
            if kind not in allowed_types or not self._is_visible_enabled(control):
                continue
            name = self._control_text(control, "name") or str(getattr(control, "window_text", lambda: "")() or "")
            automation_id = self._control_text(control, "automation_id")
            help_text = self._control_text(control, "help_text")
            metadata = " ".join((name, automation_id, help_text, self._control_text(control, "class_name"))).strip().casefold()
            found.append((control, kind, name.strip(), metadata))
        return active, found

    def _find_search_field(self, timeout_seconds: float = 8.0) -> tuple[Any, Any, str]:
        """Discover a search/address field from its accessibility metadata, app-agnostically."""
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            active, controls = self._active_ui_controls({"Edit", "ComboBox", "TextBox"})
            scored: list[tuple[int, Any, str, str]] = []
            generic: list[tuple[Any, str, str]] = []
            for control, kind, name, metadata in controls:
                # Never use a lone credential/payment field as a fallback search field.
                if re.search(r"\b(password|passcode|one.time.code|username|email address|credit card|security code)\b", metadata):
                    continue
                # Browser omniboxes are not the current website's search field.
                # Do not use an address bar as a generic fallback; it can receive text
                # without actually submitting a search on the current page.
                is_browser_address = bool(
                    re.search(r"\b(address bar|address and search bar|url bar|web address|omnibox)\b", metadata)
                )
                if not is_browser_address:
                    generic.append((control, kind, name))
                score = 0
                if not is_browser_address:
                    if re.search(r"\b(search|find)\b", metadata):
                        score += 100
                    if re.search(r"\b(query|lookup)\b", metadata):
                        score += 85
                    if re.search(r"\b(filter)\b", metadata):
                        score += 65
                    if "url" in metadata:
                        score += 55
                    if kind == "ComboBox":
                        score += 5
                    if kind == "Edit":
                        score += 3
                    if score > 0:
                        scored.append((score, control, name, kind))

            if scored:
                scored.sort(key=lambda item: item[0], reverse=True)
                top_score = scored[0][0]
                close = [item for item in scored if top_score - item[0] < 18]
                if len(close) > 1:
                    labels = [item[2] or item[3] for item in close[:4]]
                    raise DesktopAgentError(
                        "I found several equally plausible search fields: " + ", ".join(labels) +
                        ". Click the intended field once, or specify a named field."
                    )
                return active, scored[0][1], scored[0][2] or "search field"

            if len(generic) == 1:
                return active, generic[0][0], generic[0][2] or "the app's only text field"
            if len(generic) > 1:
                last_error = "This app exposes multiple text fields but doesn't label a clear search field."
            else:
                last_error = "No accessible search field is exposed yet."
            time.sleep(0.3)

        raise DesktopAgentError(
            last_error + " I won't guess where to type. Click the intended field or use the app's visible search control."
        )

    @staticmethod
    def _result_tree_signature(active: Any, controls: list[tuple[Any, str, str, str]]) -> tuple[Any, ...]:
        """Create a lightweight, app-agnostic signature of the active UI tree."""
        try:
            window_title = str(getattr(active, "window_text", lambda: "")() or "")
        except Exception:
            window_title = ""
        labels = tuple(sorted(
            (kind, re.sub(r"\s+", " ", name).casefold().strip())
            for _, kind, name, _ in controls
            if name and name.strip()
        ))
        return (window_title.casefold().strip(), labels)

    def _capture_result_snapshot(self) -> tuple[Any, ...] | None:
        """Capture the current accessible result tree before a submit key is sent."""
        allowed = {
            "ListItem", "DataItem", "TreeItem", "Hyperlink", "Button",
            "Text", "Group", "Custom", "Image",
        }
        try:
            active, controls = self._active_ui_controls(allowed)
            return self._result_tree_signature(active, controls)
        except Exception:
            # Some applications expose no UI Automation tree. The time-and-stability
            # checks in _find_result_control still apply when a snapshot is unavailable.
            return None

    def _find_browser_address_bar(self, timeout_seconds: float = 1.5) -> tuple[Any, Any, str]:
        """Find a browser's address/omnibox control by accessibility metadata."""
        deadline = time.monotonic() + timeout_seconds
        address_pattern = re.compile(
            r"\b(address bar|address and search bar|url bar|web address|omnibox|location bar)\b",
            re.IGNORECASE,
        )
        while time.monotonic() < deadline:
            active, controls = self._active_ui_controls({"Edit", "ComboBox", "TextBox"})
            for control, kind, name, metadata in controls:
                if address_pattern.search(metadata):
                    return active, control, name or "browser address bar"
            time.sleep(0.2)
        raise DesktopAgentError("The current window doesn't expose a browser address bar.")

    @staticmethod
    def _url_identity(value: str) -> tuple[str, str, str] | None:
        """Return normalized host/path/query identity for a browser address bar value."""
        candidate = str(value or "").strip()
        if not candidate:
            return None
        if "://" not in candidate:
            candidate = "https://" + candidate
        try:
            parsed = urlparse(candidate)
        except Exception:
            return None
        if not parsed.hostname:
            return None
        host = parsed.hostname.casefold().removeprefix("www.")
        path = parsed.path.rstrip("/") or "/"
        return host, path, parsed.query

    @classmethod
    def _browser_url_matches(cls, expected_url: str, observed_value: str) -> bool:
        """Compare the expected URL to the actual browser omnibox value."""
        expected = cls._url_identity(expected_url)
        observed = cls._url_identity(observed_value)
        if expected is None or observed is None or expected[0] != observed[0]:
            return False
        expected_path = expected[1]
        observed_path = observed[1]
        if expected_path != "/" and observed_path != expected_path:
            return False
        if expected[2]:
            expected_params = set(parse_qsl(expected[2], keep_blank_values=True))
            observed_params = set(parse_qsl(observed[2], keep_blank_values=True))
            if not expected_params.issubset(observed_params):
                return False
        return True

    def _wait_for_browser_url(
        self,
        expected_url: str,
        timeout_seconds: float = 25.0,
        stable_seconds: float = 0.8,
    ) -> tuple[Any, str]:
        """Don't continue until the foreground browser confirms the requested location."""
        deadline = time.monotonic() + timeout_seconds
        last_state: tuple[str, str] | None = None
        stable_since = 0.0
        last_observed = ""
        while time.monotonic() < deadline:
            try:
                active, control, _ = self._find_browser_address_bar(timeout_seconds=0.35)
                observed = self._control_value(control)
                last_observed = observed
                try:
                    title = str(getattr(active, "window_text", lambda: "")() or "").strip()
                except Exception:
                    title = ""
                if self._browser_url_matches(expected_url, observed):
                    state = (observed.casefold().strip(), title.casefold().strip())
                    if state != last_state:
                        last_state = state
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= stable_seconds:
                        return active, title
                else:
                    last_state = None
                    stable_since = 0.0
            except Exception:
                last_state = None
                stable_since = 0.0
            time.sleep(0.2)
        raise DesktopAgentError(
            "The browser did not confirm navigation to " + repr(expected_url) +
            " within " + str(round(timeout_seconds)) + " seconds. " +
            "Last visible address: " + repr(last_observed or "(not exposed by the browser)") +
            ". No later actions were run to avoid typing or clicking the wrong page."
        )

    @staticmethod
    def _same_origin(first_url: str, second_url: str) -> bool:
        """Compare origins, including non-default ports, without comparing paths."""
        try:
            first = urlparse(first_url if "://" in first_url else "https://" + first_url)
            second = urlparse(second_url if "://" in second_url else "https://" + second_url)
            if not first.hostname or not second.hostname:
                return False
            first_port = first.port or (443 if first.scheme.casefold() == "https" else 80)
            second_port = second.port or (443 if second.scheme.casefold() == "https" else 80)
            return (
                first.scheme.casefold() == second.scheme.casefold()
                and first.hostname.casefold() == second.hostname.casefold()
                and first_port == second_port
            )
        except Exception:
            return False

    @staticmethod
    def _control_runtime_id(control: Any) -> tuple[Any, ...] | None:
        try:
            value = getattr(control.element_info, "runtime_id", None)
            if callable(value):
                value = value()
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                return tuple(value)
            return (str(value),)
        except Exception:
            return None

    @staticmethod
    def _control_is_selected(control: Any) -> bool:
        try:
            return bool(control.iface_selection_item.CurrentIsSelected)
        except Exception:
            pass
        try:
            method = getattr(control, "is_selected", None)
            if callable(method):
                return bool(method())
        except Exception:
            pass
        return False

    @staticmethod
    def _title_tokens(value: str) -> set[str]:
        ignored = {
            "google", "chrome", "microsoft", "edge", "firefox", "mozilla",
            "opera", "brave", "browser", "new", "tab",
        }
        return {
            token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
            if len(token) > 1 and token not in ignored
        }

    def _remember_active_browser_tab(self) -> None:
        """Remember the active tab's accessibility identity for later dashboard submissions."""
        try:
            _, tabs = self._active_ui_controls({"TabItem"})
        except Exception:
            return
        selected = [item for item in tabs if self._control_is_selected(item[0])]
        if selected:
            runtime_id = self._control_runtime_id(selected[0][0])
            if runtime_id is not None:
                self._last_target_tab_runtime_id = runtime_id
                return

        host_label = (urlparse(self._last_target_browser_url).hostname or "").casefold()
        host_label = host_label.removeprefix("www.").split(".", 1)[0]
        target_tokens = self._title_tokens(self._last_target_window_title)
        if host_label and host_label not in {"localhost", "127"}:
            target_tokens.add(host_label)
        scored: list[tuple[int, Any]] = []
        for control, kind, name, metadata in tabs:
            candidate_tokens = self._title_tokens(name)
            score = len(target_tokens & candidate_tokens) * 10
            if host_label and host_label in candidate_tokens:
                score += 50
            if name and self._last_target_window_title and (
                name.casefold() in self._last_target_window_title.casefold()
                or self._last_target_window_title.casefold() in name.casefold()
            ):
                score += 60
            if score:
                scored.append((score, control))
        if scored:
            scored.sort(key=lambda entry: entry[0], reverse=True)
            if len(scored) == 1 or scored[0][0] > scored[1][0]:
                self._last_target_tab_runtime_id = self._control_runtime_id(scored[0][1])

    def _update_target_context_from_foreground(self) -> None:
        """Refresh the target tab URL/title after a search or result navigation."""
        try:
            active, address_control, _ = self._find_browser_address_bar(timeout_seconds=0.5)
            current_url = self._control_value(address_control)
            if current_url:
                self._last_target_browser_url = current_url
            try:
                title = str(getattr(active, "window_text", lambda: "")() or "").strip()
            except Exception:
                title = ""
            if title:
                self._last_target_window_title = title
            self._remember_active_browser_tab()
        except Exception:
            return

    def _ensure_last_target_tab_active(self) -> None:
        """Re-activate the previous target tab when the dashboard tab is foregrounded."""
        # _last_opened_url is the search scope, not proof that we have a tracked
        # foreground browser tab. Only restore tabs we explicitly opened and verified.
        expected_url = self._last_target_browser_url
        if not expected_url:
            return
        try:
            active, address_control, _ = self._find_browser_address_bar(timeout_seconds=0.6)
            current_url = self._control_value(address_control)
            if self._browser_url_matches(expected_url, current_url):
                current_tab_runtime_id = None
                try:
                    _, visible_tabs = self._active_ui_controls({"TabItem"})
                    selected_tabs = [
                        control for control, _, _, _ in visible_tabs
                        if self._control_is_selected(control)
                    ]
                    if selected_tabs:
                        current_tab_runtime_id = self._control_runtime_id(selected_tabs[0])
                except Exception:
                    current_tab_runtime_id = None
                # The same site can have multiple open tabs. A hostname match alone
                # isn't enough when we have the runtime identity of the intended tab.
                if (
                    self._last_target_tab_runtime_id is None
                    or current_tab_runtime_id is None
                    or current_tab_runtime_id == self._last_target_tab_runtime_id
                ):
                    try:
                        title = str(getattr(active, "window_text", lambda: "")() or "").strip()
                    except Exception:
                        title = ""
                    if title:
                        self._last_target_window_title = title
                    self._remember_active_browser_tab()
                    return
            _, tabs = self._active_ui_controls({"TabItem"})
        except Exception as exc:
            raise DesktopAgentError(
                "The target browser page is not foregrounded and its tab list isn't accessible. " +
                "Bring the browser forward and retry; Atlas won't type into the dashboard by mistake."
            ) from exc

        chosen = None
        if self._last_target_tab_runtime_id is not None:
            for control, _, _, _ in tabs:
                if self._control_runtime_id(control) == self._last_target_tab_runtime_id:
                    chosen = control
                    break

        if chosen is None:
            host_label = (urlparse(expected_url).hostname or "").casefold().removeprefix("www.").split(".", 1)[0]
            target_tokens = self._title_tokens(self._last_target_window_title)
            if host_label and host_label not in {"localhost", "127"}:
                target_tokens.add(host_label)
            scored: list[tuple[int, Any, str]] = []
            for control, _, name, _ in tabs:
                candidate_tokens = self._title_tokens(name)
                score = len(target_tokens & candidate_tokens) * 10
                if host_label and host_label in candidate_tokens:
                    score += 50
                if name and self._last_target_window_title and (
                    name.casefold() in self._last_target_window_title.casefold()
                    or self._last_target_window_title.casefold() in name.casefold()
                ):
                    score += 60
                if score:
                    scored.append((score, control, name))
            if scored:
                scored.sort(key=lambda entry: entry[0], reverse=True)
                best_score = scored[0][0]
                equally_good = [entry for entry in scored if entry[0] == best_score]
                if len(equally_good) > 1:
                    raise DesktopAgentError(
                        "Several browser tabs look like the last target page. Click the intended tab once, " +
                        "then retry the command so Atlas doesn't select the wrong site."
                    )
                chosen = scored[0][1]

        if chosen is None:
            raise DesktopAgentError(
                "Atlas couldn't identify the previous target browser tab. Open the target URL again or " +
                "make the desired tab active once, then retry."
            )

        try:
            chosen.click_input()
        except Exception as exc:
            raise DesktopAgentError("Atlas found the target tab but couldn't activate it: " + str(exc)) from exc
        _, title = self._wait_for_browser_url(expected_url, timeout_seconds=12.0, stable_seconds=0.35)
        self._wait_for_active_window_settled(timeout_seconds=5.0, stable_seconds=0.7)
        _, title = self._wait_for_browser_url(expected_url, timeout_seconds=5.0, stable_seconds=0.25)
        if title:
            self._last_target_window_title = title
        self._remember_active_browser_tab()

    def _navigate_browser_to_url(self, url: str, backend: Any) -> str:
        """Navigate the target page; preserve the dashboard tab when it is the controller."""
        current_address = ""
        current_title = ""
        has_browser_foreground = False
        try:
            active, address_control, _ = self._find_browser_address_bar(timeout_seconds=0.6)
            has_browser_foreground = True
            current_address = self._control_value(address_control)
            try:
                current_title = str(getattr(active, "window_text", lambda: "")() or "").strip()
            except Exception:
                current_title = ""
        except Exception:
            pass

        controller_tab = bool(
            self._controller_origin
            and (
                self._same_origin(current_address, self._controller_origin)
                or (not current_address and "jarvis" in current_title.casefold())
            )
        )
        open_separate_tab = controller_tab or not has_browser_foreground

        if open_separate_tab:
            if not webbrowser.open_new_tab(url):
                raise DesktopAgentError("Windows did not accept the request to open the website.")
            mode = "opened the target in a new browser tab to preserve the JARVIS dashboard: "
        else:
            backend.hotkey("ctrl", "l")
            time.sleep(0.12)
            backend.write(url, interval=0)
            time.sleep(0.1)
            backend.press("enter")
            mode = "navigated the current browser tab to "

        active, title = self._wait_for_browser_url(url, timeout_seconds=25.0, stable_seconds=0.75)
        self._wait_for_active_window_settled(timeout_seconds=10.0, stable_seconds=1.0)
        active, title = self._wait_for_browser_url(url, timeout_seconds=5.0, stable_seconds=0.35)
        self._last_target_browser_url = url
        self._last_target_window_title = title or current_title
        self._remember_active_browser_tab()
        return mode + url + " (navigation confirmed)"



    @staticmethod
    def _control_value(control: Any) -> str:
        """Read an accessible control value when its provider supports it."""
        try:
            getter = getattr(control, "get_value", None)
            if callable(getter):
                value = getter()
                if value:
                    return str(value)
        except Exception:
            pass
        try:
            value = control.iface_value.CurrentValue
            if value:
                return str(value)
        except Exception:
            pass
        try:
            properties = control.legacy_properties()
            if isinstance(properties, dict) and properties.get("Value"):
                return str(properties["Value"])
        except Exception:
            pass
        return ""

    def _capture_active_page_state(self) -> tuple[bool, tuple[Any, ...]]:
        """Capture title, URL/omnibox value, tabs, and accessible content state."""
        allowed = {
            "Edit", "ComboBox", "TextBox", "TabItem", "Document", "Pane",
            "ListItem", "DataItem", "TreeItem", "Hyperlink", "Button",
            "Text", "Group", "Custom", "Image",
        }
        active, controls = self._active_ui_controls(allowed)
        try:
            title = str(getattr(active, "window_text", lambda: "")() or "").strip().casefold()
        except Exception:
            title = ""
        addresses: list[str] = []
        tabs: list[str] = []
        content: list[tuple[str, str]] = []
        browser = False
        address_pattern = re.compile(
            r"\b(address bar|address and search bar|url bar|web address|omnibox|location bar)\b",
            re.IGNORECASE,
        )
        for control, kind, name, metadata in controls:
            if address_pattern.search(metadata):
                browser = True
                addresses.append(self._control_value(control).casefold().strip())
            if kind == "TabItem" and name:
                tabs.append(re.sub(r"\s+", " ", name).casefold().strip())
            elif kind in {"ListItem", "DataItem", "TreeItem", "Hyperlink", "Button", "Document"} and name:
                content.append((kind, re.sub(r"\s+", " ", name).casefold().strip()))
        signature = (title, tuple(sorted(addresses)), tuple(sorted(tabs)), tuple(sorted(content)))
        return browser, signature

    def _wait_for_active_page_change(
        self,
        before: tuple[bool, tuple[Any, ...]],
        timeout_seconds: float = 6.0,
    ) -> bool:
        """Wait for navigation/result state change after activating a browser result."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                current = self._capture_active_page_state()
            except Exception:
                time.sleep(0.25)
                continue
            if before[0] and current[0]:
                # In a browser, unrelated page content may change while a click is
                # still doing nothing (ads, recommendations, loading indicators).
                # Count only navigational evidence: window title, address/URL value,
                # or browser tab list. This avoids false-positive "clicked" reports.
                before_navigation = before[1][:3]
                current_navigation = current[1][:3]
                if current_navigation != before_navigation:
                    return True
            elif current != before:
                return True
            time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
        return False

    def _foreground_window_identity(self) -> tuple[int | None, int | None, str]:
        """Read foreground process/window identity to avoid acting on the previous app."""
        try:
            from pywinauto import Desktop
            active = Desktop(backend="uia").get_active()
        except Exception:
            return None, None, ""
        try:
            title = str(getattr(active, "window_text", lambda: "")() or "").strip()
        except Exception:
            title = ""
        try:
            process_id = active.process_id()
        except Exception:
            try:
                process_id = getattr(getattr(active, "element_info", None), "process_id", None)
            except Exception:
                process_id = None
        try:
            handle = getattr(active, "handle", None)
            if callable(handle):
                handle = handle()
            if handle is None:
                handle = getattr(getattr(active, "element_info", None), "handle", None)
        except Exception:
            handle = None
        return process_id, handle, title

    def _wait_for_app_foreground(
        self,
        app_name: str,
        before: tuple[int | None, int | None, str],
        timeout_seconds: float = 25.0,
        stable_seconds: float = 1.0,
    ) -> tuple[int | None, int | None, str]:
        """Wait until the requested app actually owns the foreground window."""
        deadline = time.monotonic() + timeout_seconds
        normalized_app = re.sub(r"[^a-z0-9]+", " ", app_name.casefold()).strip()
        shell_title = re.compile(
            r"^(?:start|search|windows search|task switching|program manager|desktop|"
            r"task view|notification center|quick settings)$",
            re.IGNORECASE,
        )
        last_identity: tuple[int | None, int | None, str] | None = None
        stable_since = 0.0

        while time.monotonic() < deadline:
            identity = self._foreground_window_identity()
            process_id, handle, title = identity
            normalized_title = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
            if normalized_title and not shell_title.fullmatch(normalized_title):
                title_matches = bool(
                    normalized_app
                    and (
                        normalized_app in normalized_title
                        or all(token in normalized_title for token in normalized_app.split())
                    )
                )
                window_changed = bool(
                    (before[1] is not None and handle is not None and before[1] != handle)
                    or (before[0] is not None and process_id is not None and before[0] != process_id)
                )
                # Prefer app-name evidence. When a product doesn't put its name in
                # the title bar, accept a different foreground process/window once it
                # is stable rather than blindly continuing in the old app.
                if title_matches or window_changed or not any(before):
                    if identity != last_identity:
                        last_identity = identity
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= stable_seconds:
                        self._wait_for_active_window_settled(
                            timeout_seconds=4.0,
                            stable_seconds=0.75,
                        )
                        return identity
                else:
                    last_identity = None
                    stable_since = 0.0
            else:
                last_identity = None
                stable_since = 0.0
            time.sleep(0.25)

        raise DesktopAgentError(
            "Windows did not bring " + repr(app_name) +
            " to the foreground within " + str(round(timeout_seconds)) +
            " seconds. No follow-up actions were run in the previous window."
        )

    def _wait_for_active_window_settled(self, timeout_seconds: float = 12.0, stable_seconds: float = 0.8) -> bool:
        """Wait for the active app/browser accessibility tree to stop changing briefly."""
        if platform.system() != "Windows":
            time.sleep(1.0)
            return False
        allowed = {
            "Edit", "ComboBox", "TextBox", "TabItem", "Document", "Pane",
            "ListItem", "DataItem", "TreeItem", "Hyperlink", "Button",
            "Text", "Group", "Custom", "Image",
        }
        deadline = time.monotonic() + timeout_seconds
        last_signature: tuple[Any, ...] | None = None
        stable_since = 0.0
        while time.monotonic() < deadline:
            try:
                active, controls = self._active_ui_controls(allowed)
                signature = self._result_tree_signature(active, controls)
                if signature != last_signature:
                    last_signature = signature
                    stable_since = time.monotonic()
                elif signature[0] and time.monotonic() - stable_since >= stable_seconds:
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def _find_result_control(self, target: str = "", timeout_seconds: float = 20.0) -> tuple[Any, str]:
        """Wait for result controls to appear and stabilize, then pick a matching result."""
        started = time.monotonic()
        deadline = started + timeout_seconds
        target_text = re.sub(r"\s+", " ", target.casefold()).strip()
        target_tokens = {token for token in re.findall(r"[a-z0-9]+", target_text) if len(token) > 1}
        allowed = {
            "ListItem", "DataItem", "TreeItem", "Hyperlink", "Button",
            "Text", "Group", "Custom", "Image", "Document", "Pane",
        }
        excluded = re.compile(
            r"^(?:home|back|forward|next|previous|play|pause|stop|close|cancel|settings|"
            r"library|search|filter|sort|more options|menu|minimize|maximize|refresh|"
            r"sign in|log in|send|delete|buy|purchase|checkout|pay|payment)$",
            re.IGNORECASE,
        )
        type_bonus = {
            "ListItem": 30, "DataItem": 28, "TreeItem": 25, "Hyperlink": 22,
            "Group": 14, "Custom": 12, "Document": 10, "Button": 8,
            "Pane": 6, "Text": 5, "Image": 3,
        }

        baseline = self._last_result_snapshot
        submitted_at = self._last_search_submitted_at
        ready_after = max(
            started + min(3.5, max(0.5, timeout_seconds * 0.175)),
            submitted_at + 3.0 if submitted_at else started,
        )
        stable_for = min(1.25, max(0.35, timeout_seconds * 0.0625))
        previous_candidate: tuple[Any, ...] | None = None
        candidate_since = 0.0
        last_tree_signature: tuple[Any, ...] | None = None
        tree_stable_since = 0.0
        baseline_changed = baseline is None

        while time.monotonic() < deadline:
            active, controls = self._active_ui_controls(allowed)
            now = time.monotonic()
            tree_signature = self._result_tree_signature(active, controls)
            if tree_signature != last_tree_signature:
                tree_stable_since = now
            if baseline is not None and tree_signature != baseline:
                baseline_changed = True
            last_tree_signature = tree_signature

            candidates: list[tuple[float, Any, str, str]] = []
            for control, kind, name, metadata in controls:
                label = re.sub(r"\s+", " ", name).strip()
                if not label or excluded.fullmatch(label) or len(label) > 400:
                    continue
                if len(label) < 2:
                    continue

                score = 0.0
                if target_tokens:
                    label_tokens = {
                        token for token in re.findall(r"[a-z0-9]+", label.casefold())
                        if len(token) > 1
                    }
                    matches = target_tokens & label_tokens
                    required_matches = 1 if len(target_tokens) <= 2 else 2
                    # A result title can be much shorter than the complete query.
                    if len(matches) < required_matches:
                        continue
                    query_coverage = len(matches) / max(1, len(target_tokens))
                    label_coverage = len(matches) / max(1, len(label_tokens))
                    score = query_coverage * 55.0 + label_coverage * 35.0
                    normalized_label = " ".join(re.findall(r"[a-z0-9]+", label.casefold()))
                    normalized_target = " ".join(re.findall(r"[a-z0-9]+", target_text))
                    if normalized_target and normalized_target in normalized_label:
                        score += 35.0
                else:
                    if kind not in {"ListItem", "DataItem", "TreeItem", "Hyperlink", "Group", "Custom"}:
                        continue

                score += type_bonus.get(kind, 0)
                candidates.append((score, control, label, kind))

            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                chosen = candidates[0]
                top_score = chosen[0]
                close = [item for item in candidates if abs(item[0] - top_score) < 0.5]
                ambiguous = bool(target_tokens and len(close) > 1 and top_score < 125)
                # Stability is measured by both the best candidate and its matching
                # score; loading pages may temporarily show different controls in the
                # same spot. Keep waiting until the likely result stops changing.
                candidate_signature = (
                    tuple((item[3], item[2], item[0]) for item in close[:4])
                    if ambiguous
                    else (chosen[3], chosen[2], chosen[0])
                )
                if candidate_signature != previous_candidate:
                    previous_candidate = candidate_signature
                    candidate_since = now

                # Prefer observable page updates, but don't deadlock when the app's
                # accessibility provider leaves its tree unchanged. After a meaningful
                # settle window, a strong, stable title match is usable even if the same
                # title existed before submission (for example, on a recommendations
                # page). Ambiguous matches still fail closed below.
                tree_is_stable = now - tree_stable_since >= stable_for
                stale_tree_timeout = min(5.0, max(0.5, timeout_seconds * 0.25))
                stable_match_without_tree_update = (
                    baseline is not None
                    and not baseline_changed
                    and now - started >= stale_tree_timeout
                    and chosen[0] >= 50
                )
                if (
                    now >= ready_after
                    and now - candidate_since >= stable_for
                    and tree_is_stable
                    and (baseline_changed or stable_match_without_tree_update)
                ):
                    if ambiguous:
                        names = [item[2] for item in close[:4]]
                        raise DesktopAgentError(
                            "Several visible results match that text: " + "; ".join(names) +
                            ". Name the exact result so I don't click an uncertain item."
                        )
                    if not target_tokens and chosen[3] not in {
                        "ListItem", "DataItem", "TreeItem", "Hyperlink", "Group", "Custom"
                    }:
                        raise DesktopAgentError(
                            "I couldn't identify a result container in this app. Name the result instead."
                        )
                    return chosen[1], chosen[2]
            else:
                previous_candidate = None
                candidate_since = 0.0

            time.sleep(min(0.25, max(0.01, deadline - now)))

        if baseline is not None and target_tokens:
            raise DesktopAgentError(
                "I couldn't find a stable, sufficiently strong result for " + repr(target) +
                " within the wait window. No click was made; the page may still be loading or may not expose matching result controls."
            )
        if target_tokens:
            raise DesktopAgentError(
                "I couldn't find a stable, accessible result matching " + repr(target) +
                ". No result was clicked. The app may still be loading or may not expose its result controls."
            )
        raise DesktopAgentError(
            "I couldn't identify a stable, visible result in the active app. No result was clicked."
        )

    def _click_result_control(self, control: Any) -> str:
        """Activate a result semantically first, then verify and use a physical fallback."""
        actionable = {"Hyperlink", "ListItem", "DataItem", "TreeItem", "Button"}
        current = control
        selected = control
        for _ in range(8):
            kind = self._control_text(current, "control_type")
            if kind in actionable:
                selected = current
                # Keep climbing only if a direct text/link control has an empty or
                # implausibly small rectangle; the nearest actionable ancestor is
                # usually the clickable card/container.
                try:
                    rect = current.rectangle()
                    width = max(0, int(rect.right - rect.left))
                    height = max(0, int(rect.bottom - rect.top))
                except Exception:
                    width, height = 0, 0
                if width >= 18 and height >= 12:
                    break
            try:
                parent = current.parent()
            except Exception:
                break
            if parent is None or parent is current:
                break
            current = parent

        selected_name = (
            self._control_text(selected, "name")
            or self._control_text(selected, "control_type")
            or "result control"
        )
        try:
            before_state = self._capture_active_page_state()
        except Exception:
            before_state = None

        invoke_error: Exception | None = None
        invoked = False
        activation_sent = False
        try:
            invoke = getattr(selected, "iface_invoke", None)
            if invoke is not None:
                invoke.Invoke()
                invoked = True
                activation_sent = True
        except Exception as exc:
            invoke_error = exc

        if not invoked:
            try:
                selected.click_input()
                activation_sent = True
            except Exception as click_error:
                invoke_error = click_error

        # Browser result clicks should navigate/open a player or tab. Don't call the
        # click successful just because an input event was issued; wait for title,
        # URL, tab or accessible page content to change.
        browser_context = bool(before_state and before_state[0])
        if browser_context and self._wait_for_active_page_change(before_state, timeout_seconds=5.0):
            return selected_name
        if not browser_context and activation_sent:
            # A non-browser result can simply select/focus an item without navigation.
            # Do not issue a second physical click after a successful semantic click.
            return selected_name

        # If the semantic click was unsupported or had no observable effect, click
        # the verified bounds of the selected result. This remains grounded in the
        # actual accessibility element, not a guessed screen coordinate.
        try:
            rect = selected.rectangle()
            left, top = int(rect.left), int(rect.top)
            right, bottom = int(rect.right), int(rect.bottom)
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            if right - left < 8 or bottom - top < 8:
                raise DesktopAgentError("The result's accessible bounds are too small to click safely.")
            backend = self._get_backend()
            size = backend.size()
            if not (0 <= center_x < int(size.width) and 0 <= center_y < int(size.height)):
                raise DesktopAgentError("The result control is outside the visible screen.")
            backend.click(x=center_x, y=center_y)
        except Exception as fallback_error:
            reason = (" Semantic activation error: " + str(invoke_error)) if invoke_error else ""
            raise DesktopAgentError(
                "I found the result but could not activate it." + reason +
                " Physical fallback failed: " + str(fallback_error)
            ) from fallback_error

        if browser_context:
            if self._wait_for_active_page_change(before_state, timeout_seconds=8.0):
                return selected_name
            raise DesktopAgentError(
                "The result was targeted, but the browser showed no observable navigation, tab, title, or page-content change after two activation methods. "
                "I won't report the click as successful."
            )

        # Non-browser list selections need not navigate, so a successful UIA/physical
        # activation is sufficient when there's no browser page state to observe.
        return selected_name

    def execute(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        backend = self._get_backend()
        completed: list[dict[str, Any]] = []
        last_search_query = self._last_search_query
        for index, action in enumerate(actions, 1):
            kind = action.get("type")
            try:
                if (
                    kind in {"smart_type", "focus_search", "type_text", "click_result", "click_first_result"}
                    or (
                        kind == "hotkey"
                        and tuple(str(key).casefold() for key in action.get("keys", [])) == ("enter",)
                        and last_search_query
                    )
                ):
                    self._ensure_last_target_tab_active()

                if kind == "open_app":
                    app = str(action.get("app", "")).strip()
                    app_key = APP_ALIASES.get(app.casefold(), app.casefold())
                    if app_key != self._last_app_name:
                        last_search_query = ""
                        self._last_search_query = ""
                    self._last_app_name = app_key
                    self._last_opened_url = ""
                    self._last_target_browser_url = ""
                    self._last_target_window_title = ""
                    self._last_target_tab_runtime_id = None
                    self._last_result_snapshot = None
                    self._last_search_submitted_at = 0.0
                    self._search_navigation_completed = False
                    before_window = self._foreground_window_identity()
                    executable = self._find_app(app)
                    if executable:
                        subprocess.Popen([executable], shell=False, close_fds=True)
                    else:
                        backend.press("win")
                        time.sleep(0.25)
                        backend.write(app, interval=0)
                        time.sleep(0.7)
                        backend.press("enter")
                    settled_window = self._wait_for_app_foreground(
                        app,
                        before_window,
                        timeout_seconds=25.0,
                        stable_seconds=1.0,
                    )
                    detail = (
                        "Launched " + app + " and confirmed foreground window " +
                        repr(settled_window[2] or app) + " is stable"
                    )
                elif kind == "open_url":
                    url = validate_url(str(action.get("url", "")))
                    detail = self._navigate_browser_to_url(url, backend)
                    self._last_opened_url = url
                    self._last_app_name = ""
                    self._last_target_browser_url = url
                    self._last_result_snapshot = None
                    self._last_search_submitted_at = 0.0
                    self._search_navigation_completed = False
                elif kind == "type_text":
                    value = str(action.get("text", ""))
                    if not value or len(value) > 8_000:
                        raise DesktopAgentError("Text must be between 1 and 8,000 characters.")
                    if value.isascii():
                        backend.write(value, interval=0)
                    else:
                        try:
                            import pyperclip
                        except ImportError as exc:
                            raise DesktopAgentError("Unicode typing needs pyperclip; run run_desktop_agent.bat to install it.") from exc
                        original_clipboard = None
                        try:
                            original_clipboard = pyperclip.paste()
                        except Exception:
                            pass
                        pyperclip.copy(value)
                        backend.hotkey("ctrl", "v")
                        time.sleep(0.15)
                        if original_clipboard is not None:
                            try:
                                pyperclip.copy(original_clipboard)
                            except Exception:
                                pass
                    detail = "Typed " + str(len(value)) + " characters"
                elif kind in {"smart_type", "focus_search"}:
                    value = str(action.get("text", ""))
                    if kind == "smart_type" and (not value or len(value) > 8_000):
                        raise DesktopAgentError("Search text must be between 1 and 8,000 characters.")

                    try:
                        # Discover a real in-page search field, not the browser omnibox.
                        _, control, field_name = self._find_search_field()
                        control.click_input()
                    except Exception as field_exc:
                        # Generic fallback for websites whose search boxes aren't
                        # exposed to UI Automation: use the current browser's normal
                        # address/omnibox search. No provider-specific URL is assumed.
                        if kind != "smart_type":
                            if isinstance(field_exc, DesktopAgentError):
                                raise
                            raise DesktopAgentError(
                                "Could not focus the app's search field: " + str(field_exc)
                            ) from field_exc
                        try:
                            # Detect browser context from its accessible omnibox, not
                            # from a prior "open URL" instruction, so launched browsers
                            # and already-open browser windows use the same fallback.
                            self._find_browser_address_bar(timeout_seconds=1.5)
                        except Exception as address_error:
                            raise DesktopAgentError(
                                "The page search field is inaccessible and no browser address bar is exposed. " +
                                "I couldn't submit the query safely: " + str(address_error)
                            ) from field_exc

                        self._last_result_snapshot = self._capture_result_snapshot()
                        self._last_search_submitted_at = time.monotonic()
                        self._search_navigation_completed = False
                        # If the current page's own search isn't accessible, constrain
                        # the browser's search to the current site instead of accidentally
                        # searching the entire web. This is derived from the current URL,
                        # not a provider- or site-specific selector.
                        fallback_query = value
                        current_host = (urlparse(self._last_opened_url).hostname or "").casefold().removeprefix("www.")
                        search_engine_hosts = {
                            "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
                            "search.yahoo.com", "search.brave.com", "ecosia.org", "baidu.com",
                        }
                        is_search_engine = any(
                            current_host == host or current_host.endswith("." + host)
                            for host in search_engine_hosts
                        )
                        if current_host and not is_search_engine:
                            fallback_query = "site:" + current_host + " " + value
                        backend.hotkey("ctrl", "l")
                        time.sleep(0.15)
                        backend.write(fallback_query, interval=0)
                        time.sleep(0.1)
                        backend.press("enter")
                        self._search_navigation_completed = True
                        last_search_query = value
                        self._last_search_query = value
                        # The omnibox fallback submits immediately. Wait for an actual
                        # stable matching result so the caller cannot race page loading.
                        self._find_result_control(value, timeout_seconds=20.0)
                        self._update_target_context_from_foreground()
                        if fallback_query != value:
                            detail = (
                                "Submitted a site-restricted browser search for " + repr(value) +
                                " on " + current_host + " and waited for a stable result"
                            )
                        else:
                            detail = (
                                "Submitted " + repr(value) +
                                " through the browser's default search and waited for a stable result"
                            )
                    else:
                        if kind == "smart_type":
                            backend.hotkey("ctrl", "a")
                            if value.isascii():
                                backend.write(value, interval=0)
                            else:
                                try:
                                    import pyperclip
                                except ImportError as exc:
                                    raise DesktopAgentError("Unicode search text needs pyperclip; run run_desktop_agent.bat to install it.") from exc
                                original_clipboard = None
                                try:
                                    original_clipboard = pyperclip.paste()
                                except Exception:
                                    pass
                                pyperclip.copy(value)
                                backend.hotkey("ctrl", "v")
                                time.sleep(0.15)
                                if original_clipboard is not None:
                                    try:
                                        pyperclip.copy(original_clipboard)
                                    except Exception:
                                        pass
                            last_search_query = value
                            self._last_search_query = value
                            self._last_result_snapshot = None
                            self._last_search_submitted_at = 0.0
                            self._search_navigation_completed = False
                            detail = "Found " + repr(field_name) + " and entered the search text"
                        else:
                            detail = "Focused " + repr(field_name)
                elif kind in {"click_result", "click_first_result"}:
                    target = str(action.get("target", "") or last_search_query).strip()
                    control, result_name = self._find_result_control(target)
                    activated_name = self._click_result_control(control)
                    self._last_result_snapshot = None
                    self._last_search_submitted_at = 0.0
                    self._search_navigation_completed = False
                    self._update_target_context_from_foreground()
                    detail = "Clicked result " + repr(result_name) + " using " + repr(activated_name)
                elif kind in {"click", "double_click"}:
                    size = backend.size()
                    x, y = int(action.get("x", -1)), int(action.get("y", -1))
                    if not (0 <= x < int(size.width) and 0 <= y < int(size.height)):
                        raise DesktopAgentError("Click coordinates are outside the current screen.")
                    button = str(action.get("button", "left")).casefold()
                    if button not in {"left", "right"}:
                        raise DesktopAgentError("Only left and right mouse clicks are supported.")
                    backend.moveTo(x, y, duration=0.08)
                    if kind == "double_click":
                        backend.doubleClick(x=x, y=y, interval=0.12, button="left")
                        detail = "Double-clicked (" + str(x) + ", " + str(y) + ")"
                    else:
                        backend.click(x=x, y=y, button=button)
                        detail = "Clicked (" + str(x) + ", " + str(y) + ")"
                elif kind == "drag":
                    size = backend.size()
                    coords = [int(action.get(name, -1)) for name in ("x1", "y1", "x2", "y2")]
                    points = ((coords[0], coords[1]), (coords[2], coords[3]))
                    if any(x < 0 or y < 0 or x >= int(size.width) or y >= int(size.height) for x, y in points):
                        raise DesktopAgentError("Drag coordinates are outside the current screen.")
                    backend.moveTo(coords[0], coords[1], duration=0.08)
                    backend.dragTo(coords[2], coords[3], duration=0.3, button="left")
                    detail = "Dragged from (" + str(coords[0]) + ", " + str(coords[1]) + ") to (" + str(coords[2]) + ", " + str(coords[3]) + ")"
                elif kind == "scroll":
                    amount = int(action.get("amount", 0))
                    if amount == 0 or abs(amount) > 1500:
                        raise DesktopAgentError("Scroll amount must be between 1 and 1,500 units.")
                    backend.scroll(amount)
                    detail = "Scrolled " + ("up " if amount > 0 else "down ") + str(abs(amount)) + " units"
                elif kind == "click_ui":
                    control_type = str(action.get("control", "")).casefold()
                    label = str(action.get("text", "")).strip()
                    if control_type not in {"button", "link"} or not label or len(label) > 100:
                        raise DesktopAgentError("UI click requires an explicit button or link name.")
                    try:
                        from pywinauto import Desktop
                    except ImportError as exc:
                        raise DesktopAgentError('Named UI clicks need pywinauto; run run_desktop_agent.bat to install it.') from exc
                    active = Desktop(backend="uia").get_active()
                    expected_type = "Button" if control_type == "button" else "Hyperlink"
                    control = active.child_window(title=label, control_type=expected_type)
                    control.wait("visible enabled", timeout=2)
                    control.click_input()
                    detail = "Clicked " + control_type + " " + repr(label)
                elif kind == "hotkey":
                    keys = tuple(str(key).casefold() for key in action.get("keys", []))
                    key_name = next((name for name, key_values in SAFE_HOTKEYS.items() if key_values == keys), None)
                    if not key_name:
                        raise DesktopAgentError("Keyboard shortcut is not on the allowlist.")
                    if keys == ("enter",) and last_search_query:
                        # Capture the pre-submit accessibility tree, so result-clicking
                        # can tell whether the page reacted to the search submission.
                        if self._search_navigation_completed:
                            # The generic omnibox fallback already pressed Enter and
                            # waited for a stable result. Do not submit the same query twice.
                            self._search_navigation_completed = False
                            detail = "Search was already submitted and its result became stable"
                            completed.append({"step": index, "status": "succeeded", "detail": detail})
                            continue
                        self._last_result_snapshot = self._capture_result_snapshot()
                        self._last_search_submitted_at = time.monotonic()
                    if len(keys) == 1:
                        backend.press(keys[0])
                    else:
                        backend.hotkey(*keys)
                    detail = "Pressed " + "+".join(keys)
                    if keys == ("enter",) and last_search_query:
                        # Wait on the same executor request until the UI exposes a stable
                        # match. Separate follow-up commands therefore don't race the page.
                        self._find_result_control(last_search_query, timeout_seconds=20.0)
                        self._update_target_context_from_foreground()
                        detail += " and waited for a stable matching result"
                elif kind == "wait":
                    seconds = float(action.get("seconds", 0))
                    if not 0 < seconds <= 5:
                        raise DesktopAgentError("Wait must be between 0 and 5 seconds.")
                    time.sleep(seconds)
                    detail = "Waited " + str(seconds) + " seconds"
                else:
                    raise DesktopAgentError("Unsupported action type.")
                completed.append({"step": index, "status": "succeeded", "detail": detail})
            except Exception as exc:
                return {
                    "ok": False,
                    "completed": completed,
                    "failed_step": index,
                    "error": str(exc)[:400],
                    "message": "Execution stopped at the first failed step. Review what already ran before retrying.",
                }
        return {"ok": True, "completed": completed, "message": "All approved steps finished."}

    def screenshot_png(self) -> bytes:
        backend = self._get_backend()
        image = backend.screenshot()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


@dataclass
class ApprovalTicket:
    task: str
    actions: list[dict[str, Any]]
    active_window: str = ""
    approved: bool = False
    finished: threading.Event = field(default_factory=threading.Event)


class ApprovalBroker:
    """Bridge workers queue approval requests; Tkinter displays each prompt on the main thread."""

    def __init__(self) -> None:
        self.pending: queue.Queue[ApprovalTicket] = queue.Queue()

    def request(self, task: str, actions: list[dict[str, Any]], active_window: str = "") -> bool:
        ticket = ApprovalTicket(task=task, actions=actions, active_window=active_window)
        self.pending.put(ticket)
        if not ticket.finished.wait(APPROVAL_TIMEOUT_SECONDS):
            return False
        return ticket.approved


class BridgeState:
    def __init__(self, token: str) -> None:
        self.token = token
        self.executor = DesktopActionExecutor()
        self.approvals = ApprovalBroker()


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, state: BridgeState):
        self.state = state
        super().__init__(address, handler)


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer

    def log_message(self, format_string: str, *args: Any) -> None:
        # Never log prompt bodies, pairing keys, or Authorization headers.
        print("[desktop-bridge] " + str(format_string % args)[:180])

    @property
    def state(self) -> BridgeState:
        return self.server.state

    def _origin(self) -> str:
        return self.headers.get("Origin", "").rstrip("/")

    def _origin_allowed(self) -> bool:
        origin = self._origin()
        return not origin or origin in allowed_origins()

    def _add_cors_headers(self) -> None:
        origin = self._origin()
        if origin and origin in allowed_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            if self.headers.get("Access-Control-Request-Private-Network", "").casefold() == "true":
                self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_png(self, data: bytes) -> None:
        self.send_response(200)
        self._add_cors_headers()
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        supplied = header[7:].strip() if header.startswith("Bearer ") else ""
        return bool(supplied) and hmac.compare_digest(supplied, self.state.token)

    def _require_request_access(self) -> bool:
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origin is not permitted. Check JARVIS_DESKTOP_ALLOWED_ORIGINS."})
            return False
        if not self._authorised():
            self._send_json(401, {"ok": False, "error": "Pair this browser with the token shown in the local JARVIS desktop companion."})
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            raise DesktopAgentError("Request body must be between 1 byte and 16 KB.")
        if "application/json" not in self.headers.get("Content-Type", "").casefold():
            raise DesktopAgentError("Use application/json for this request.")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise DesktopAgentError("Request must be a JSON object.")
        return payload

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origin is not permitted."})
            return
        self.send_response(204)
        self._add_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "JARVIS Desktop Companion", "bound_to": HOST})
            return
        if self.path != "/v1/status":
            self._send_json(404, {"ok": False, "error": "Not found."})
            return
        if not self._require_request_access():
            return
        self._send_json(200, self.state.executor.status())

    def do_POST(self) -> None:
        if self.path not in {"/v1/preview", "/v1/execute", "/v1/screenshot"}:
            self._send_json(404, {"ok": False, "error": "Not found."})
            return
        if not self._require_request_access():
            return
        try:
            if self.path == "/v1/screenshot":
                status = self.state.executor.status()
                if not status.get("desktop_available"):
                    self._send_json(503, {"ok": False, "error": status.get("message", "Desktop controls are unavailable.")})
                    return
                active_window = str(status.get("active_window", "Unknown"))
                actions = [{"type": "screenshot"}]
                approved = self.state.approvals.request("Capture current screen", actions, active_window)
                if not approved:
                    self._send_json(403, {"ok": False, "error": "Screen capture was denied or timed out."})
                    return
                self._send_png(self.state.executor.screenshot_png())
                return

            payload = self._read_json()
            task = _normalise_task(payload.get("task"))
            executor = self.state.executor
            actions = parse_task(
                task,
                app_context=bool(
                    getattr(executor, "_last_opened_url", "")
                    or getattr(executor, "_last_app_name", "")
                ),
                last_search_query=getattr(executor, "_last_search_query", ""),
            )
            if self.path == "/v1/preview":
                status = self.state.executor.status()
                active_window = str(status.get("active_window", "Unknown"))
                reason = approval_reason(task, actions, active_window)
                self._send_json(200, {
                    "ok": True, "task": task, "actions": actions,
                    "summary": action_summary(actions, active_window),
                    "approval_required": bool(reason), "approval_reason": reason,
                    "execution_mode": "local_approval" if reason else "routine_auto_run",
                })
                return
            status = self.state.executor.status()
            if not status.get("desktop_available"):
                self._send_json(503, {"ok": False, "error": status.get("message", "Desktop controls are unavailable.")})
                return
            active_window = str(status.get("active_window", "Unknown"))
            reason = approval_reason(task, actions, active_window)
            if reason:
                approved = self.state.approvals.request(task, actions, active_window)
                if not approved:
                    self._send_json(403, {"ok": False, "error": "Task was denied or approval timed out. Nothing was executed.", "approval_required": True, "approval_reason": reason})
                    return
            # Origin has already passed the allowlist check; the executor uses
            # it only to avoid navigating away from the browser tab hosting the dashboard.
            try:
                self.state.executor._controller_origin = self._origin()
            except Exception:
                pass
            result = self.state.executor.execute(actions)
            result["approval_required"] = bool(reason)
            result["approval_reason"] = reason
            if result.get("ok") and not reason:
                result["message"] = "Routine task completed without a permission prompt."
            self._send_json(200 if result.get("ok") else 409, result)
        except DesktopAgentError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": "Invalid JSON request."})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "Desktop bridge failed safely: " + type(exc).__name__})


def _run_tk_app(state: BridgeState, server: BridgeHTTPServer) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("JARVIS Desktop Companion")
    root.geometry("560x390")
    root.minsize(520, 360)
    root.configure(bg="#07111b")

    title = tk.Label(root, text="JARVIS / DESKTOP COMPANION", bg="#07111b", fg="#6be7ff", font=("Consolas", 16, "bold"))
    title.pack(anchor="w", padx=22, pady=(20, 4))
    subtitle = tk.Label(root, text="Local Windows control · routine actions run directly · sensitive actions require approval", bg="#07111b", fg="#a8bac9", font=("Segoe UI", 9))
    subtitle.pack(anchor="w", padx=22, pady=(0, 14))
    status = tk.Label(root, text="RUNNING ON 127.0.0.1:8766", bg="#0c2732", fg="#72f3d4", font=("Consolas", 10, "bold"), padx=10, pady=8)
    status.pack(fill="x", padx=22, pady=(0, 14))
    tk.Label(root, text="PAIRING KEY — paste into the Desktop Agent page once", bg="#07111b", fg="#d6e5ef", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=22)
    key_entry = tk.Entry(root, show="•", relief="flat", bg="#101e2a", fg="#d6e5ef", insertbackground="#6be7ff", font=("Consolas", 9))
    key_entry.pack(fill="x", padx=22, pady=(6, 6), ipady=8)
    key_entry.insert(0, state.token)
    key_entry.configure(state="readonly")
    def copy_key():
        root.clipboard_clear()
        root.clipboard_append(state.token)
        root.update()
        messagebox.showinfo("JARVIS pairing key", "Pairing key copied. Keep it private.", parent=root)
    tk.Button(root, text="COPY PAIRING KEY", command=copy_key, bg="#07546a", fg="white", activebackground="#0a718d", relief="flat", padx=14, pady=8).pack(anchor="w", padx=22, pady=(4, 14))
    tk.Label(
        root,
        text="Permission model: each task shows its exact actions in this window before execution.\n"
             "Emergency stop: move the mouse to the top-left screen corner.\n"
             "Keep this companion open while using desktop control.",
        bg="#07111b", fg="#93a8b9", justify="left", font=("Segoe UI", 9),
    ).pack(anchor="w", padx=22, pady=(0, 10))
    tk.Label(root, text="Close this window to stop the local bridge.", bg="#07111b", fg="#ffb56b", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=22)

    def poll_approvals():
        try:
            ticket = state.approvals.pending.get_nowait()
        except queue.Empty:
            root.after(120, poll_approvals)
            return
        try:
            details = action_summary(ticket.actions, ticket.active_window)
            ticket.approved = bool(messagebox.askyesno(
                "JARVIS permission required",
                "This action was classified as sensitive. Review the exact sequence:\n\n" + details,
                icon="warning",
                parent=root,
            ))
        except Exception:
            ticket.approved = False
        finally:
            ticket.finished.set()
            root.after(80, poll_approvals)

    def close_app():
        try:
            server.shutdown()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_app)
    root.after(120, poll_approvals)
    root.mainloop()


def main() -> None:
    if platform.system() != "Windows":
        print("JARVIS Desktop Companion is designed for Windows 10/11.")
    token = load_or_create_token()
    state = BridgeState(token)
    server = BridgeHTTPServer((HOST, PORT), BridgeHandler, state)
    thread = threading.Thread(target=server.serve_forever, name="jarvis-desktop-http", daemon=True)
    thread.start()
    print("JARVIS Desktop Companion listening on http://" + HOST + ":" + str(PORT))
    print("Pairing key is shown in the local companion window. Do not share it.")
    try:
        _run_tk_app(state, server)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        print("JARVIS Desktop Companion stopped.")


if __name__ == "__main__":
    main()
