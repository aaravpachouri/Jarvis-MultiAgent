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
from urllib.parse import urlparse, quote_plus


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
        target = named_result.group(1).strip().strip("'\"")
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


def parse_task(value: Any) -> list[dict[str, Any]]:
    """Translate a small, explicit command grammar into allowlisted actions."""
    task = _normalise_task(value)
    # Split a natural-language request into sequential commands while leaving words such
    # as "rock and roll" inside search text untouched.
    splitter = r"\s*(?:;|\n+)\s*|\s+(?:and\s+then|then)\s+|,\s*(?=(?:and\s+)?(?:search|find|type|write|enter|press|click|tap|scroll|focus|go\s+to|wait|open|launch|start)\b)|\s+and\s+(?=(?:search|find|type|write|enter|press|click|tap|scroll|focus|go\s+to|wait)\b)"
    segments = [part.strip(" ,\t") for part in re.split(splitter, task, flags=re.IGNORECASE) if part.strip(" ,\t")]
    actions: list[dict[str, Any]] = []
    app_context = False
    last_search_query = ""
    for segment in segments:
        step_actions = _parse_one(segment, app_context=app_context)
        for action in step_actions:
            if action.get("type") == "open_app":
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
        if kind == "click_ui" and sensitive_control.search(str(action.get("text", ""))):
            return "This named control may perform a consequential action."
        if kind == "type_text":
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
    """Execute only the actions returned by parse_task; all side effects are approved first."""

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend

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

    def execute(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        backend = self._get_backend()
        completed: list[dict[str, Any]] = []
        for index, action in enumerate(actions, 1):
            kind = action.get("type")
            try:
                if kind == "open_app":
                    app = str(action.get("app", ""))
                    executable = self._find_app(app)
                    if executable:
                        subprocess.Popen([executable], shell=False, close_fds=True)
                        detail = "Launched " + app
                    else:
                        backend.press("win")
                        time.sleep(0.25)
                        backend.write(app, interval=0)
                        time.sleep(0.7)
                        backend.press("enter")
                        detail = "Searched Windows Start and launched " + app
                    time.sleep(0.45)
                elif kind == "open_url":
                    url = validate_url(str(action.get("url", "")))
                    if not webbrowser.open_new_tab(url):
                        raise DesktopAgentError("Windows did not accept the request to open the website.")
                    detail = "Opened " + url
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
                    if len(keys) == 1:
                        backend.press(keys[0])
                    else:
                        backend.hotkey(*keys)
                    detail = "Pressed " + "+".join(keys)
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
            actions = parse_task(task)
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
