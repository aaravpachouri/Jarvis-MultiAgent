from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import agents.desktop_agent.local_bridge as desktop_bridge
from agents.desktop_agent.local_bridge import (
    BridgeHandler,
    BridgeHTTPServer,
    BridgeState,
    DesktopActionExecutor,
    DesktopAgentError,
    action_summary,
    parse_task,
    validate_url,
    approval_reason,
    allowed_origins,
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
    assert payload["approval_required"] is False
    assert payload["execution_mode"] == "routine_auto_run"
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


def test_any_installed_app_name_can_be_parsed_without_a_fixed_allowlist():
    assert parse_task("open Spotify") == [{"type": "open_app", "app": "Spotify"}]
    assert parse_task("open Microsoft Word") == [{"type": "open_app", "app": "Microsoft Word"}]
    assert parse_task("open Windows Settings") == [{"type": "open_app", "app": "Windows Settings"}]


def test_generic_app_target_can_be_opened_and_typed_into():
    assert parse_task("type hello from JARVIS into Discord") == [
        {"type": "open_app", "app": "Discord"},
        {"type": "type_text", "text": "hello from JARVIS"},
    ]


def test_routine_tasks_do_not_require_approval():
    actions = parse_task("open notepad and type Hello from JARVIS")
    assert approval_reason("open notepad and type Hello from JARVIS", actions, "Desktop") is None
    assert approval_reason("search for Python release notes", parse_task("search for Python release notes"), "Desktop") is None


def test_sensitive_tasks_and_ambiguous_coordinates_require_approval():
    assert approval_reason("click button Send", parse_task("click button Send"), "Inbox") is not None
    assert approval_reason("click 620, 420", parse_task("click 620, 420"), "Desktop") is not None
    assert approval_reason("open PowerShell", parse_task("open PowerShell"), "Desktop") is not None
    assert approval_reason("type 123456 into Notepad", parse_task("type 123456 into Notepad"), "Password dialog") is not None


def test_named_routine_control_does_not_require_approval():
    assert approval_reason("click button Refresh", parse_task("click button Refresh"), "Browser") is None


def test_shell_commands_remain_unsupported():
    for task in ("run powershell Get-Process", "delete files in Downloads", "make a payment"):
        with pytest.raises(DesktopAgentError):
            parse_task(task)


def test_general_mouse_actions_are_parsed():
    assert parse_task("scroll down") == [{"type": "scroll", "amount": -500}]
    assert parse_task("scroll up 750") == [{"type": "scroll", "amount": 750}]
    assert parse_task("double click 100, 200") == [{"type": "double_click", "x": 100, "y": 200}]
    assert parse_task("drag 100, 200 to 300, 400") == [
        {"type": "drag", "x1": 100, "y1": 200, "x2": 300, "y2": 400}
    ]


def test_right_click_and_generic_app_browser_search():
    assert parse_task("right click 210, 120") == [
        {"type": "click", "x": 210, "y": 120, "button": "right"}
    ]
    actions = parse_task("open Spotify and search for focus music")
    assert actions == [
        {"type": "open_app", "app": "Spotify"},
        {"type": "smart_type", "text": "focus music", "target": "search"},
    ]


def test_default_dashboard_origins_include_production_and_feature_preview(monkeypatch):
    monkeypatch.delenv("JARVIS_DESKTOP_ALLOWED_ORIGINS", raising=False)
    origins = allowed_origins()
    assert "https://jarvis-multiagent-dashboard.vercel.app" in origins
    assert "https://jarvis-multiagent-dashboard-git-publish-ja-77d6ea-aaravpachouri.vercel.app" in origins


def test_generic_app_search_and_result_workflow_is_sequenced():
    actions = parse_task(
        "open Spotify then search for Blinding Lights then press Enter then click the result"
    )
    assert actions == [
        {"type": "open_app", "app": "Spotify"},
        {"type": "smart_type", "text": "Blinding Lights", "target": "search"},
        {"type": "hotkey", "keys": ["enter"]},
        {"type": "click_first_result", "target": "Blinding Lights"},
    ]


def test_browser_url_then_search_uses_the_open_page_not_a_second_google_tab():
    actions = parse_task(
        "open https://www.youtube.com then search for The Weeknd Blinding Lights official music video "
        "then press Enter then click the first result"
    )
    assert actions == [
        {"type": "open_url", "url": "https://www.youtube.com"},
        {
            "type": "smart_type",
            "text": "The Weeknd Blinding Lights official music video",
            "target": "search",
        },
        {"type": "hotkey", "keys": ["enter"]},
        {
            "type": "click_first_result",
            "target": "The Weeknd Blinding Lights official music video",
        },
    ]


def test_search_box_instructions_use_adaptive_ui_automation():
    assert parse_task("type Blinding Lights into the search bar") == [
        {"type": "smart_type", "text": "Blinding Lights", "target": "search"}
    ]
    assert parse_task("focus the search bar") == [{"type": "focus_search"}]
    assert parse_task("click result Blinding Lights") == [
        {"type": "click_result", "target": "Blinding Lights"}
    ]


def test_sequencing_accepts_commas_and_and_connectors():
    actions = parse_task(
        "open Spotify and search for Blinding Lights, press Enter, and click first result"
    )
    assert [action["type"] for action in actions] == [
        "open_app", "smart_type", "hotkey", "click_first_result"
    ]
    assert actions[-1]["target"] == "Blinding Lights"


def test_standalone_enter_and_back_forward_navigation_steps():
    assert parse_task("Enter") == [{"type": "hotkey", "keys": ["enter"]}]
    assert parse_task("hit Enter") == [{"type": "hotkey", "keys": ["enter"]}]
    assert parse_task("go back") == [{"type": "hotkey", "keys": ["alt", "left"]}]
    assert parse_task("go forward") == [{"type": "hotkey", "keys": ["alt", "right"]}]


def test_named_result_can_be_described_as_called_or_named():
    assert parse_task("click the result named Blinding Lights") == [
        {"type": "click_result", "target": "Blinding Lights"}
    ]


class FakeKeyboardBackend:
    def __init__(self):
        self.events = []

    def hotkey(self, *keys):
        self.events.append(("hotkey", keys))

    def press(self, key):
        self.events.append(("press", key))

    def write(self, value, interval=0):
        self.events.append(("write", value))


class FakeUIControl:
    def __init__(self):
        self.clicked = 0

    def click_input(self):
        self.clicked += 1


class FakeResultWrapper:
    def __init__(self, name, control_type, parent=None):
        from types import SimpleNamespace

        self.element_info = SimpleNamespace(
            name=name,
            control_type=control_type,
            automation_id="",
            help_text="",
            class_name="",
        )
        self._parent = parent
        self.clicked = 0

    def parent(self):
        return self._parent

    def click_input(self):
        self.clicked += 1


def test_result_finder_matches_short_browser_title_against_long_query(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    title = FakeResultWrapper("Blinding Lights", "Text")
    visible_controls = [
        (title, "Text", "Blinding Lights", "blinding lights text"),
    ]
    monkeypatch.setattr(
        executor,
        "_active_ui_controls",
        lambda allowed: (object(), visible_controls),
    )

    found, label = executor._find_result_control(
        "The Weeknd Blinding Lights official music video",
        timeout_seconds=2.0,
    )

    assert found is title
    assert label == "Blinding Lights"


def test_result_finder_does_not_click_a_matching_stale_tree_after_enter(monkeypatch):
    from types import SimpleNamespace

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    title = FakeResultWrapper("Blinding Lights", "Text")
    controls = [(title, "Text", "Blinding Lights", "blinding lights text")]
    active = SimpleNamespace(window_text=lambda: "Search results")
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (active, controls))

    executor._last_result_snapshot = executor._result_tree_signature(active, controls)
    executor._last_search_submitted_at = 0.0

    with pytest.raises(DesktopAgentError, match="did not visibly update"):
        executor._find_result_control(
            "The Weeknd Blinding Lights official music video",
            timeout_seconds=0.35,
        )


def test_result_click_promotes_title_text_to_its_clickable_ancestor(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    link = FakeResultWrapper("Blinding Lights — The Weeknd", "Hyperlink")
    title = FakeResultWrapper("Blinding Lights", "Text", parent=link)
    monkeypatch.setattr(
        executor,
        "_find_result_control",
        lambda target="", timeout_seconds=8.0: (title, "Blinding Lights"),
    )

    result = executor.execute([
        {
            "type": "click_first_result",
            "target": "The Weeknd Blinding Lights official music video",
        }
    ])

    assert result["ok"] is True
    assert link.clicked == 1
    assert title.clicked == 0
    assert "Clicked result" in result["completed"][0]["detail"]


def test_youtube_search_falls_back_to_results_url_when_page_search_is_not_accessible(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    monkeypatch.setattr(desktop_bridge.webbrowser, "open_new_tab", lambda url: True)
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        executor,
        "_find_search_field",
        lambda timeout_seconds=8.0: (_ for _ in ()).throw(
            DesktopAgentError("No accessible search field is exposed yet.")
        ),
    )

    result = executor.execute([
        {"type": "open_url", "url": "https://www.youtube.com"},
        {"type": "smart_type", "text": "The Weeknd Blinding Lights", "target": "search"},
    ])

    assert result["ok"] is True
    assert "YouTube search results" in result["completed"][1]["detail"]
    assert ("hotkey", ("ctrl", "l")) in backend.events
    assert (
        "write",
        "https://www.youtube.com/results?search_query=The+Weeknd+Blinding+Lights",
    ) in backend.events
    assert ("press", "enter") in backend.events
    assert executor._last_search_query == "The Weeknd Blinding Lights"


def test_youtube_page_never_uses_address_bar_as_generic_search_field(monkeypatch):
    # The browser's address bar must not be accepted as the page's only text field.
    class Rect:
        def width(self):
            return 120
        def height(self):
            return 24

    class Control:
        def __init__(self, name, kind="Edit"):
            self.element_info = type(
                "ElementInfo",
                (),
                {
                    "name": name,
                    "automation_id": "",
                    "help_text": "",
                    "class_name": "",
                    "control_type": kind,
                },
            )()

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def rectangle(self):
            return Rect()

        def window_text(self):
            return self.element_info.name

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    address_control = Control("Address and search bar")
    address = (
        address_control,
        "Edit",
        "Address and search bar",
        "address and search bar",
    )
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (object(), [address]))
    with pytest.raises(DesktopAgentError, match="No accessible search field"):
        executor._find_search_field(timeout_seconds=0.01)


def test_search_query_is_remembered_for_a_follow_up_result_click(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    search_field = FakeUIControl()
    chosen_results = []
    result_control = FakeUIControl()

    monkeypatch.setattr(
        executor, "_find_search_field",
        lambda timeout_seconds=8.0: (object(), search_field, "Search"),
    )

    first = executor.execute([
        {"type": "smart_type", "text": "Blinding Lights", "target": "search"}
    ])
    assert first["ok"] is True
    assert executor._last_search_query == "Blinding Lights"
    assert search_field.clicked == 1
    assert ("write", "Blinding Lights") in backend.events

    def select_result(target="", timeout_seconds=8.0):
        chosen_results.append(target)
        return result_control, "Blinding Lights — The Weeknd"

    monkeypatch.setattr(executor, "_find_result_control", select_result)
    second = executor.execute([{"type": "click_first_result"}])
    assert second["ok"] is True
    assert chosen_results == ["Blinding Lights"]
    assert result_control.clicked == 1
