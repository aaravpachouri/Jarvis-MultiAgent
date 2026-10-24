from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from agents.desktop_agent.local_bridge import (
    BridgeHandler,
    BridgeHTTPServer,
    BridgeState,
    DesktopAgentError,
    action_summary,
    parse_task,
    validate_url,
)


def test_open_app_and_type_text_becomes_two_explicit_steps():
    actions = parse_task("Open Notepad and type Hello from JARVIS")
    assert actions == [
        {"type": "open_app", "app": "notepad"},
        {"type": "type_text", "text": "Hello from JARVIS"},
    ]


def test_common_desktop_actions_are_parsed_without_shell():
    assert parse_task("open calculator") == [{"type": "open_app", "app": "calculator"}]
    assert parse_task("click 620, 420") == [
        {"type": "click", "x": 620, "y": 420, "button": "left"}
    ]
    assert parse_task("press ctrl+l") == [
        {"type": "hotkey", "keys": ["ctrl", "l"]}
    ]
    assert parse_task("wait 1 second") == [{"type": "wait", "seconds": 1.0}]


def test_text_can_be_typed_into_a_named_allowlisted_app():
    assert parse_task("type Hello from JARVIS into Notepad") == [
        {"type": "open_app", "app": "notepad"},
        {"type": "type_text", "text": "Hello from JARVIS"},
    ]


def test_named_button_click_targets_allowlisted_app():
    assert parse_task("click button Save in Notepad") == [
        {"type": "open_app", "app": "notepad"},
        {"type": "click_ui", "control": "button", "text": "Save"},
    ]
    assert parse_task("click the Save button in Notepad") == [
        {"type": "open_app", "app": "notepad"},
        {"type": "click_ui", "control": "button", "text": "Save"},
    ]


def test_web_search_is_encoded_as_an_https_url():
    actions = parse_task("search for Python 3.12 release notes")
    assert actions[0]["type"] == "open_url"
    assert actions[0]["url"].startswith("https://www.google.com/search?q=")
    assert "Python+3.12+release+notes" in actions[0]["url"]


@pytest.mark.parametrize(
    "task",
    [
        "run powershell Get-Process",
        "shutdown computer",
        "delete files in Downloads",
        "send email to someone",
        "make a payment",
        "press ctrl+alt+delete",
    ],
)
def test_unsafe_or_unknown_actions_are_rejected(task):
    with pytest.raises(DesktopAgentError):
        parse_task(task)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///C:/Users/test.txt",
        "http://localhost:8766/anything",
        "https://user:password@example.com/",
    ],
)
def test_non_web_or_local_urls_are_rejected(url):
    with pytest.raises(DesktopAgentError):
        validate_url(url)


def test_approval_summary_shows_target_and_text_preview():
    summary = action_summary(
        [{"type": "type_text", "text": "Approved visible text"}],
        "Notepad",
    )
    assert "Current foreground window: Notepad" in summary
    assert "Approved visible text" in summary
    assert "Approve this exact sequence" in summary


class FakeExecutor:
    def status(self):
        return {"ok": True, "platform": "Windows", "desktop_available": True}


@pytest.fixture
def bridge_server(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_ALLOWED_ORIGINS", "https://example.test")
    state = BridgeState("test-pairing-token-not-a-real-secret")
    state.executor = FakeExecutor()
    server = BridgeHTTPServer(("127.0.0.1", 0), BridgeHandler, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_bridge_requires_pairing_token(bridge_server):
    with pytest.raises(urllib.error.HTTPError) as captured:
        urllib.request.urlopen(bridge_server + "/v1/status", timeout=2)
    assert captured.value.code == 401
    payload = json.loads(captured.value.read().decode("utf-8"))
    assert "pair" in payload["error"].casefold()


def test_preview_requires_auth_and_returns_allowlisted_actions(bridge_server):
    request = urllib.request.Request(
        bridge_server + "/v1/preview",
        data=json.dumps({"task": "open notepad and type Hello"}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer test-pairing-token-not-a-real-secret",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["approval_required"] is True
    assert [action["type"] for action in payload["actions"]] == ["open_app", "type_text"]


def test_bridge_rejects_unapproved_browser_origins(bridge_server):
    request = urllib.request.Request(
        bridge_server + "/v1/status",
        headers={
            "Authorization": "Bearer test-pairing-token-not-a-real-secret",
            "Origin": "https://evil.example",
        },
    )
    with pytest.raises(urllib.error.HTTPError) as captured:
        urllib.request.urlopen(request, timeout=2)
    assert captured.value.code == 403
