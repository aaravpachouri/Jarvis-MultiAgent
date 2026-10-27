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


def test_bridge_preserves_target_context_across_separate_dashboard_commands(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_ALLOWED_ORIGINS", "https://example.test")

    class WorkflowExecutor:
        def __init__(self):
            self._last_opened_url = ""
            self._last_app_name = ""
            self._last_search_query = ""
            self._controller_origin = ""
            self.calls = []

        def status(self):
            return {
                "ok": True,
                "platform": "Windows",
                "desktop_available": True,
                "active_window": "JARVIS Desktop",
            }

        def execute(self, actions):
            self.calls.append(actions)
            for action in actions:
                if action["type"] == "open_url":
                    self._last_opened_url = action["url"]
                    self._last_app_name = ""
                elif action["type"] == "open_app":
                    self._last_app_name = action["app"]
                    self._last_opened_url = ""
                elif action["type"] == "smart_type":
                    self._last_search_query = action["text"]
            return {
                "ok": True,
                "completed": [{"step": 1, "status": "succeeded", "detail": "mocked"}],
            }

    state = BridgeState("workflow-test-pairing-token")
    executor = WorkflowExecutor()
    state.executor = executor
    server = BridgeHTTPServer(("127.0.0.1", 0), BridgeHandler, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = "http://127.0.0.1:" + str(server.server_address[1]) + "/v1/execute"

    def submit(task):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({"task": task}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer workflow-test-pairing-token",
                "Origin": "https://example.test",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        first = submit("open https://www.youtube.com")
        second = submit("search for Blinding Lights")
        third = submit("press Enter")
        fourth = submit("click the first result")

        assert first["ok"] is True
        assert second["ok"] is True
        assert third["ok"] is True
        assert fourth["ok"] is True
        assert executor.calls[0][0]["type"] == "open_url"
        assert executor.calls[1] == [
            {"type": "smart_type", "text": "Blinding Lights", "target": "search"}
        ]
        assert executor.calls[2] == [{"type": "hotkey", "keys": ["enter"]}]
        assert executor.calls[3] == [
            {"type": "click_first_result", "target": "Blinding Lights"}
        ]
        assert executor._controller_origin == "https://example.test"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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


def test_follow_up_search_preserves_existing_app_context_between_submissions():
    assert parse_task("search for Blinding Lights", app_context=True) == [
        {"type": "smart_type", "text": "Blinding Lights", "target": "search"}
    ]


def test_follow_up_search_stays_web_search_without_existing_app_context():
    actions = parse_task("search for Blinding Lights", app_context=False)
    assert actions[0]["type"] == "open_url"
    assert "google.com/search" in actions[0]["url"]


def test_follow_up_first_result_inherits_previous_search_context():
    assert parse_task(
        "click the first result",
        app_context=True,
        last_search_query="Blinding Lights",
    ) == [{"type": "click_first_result", "target": "Blinding Lights"}]


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


def test_end_to_end_browser_workflow_waits_for_results_before_clicking(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    search_field = FakeUIControl()
    result_control = FakeUIControl()
    sequence = []

    actions = parse_task(
        "open https://www.youtube.com then search for The Weeknd Blinding Lights official music video "
        "then press Enter then click the first result"
    )
    assert [action["type"] for action in actions] == [
        "open_url", "smart_type", "hotkey", "click_first_result"
    ]

    def navigate(url, actual_backend):
        sequence.append(("open_url", url))
        return "Opened target and confirmed navigation"

    monkeypatch.setattr(executor, "_navigate_browser_to_url", navigate)
    monkeypatch.setattr(executor, "_ensure_last_target_tab_active", lambda: None)
    monkeypatch.setattr(
        executor,
        "_find_search_field",
        lambda timeout_seconds=8.0: (
            sequence.append(("search_field", "Search")) or object(),
            search_field,
            "Search",
        ),
    )
    monkeypatch.setattr(executor, "_capture_result_snapshot", lambda: ("pre-submit",))
    monkeypatch.setattr(executor, "_update_target_context_from_foreground", lambda: None)

    def wait_for_result(target="", timeout_seconds=20.0):
        sequence.append(("wait_for_result", target))
        # Simulate slow page loading; Enter must not finish before the result is ready.
        if len([item for item in sequence if item[0] == "wait_for_result"]) == 1:
            desktop_bridge.time.sleep(0.1)
        return result_control, "Blinding Lights — The Weeknd (Official Video)"

    monkeypatch.setattr(executor, "_find_result_control", wait_for_result)

    def click_result(control, target=""):
        sequence.append(("click_result", target))
        return "Blinding Lights — The Weeknd"

    monkeypatch.setattr(executor, "_click_result_control", click_result)

    result = executor.execute(actions)

    assert result["ok"] is True
    assert [row["status"] for row in result["completed"]] == ["succeeded"] * 4
    assert search_field.clicked == 1
    assert ("write", "The Weeknd Blinding Lights official music video") in backend.events
    assert ("press", "enter") in backend.events
    assert sequence == [
        ("open_url", "https://www.youtube.com"),
        ("search_field", "Search"),
        ("wait_for_result", "The Weeknd Blinding Lights official music video"),
        ("wait_for_result", "The Weeknd Blinding Lights official music video"),
        ("click_result", "The Weeknd Blinding Lights official music video"),
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
        self.on_click = None

    def hotkey(self, *keys):
        self.events.append(("hotkey", keys))

    def press(self, key):
        self.events.append(("press", key))

    def write(self, value, interval=0):
        self.events.append(("write", value))

    def size(self):
        from types import SimpleNamespace
        return SimpleNamespace(width=1920, height=1080)

    def moveTo(self, x, y, duration=0):
        self.events.append(("move", x, y))

    def click(self, x, y, button="left"):
        self.events.append(("click", x, y, button))
        if callable(self.on_click):
            self.on_click()


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

    def rectangle(self):
        from types import SimpleNamespace
        return SimpleNamespace(left=100, top=120, right=500, bottom=200)


def test_type_text_focuses_the_unique_accessible_document_before_typing(monkeypatch):
    import sys
    from types import SimpleNamespace

    class EditableDocument:
        def __init__(self):
            self.element_info = SimpleNamespace(
                name="Text editor", control_type="Document",
                automation_id="text-area", help_text="", class_name="RichEdit",
            )
            self.focused = False
            self.clicked = 0

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def rectangle(self):
            return SimpleNamespace(width=lambda: 800, height=lambda: 500)

        def set_focus(self):
            self.focused = True

        def has_keyboard_focus(self):
            return self.focused

        def click_input(self):
            self.clicked += 1
            self.focused = True

    document = EditableDocument()
    active = SimpleNamespace(window_text=lambda: "Untitled - Notepad", descendants=lambda: [document])
    fake_module = SimpleNamespace(Desktop=lambda backend: SimpleNamespace(get_active=lambda: active))
    monkeypatch.setitem(sys.modules, "pywinauto", fake_module)

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    detail = executor._focus_text_entry_target()

    assert document.focused is True
    assert document.clicked == 0
    assert "Notepad" in detail
    assert "Document" in detail


def test_type_text_preserves_current_caret_when_no_editable_surface_is_exposed(monkeypatch):
    import sys
    from types import SimpleNamespace

    class NonEditableControl:
        element_info = SimpleNamespace(
            name="Run", control_type="Button", automation_id="", help_text="", class_name="Button",
        )

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def rectangle(self):
            return SimpleNamespace(width=lambda: 80, height=lambda: 24)

    active = SimpleNamespace(window_text=lambda: "Custom editor", descendants=lambda: [NonEditableControl()])
    fake_module = SimpleNamespace(Desktop=lambda backend: SimpleNamespace(get_active=lambda: active))
    monkeypatch.setitem(sys.modules, "pywinauto", fake_module)

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    detail = executor._focus_text_entry_target()

    assert "current caret preserved" in detail
    assert "Custom editor" in detail


def test_type_text_preserves_current_caret_when_multiple_fields_are_equally_likely(monkeypatch):
    import sys
    from types import SimpleNamespace

    class Editable:
        def __init__(self, name):
            self.element_info = SimpleNamespace(
                name=name, control_type="Edit", automation_id="", help_text="", class_name="Edit",
            )

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def rectangle(self):
            return SimpleNamespace(width=lambda: 200, height=lambda: 30)

        def set_focus(self):
            raise AssertionError("Ambiguous fields must not be guessed")

        def click_input(self):
            raise AssertionError("Ambiguous fields must not be clicked")

    active = SimpleNamespace(window_text=lambda: "Generic application", descendants=lambda: [Editable("Field A"), Editable("Field B")])
    fake_module = SimpleNamespace(Desktop=lambda backend: SimpleNamespace(get_active=lambda: active))
    monkeypatch.setitem(sys.modules, "pywinauto", fake_module)

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    detail = executor._focus_text_entry_target()

    assert "current caret preserved" in detail


def test_result_finder_never_treats_whole_page_pane_as_a_video_result(monkeypatch):
    from types import SimpleNamespace

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    pane = FakeResultWrapper("Blinding Lights Official Video", "Pane")
    active = SimpleNamespace(window_text=lambda: "YouTube - Search results")
    controls = [(pane, "Pane", "Blinding Lights Official Video", "blinding lights official video pane")]

    monkeypatch.setattr(
        executor,
        "_active_ui_controls",
        lambda allowed: (active, [entry for entry in controls if entry[1] in allowed]),
    )

    with pytest.raises(DesktopAgentError, match="couldn't find a stable result matching"):
        executor._find_result_control(
            "The Weeknd Blinding Lights official music video",
            timeout_seconds=0.35,
        )


def test_visual_result_finder_uses_ocr_text_and_scales_verified_screen_bounds(monkeypatch):
    import sys
    from types import SimpleNamespace

    class FakeImage:
        size = (1000, 500)

    class FakeBackend(FakeKeyboardBackend):
        def screenshot(self):
            return FakeImage()

        def size(self):
            return SimpleNamespace(width=2000, height=1000)

    data = {
        "text": ["The", "Weeknd", "Blinding", "Lights", "(Official", "Video"],
        "left": [40, 80, 165, 260, 345, 445],
        "top": [100, 100, 100, 100, 100, 100],
        "width": [30, 70, 85, 75, 90, 50],
        "height": [20, 20, 20, 20, 20, 20],
        "conf": ["95"] * 6,
        "block_num": [1] * 6,
        "par_num": [1] * 6,
        "line_num": [1] * 6,
    }
    fake_module = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=""),
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda image, output_type, config, timeout: data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_module)
    monkeypatch.setattr(desktop_bridge.shutil, "which", lambda command: "tesseract")
    monkeypatch.setenv("TESSERACT_CMD", "")

    executor = DesktopActionExecutor(backend=FakeBackend())
    result = executor._find_visual_result_control(
        "The Weeknd Blinding Lights official music video"
    )

    assert result is not None
    control, label = result
    assert "Blinding Lights" in label
    assert control.element_info.control_type == "Custom"
    rect = control.rectangle()
    assert rect.left == 80
    assert rect.top == 200
    assert rect.right == 990
    assert rect.bottom == 240
    assert "found a visible match" in executor._last_ocr_fallback_status


def test_visual_search_field_finds_generic_visible_search_placeholder(monkeypatch):
    import sys
    from types import SimpleNamespace

    class FakeImage:
        size = (1000, 500)

    class FakeBackend(FakeKeyboardBackend):
        def screenshot(self):
            return FakeImage()

        def size(self):
            return SimpleNamespace(width=2000, height=1000)

    data = {
        "text": ["What", "do", "you", "want", "to", "play?"],
        "left": [50, 100, 125, 160, 210, 235],
        "top": [90] * 6,
        "width": [45, 20, 30, 45, 15, 40],
        "height": [18] * 6,
        "conf": ["95"] * 6,
        "block_num": [1] * 6,
        "par_num": [1] * 6,
        "line_num": [1] * 6,
    }
    fake_module = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=""),
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda image, output_type, config, timeout: data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_module)
    monkeypatch.setattr(desktop_bridge.shutil, "which", lambda command: "tesseract")
    monkeypatch.setenv("TESSERACT_CMD", "")

    backend = FakeBackend()
    executor = DesktopActionExecutor(backend=backend)
    found = executor._find_visual_search_field()

    assert found is not None
    control, label = found
    assert "What" in label
    assert control.element_info.automation_id == "ocr-search-field"
    control.click_input()
    assert ("click", 325, 198, "left") in backend.events


def test_smart_type_uses_visual_search_fallback_for_inaccessible_native_app(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    monkeypatch.setattr(
        executor,
        "_find_search_field",
        lambda: (_ for _ in ()).throw(DesktopAgentError("No accessible search field is exposed yet.")),
    )
    visual = FakeResultWrapper("Search songs", "Custom")
    visual.element_info.automation_id = "ocr-search-field"
    monkeypatch.setattr(executor, "_find_visual_search_field", lambda: (visual, "Search songs"))

    result = executor.execute([{"type": "smart_type", "text": "Blinding Lights", "target": "search"}])

    assert result["ok"] is True
    assert visual.clicked == 1
    assert ("hotkey", ("ctrl", "a")) in backend.events
    assert ("write", "Blinding Lights") in backend.events
    assert executor._last_search_query == "Blinding Lights"
    assert "with OCR" in result["completed"][0]["detail"]


def test_result_finder_uses_repeated_ocr_match_when_uia_exposes_no_result(monkeypatch):
    from types import SimpleNamespace

    clock = {"now": 0.0}
    monkeypatch.setattr(desktop_bridge.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    active = SimpleNamespace(window_text=lambda: "Search results")
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (active, []))
    executor._last_result_snapshot = ("Before search", ())

    visual = FakeResultWrapper("Blinding Lights Official Video", "Custom")
    visual.element_info.automation_id = "ocr-result"
    visual.rect_value = None
    calls = []

    def find_visual(target):
        calls.append(target)
        return visual, "Blinding Lights Official Video"

    monkeypatch.setattr(executor, "_find_visual_result_control", find_visual)
    control, label = executor._find_result_control(
        "The Weeknd Blinding Lights official music video",
        timeout_seconds=10.0,
    )

    assert control is visual
    assert label == "Blinding Lights Official Video"
    assert len(calls) >= 2
    assert clock["now"] >= 4.7


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


def test_result_finder_uses_ocr_when_weak_uia_match_blocks_visual_fallback(monkeypatch):
    from types import SimpleNamespace

    # A deterministic clock makes the long loading/stability windows cheap to test.
    clock = {"now": 100.0}
    def fake_monotonic():
        return clock["now"]
    def fake_sleep(seconds):
        clock["now"] += max(0.01, seconds)

    monkeypatch.setattr(desktop_bridge.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(desktop_bridge.time, "sleep", fake_sleep)

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    weak = FakeResultWrapper(
        "Blinding Lights extra related context terms not matching",
        "Custom",
    )
    active = SimpleNamespace(window_text=lambda: "Search results")
    controls = [
        (weak, "Custom", weak.element_info.name, weak.element_info.name.casefold())
    ]
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (active, controls))
    executor._last_result_snapshot = executor._result_tree_signature(active, controls)

    visual_rect = SimpleNamespace(left=820, top=600, right=1020, bottom=660)
    visual = SimpleNamespace(
        element_info=SimpleNamespace(
            name="Blinding Lights", control_type="Custom",
            automation_id="ocr-result", help_text="OCR-visible result text",
            class_name="VisualResultTarget",
        ),
        rectangle=lambda: visual_rect,
        parent=lambda: None,
    )
    visual_calls = []
    monkeypatch.setattr(
        executor,
        "_find_visual_result_control",
        lambda target: visual_calls.append(target) or (visual, "Blinding Lights"),
    )

    found, label = executor._find_result_control(
        "The Weeknd Blinding Lights official music video",
        timeout_seconds=8.0,
    )

    assert found is visual
    assert label == "Blinding Lights"
    assert len(visual_calls) >= 2
    assert clock["now"] >= 105.0


def test_result_finder_does_not_click_a_matching_stale_tree_after_enter(monkeypatch):
    from types import SimpleNamespace

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    title = FakeResultWrapper("Blinding Lights", "Text")
    controls = [(title, "Text", "Blinding Lights", "blinding lights text")]
    active = SimpleNamespace(window_text=lambda: "Search results")
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (active, controls))

    executor._last_result_snapshot = executor._result_tree_signature(active, controls)
    executor._last_search_submitted_at = 0.0

    with pytest.raises(DesktopAgentError, match="within the wait window"):
        executor._find_result_control(
            "The Weeknd Blinding Lights official music video",
            timeout_seconds=0.35,
        )


def test_result_finder_allows_strong_stable_match_when_accessibility_tree_is_stale(monkeypatch):
    from types import SimpleNamespace

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    title = FakeResultWrapper("Blinding Lights", "Text")
    controls = [(title, "Text", "Blinding Lights", "blinding lights text")]
    active = SimpleNamespace(window_text=lambda: "Search results")
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (active, controls))

    # Some browser/app accessibility providers don't refresh their tree after Enter.
    executor._last_result_snapshot = executor._result_tree_signature(active, controls)
    executor._last_search_submitted_at = 0.0

    found, label = executor._find_result_control(
        "The Weeknd Blinding Lights official music video",
        timeout_seconds=2.0,
    )

    assert found is title
    assert label == "Blinding Lights"


def test_result_finder_waits_for_slow_tree_update_before_returning_result(monkeypatch):
    from types import SimpleNamespace
    import time as time_module

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    old_title = FakeResultWrapper("Old unrelated result", "Text")
    new_title = FakeResultWrapper("Blinding Lights", "Text")
    active = SimpleNamespace(window_text=lambda: "Search results")
    old_controls = [(old_title, "Text", "Old unrelated result", "old unrelated result")]
    new_controls = [(new_title, "Text", "Blinding Lights", "blinding lights")]
    started = time_module.monotonic()

    def delayed_tree(_allowed):
        if time_module.monotonic() - started < 1.2:
            return active, old_controls
        return active, new_controls

    monkeypatch.setattr(executor, "_active_ui_controls", delayed_tree)
    executor._last_result_snapshot = executor._result_tree_signature(active, old_controls)
    executor._last_search_submitted_at = time_module.monotonic()

    found, label = executor._find_result_control(
        "The Weeknd Blinding Lights official music video",
        timeout_seconds=4.0,
    )

    assert found is new_title
    assert label == "Blinding Lights"
    assert time_module.monotonic() - started >= 1.5


def test_browser_content_change_without_navigation_is_not_click_success(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    before = (True, ("search results", ("https://example.test/search",), ("search tab",), (("Text", "old result"),)))
    content_only_change = (
        True,
        ("search results", ("https://example.test/search",), ("search tab",), (("Text", "new dynamic recommendation"),)),
    )
    monkeypatch.setattr(executor, "_capture_active_page_state", lambda: content_only_change)
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)

    assert executor._wait_for_active_page_change(before, timeout_seconds=0.02) is False


def test_browser_url_change_is_click_success(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    before = (True, ("search results", ("https://example.test/search",), ("search tab",), (("Text", "result"),)))
    after = (True, ("target title", ("https://example.test/watch/123",), ("target tab",), (("Text", "player"),)))
    snapshots = iter([after])
    monkeypatch.setattr(executor, "_capture_active_page_state", lambda: next(snapshots))
    assert executor._wait_for_active_page_change(before, timeout_seconds=0.1) is True


def test_browser_target_without_accessible_omnibox_still_uses_physical_click_fallback(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    executor._last_target_browser_url = "https://www.youtube.com"
    link = FakeResultWrapper("Blinding Lights", "Hyperlink")

    # A browser may not expose its omnibox through UI Automation. The known target
    # URL must still make Atlas verify navigation and try the grounded bounds click.
    monkeypatch.setattr(
        executor,
        "_capture_active_page_state",
        lambda: (False, ("search results", ("",), ("tab",), (("Hyperlink", "blinding lights"),))),
    )
    waits = iter([False, True])
    monkeypatch.setattr(
        executor,
        "_wait_for_active_page_change",
        lambda before, timeout_seconds=6.0: next(waits),
    )

    name = executor._click_result_control(link)

    assert name == "Blinding Lights"
    assert link.clicked == 1
    assert ("click", 300, 160, "left") in backend.events


def test_browser_with_missing_omnibox_does_not_accept_content_churn_as_navigation(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    executor._last_target_browser_url = "https://www.youtube.com"
    before = (
        False,
        ("search results", ("",), ("YouTube tab",), (("Text", "old content"),)),
    )
    after = (
        False,
        ("search results", ("",), ("YouTube tab",), (("Text", "new dynamic recommendation"),)),
    )
    monkeypatch.setattr(executor, "_capture_active_page_state", lambda: after)
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)

    assert executor._wait_for_active_page_change(before, timeout_seconds=0.02) is False


def test_result_click_uses_verified_bounds_if_semantic_click_has_no_effect(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    link = FakeResultWrapper("Blinding Lights", "Hyperlink")
    state = {"changed": False}
    backend.on_click = lambda: state.update(changed=True)
    monkeypatch.setattr(
        executor,
        "_capture_active_page_state",
        lambda: (True, ("after",) if state["changed"] else ("before",)),
    )
    waits = iter([False, True])
    monkeypatch.setattr(
        executor,
        "_wait_for_active_page_change",
        lambda before, timeout_seconds=6.0: next(waits),
    )

    name = executor._click_result_control(link)

    assert name == "Blinding Lights"
    assert link.clicked == 1
    assert ("click", 300, 160, "left") in backend.events


def test_result_click_retries_with_ocr_title_bounds_when_uia_target_does_not_navigate(monkeypatch):
    from types import SimpleNamespace

    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    executor._last_target_browser_url = "https://example.test/search"
    link = FakeResultWrapper("Blinding Lights", "Hyperlink")
    visual_rect = SimpleNamespace(left=820, top=600, right=1020, bottom=660)
    visual = SimpleNamespace(
        element_info=SimpleNamespace(name="Blinding Lights", control_type="Custom"),
        rectangle=lambda: visual_rect,
        parent=lambda: None,
    )
    monkeypatch.setattr(
        executor,
        "_capture_active_page_state",
        lambda: (True, ("search results", ("https://example.test/search",), ("tab",), ())),
    )
    waits = iter([False, False, True])
    monkeypatch.setattr(
        executor,
        "_wait_for_active_page_change",
        lambda before, timeout_seconds=6.0: next(waits),
    )
    monkeypatch.setattr(
        executor,
        "_find_visual_result_control",
        lambda target: (visual, "Blinding Lights"),
    )

    result = executor._click_result_control(
        link,
        target="The Weeknd Blinding Lights official music video",
    )

    assert "OCR-verified visible title" in result
    assert link.clicked == 1
    assert ("click", 300, 160, "left") in backend.events
    assert ("move", 920, 630) in backend.events
    assert ("click", 920, 630, "left") in backend.events


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


def test_browser_search_falls_back_to_default_omnibox_without_site_specific_urls(monkeypatch):
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
    monkeypatch.setattr(
        executor,
        "_find_browser_address_bar",
        lambda timeout_seconds=1.5: (object(), object(), "Address and search bar"),
    )
    monkeypatch.setattr(executor, "_capture_result_snapshot", lambda: ("pre-search",))
    monkeypatch.setattr(
        executor,
        "_find_result_control",
        lambda target="", timeout_seconds=20.0: (FakeUIControl(), "Universal search query"),
    )

    # This represents a browser that was already open, not one opened by
    # this command: fallback must detect the omnibox from accessibility metadata.
    result = executor.execute([
        {"type": "smart_type", "text": "Universal search query", "target": "search"},
    ])

    assert result["ok"] is True
    assert "default search" in result["completed"][0]["detail"]
    assert "stable result" in result["completed"][0]["detail"]
    assert ("hotkey", ("ctrl", "l")) in backend.events
    assert ("write", "Universal search query") in backend.events
    assert ("press", "enter") in backend.events
    assert executor._last_search_query == "Universal search query"
    assert executor._last_result_snapshot == ("pre-search",)
    assert executor._last_search_submitted_at > 0


def test_browser_url_identity_matches_normalized_host_and_path():
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    assert executor._browser_url_matches(
        "https://youtube.com/",
        "https://www.youtube.com/",
    )
    assert executor._browser_url_matches(
        "https://example.test/watch/123",
        "https://www.example.test/watch/123",
    )
    assert not executor._browser_url_matches(
        "https://example.test/watch/123",
        "https://example.test/watch/456",
    )
    assert executor._browser_url_matches(
        "https://example.test/search?q=one",
        "https://example.test/search?q=one&client=desktop",
    )
    assert not executor._browser_url_matches(
        "https://example.test/search?q=one",
        "https://example.test/search?q=two",
    )


def test_browser_url_wait_blocks_until_expected_location_is_visible(monkeypatch):
    import time as time_module

    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    started = time_module.monotonic()

    class AddressControl:
        def get_value(self):
            if time_module.monotonic() - started < 0.35:
                return "https://jarvis-multiagent-dashboard.vercel.app/desktop"
            return "https://www.example.test/search?q=correct"

    class ActiveWindow:
        def window_text(self):
            return "JARVIS Desktop" if time_module.monotonic() - started < 0.35 else "Search results"

    address = AddressControl()
    active = ActiveWindow()
    monkeypatch.setattr(
        executor, "_find_browser_address_bar",
        lambda timeout_seconds=0.35: (active, address, "Address and search bar"),
    )

    _, title = executor._wait_for_browser_url(
        "https://example.test/search?q=correct",
        timeout_seconds=2.0,
        stable_seconds=0.15,
    )

    assert title == "Search results"
    assert time_module.monotonic() - started >= 0.45


def test_browser_url_wait_fails_closed_if_address_never_reaches_target(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    active = type("ActiveWindow", (), {"window_text": lambda self: "JARVIS Desktop"})()
    address = type(
        "AddressControl",
        (),
        {"get_value": lambda self: "https://jarvis-multiagent-dashboard.vercel.app/desktop"},
    )()
    monkeypatch.setattr(
        executor, "_find_browser_address_bar",
        lambda timeout_seconds=0.35: (active, address, "Address and search bar"),
    )

    with pytest.raises(DesktopAgentError, match="did not confirm navigation"):
        executor._wait_for_browser_url(
            "https://example.test/",
            timeout_seconds=0.45,
            stable_seconds=0.1,
        )


def test_enter_waits_for_a_matching_result_before_command_completes(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    executor._last_search_query = "Blinding Lights"
    waited = []
    monkeypatch.setattr(
        executor, "_find_result_control",
        lambda target="", timeout_seconds=20.0: waited.append((target, timeout_seconds))
        or (FakeUIControl(), "Blinding Lights"),
    )
    monkeypatch.setattr(executor, "_update_target_context_from_foreground", lambda: None)

    result = executor.execute([{"type": "hotkey", "keys": ["enter"]}])

    assert result["ok"] is True
    assert waited == [("Blinding Lights", 20.0)]
    assert "waited for a stable matching result" in result["completed"][0]["detail"]
    assert ("press", "enter") in backend.events


def test_open_url_reuses_current_browser_tab_and_confirms_navigation(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    opened = []
    waited = []
    monkeypatch.setattr(
        executor, "_find_browser_address_bar",
        lambda timeout_seconds=1.5: (object(), object(), "Address and search bar"),
    )
    monkeypatch.setattr(
        executor, "_wait_for_browser_url",
        lambda url, timeout_seconds=25.0, stable_seconds=0.8: waited.append(url) or (object(), "Example"),
    )
    monkeypatch.setattr(
        executor, "_wait_for_active_window_settled",
        lambda timeout_seconds=12.0, stable_seconds=0.8: True,
    )
    monkeypatch.setattr(
        desktop_bridge.webbrowser, "open_new_tab",
        lambda url: opened.append(url) or True,
    )
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)

    detail = executor._navigate_browser_to_url("https://example.test/watch/123", backend)

    assert "current browser tab" in detail
    assert opened == []
    assert waited == ["https://example.test/watch/123", "https://example.test/watch/123"]
    assert ("hotkey", ("ctrl", "l")) in backend.events
    assert ("write", "https://example.test/watch/123") in backend.events
    assert ("press", "enter") in backend.events


def test_open_url_opens_a_separate_tab_when_current_tab_is_the_dashboard(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    executor._controller_origin = "https://jarvis-multiagent-dashboard.vercel.app"
    opened = []
    controller = type("ActiveWindow", (), {"window_text": lambda self: "JARVIS Desktop"})()
    address_bar = type(
        "AddressBar",
        (),
        {"get_value": lambda self: "https://jarvis-multiagent-dashboard.vercel.app/desktop"},
    )()
    tab = FakeResultWrapper("YouTube", "TabItem")
    tab.element_info.runtime_id = (17,)
    tab.iface_selection_item = type(
        "Selection", (), {"CurrentIsSelected": True}
    )()
    monkeypatch.setattr(
        executor,
        "_find_browser_address_bar",
        lambda timeout_seconds=1.5: (controller, address_bar, "Address and search bar"),
    )
    monkeypatch.setattr(
        executor,
        "_wait_for_browser_url",
        lambda url, timeout_seconds=25.0, stable_seconds=0.8: (controller, "YouTube"),
    )
    monkeypatch.setattr(
        executor,
        "_wait_for_active_window_settled",
        lambda timeout_seconds=12.0, stable_seconds=0.8: True,
    )
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (controller, [
        (tab, "TabItem", "YouTube", "youtube tab")
    ]))
    monkeypatch.setattr(
        desktop_bridge.webbrowser, "open_new_tab",
        lambda url: opened.append(url) or True,
    )
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)

    detail = executor._navigate_browser_to_url("https://www.youtube.com/", backend)

    assert opened == ["https://www.youtube.com/"]
    assert "preserve the JARVIS dashboard" in detail
    assert not any(event[:2] == ("hotkey", ("ctrl", "l")) for event in backend.events)
    assert executor._last_target_browser_url == "https://www.youtube.com/"
    assert executor._last_target_window_title == "YouTube"
    assert executor._last_target_tab_runtime_id is not None


def test_follow_up_action_restores_the_saved_target_tab(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    executor._last_target_browser_url = "https://www.youtube.com/"
    executor._last_opened_url = "https://www.youtube.com/"
    executor._last_target_window_title = "YouTube - Google Chrome"
    executor._last_target_tab_runtime_id = (42,)
    dashboard = type("ActiveWindow", (), {"window_text": lambda self: "JARVIS Desktop"})()
    address_bar = type(
        "AddressBar",
        (),
        {"get_value": lambda self: "https://jarvis-multiagent-dashboard.vercel.app/desktop"},
    )()
    target_tab = FakeResultWrapper("YouTube", "TabItem")
    target_tab.element_info.runtime_id = (42,)
    target_tab.iface_selection_item = type(
        "Selection", (), {"CurrentIsSelected": False}
    )()
    monkeypatch.setattr(
        executor, "_find_browser_address_bar",
        lambda timeout_seconds=1.5: (dashboard, address_bar, "Address and search bar"),
    )
    monkeypatch.setattr(executor, "_active_ui_controls", lambda allowed: (dashboard, [
        (target_tab, "TabItem", "YouTube", "youtube tab"),
    ]))
    monkeypatch.setattr(
        executor, "_wait_for_browser_url",
        lambda url, timeout_seconds=12.0, stable_seconds=0.35: (dashboard, "YouTube"),
    )
    monkeypatch.setattr(
        executor, "_wait_for_active_window_settled",
        lambda timeout_seconds=5.0, stable_seconds=0.7: True,
    )
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)

    executor._ensure_last_target_tab_active()

    assert target_tab.clicked == 1
    assert executor._last_target_window_title == "YouTube"


def test_target_tab_runtime_identity_wins_over_same_host_tab(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    executor._last_target_browser_url = "https://www.youtube.com/"
    executor._last_opened_url = "https://www.youtube.com/"
    executor._last_target_window_title = "Blinding Lights - YouTube"
    executor._last_target_tab_runtime_id = (42,)
    active = type("ActiveWindow", (), {"window_text": lambda self: "YouTube - Home"})()
    address = type(
        "AddressBar",
        (),
        {"get_value": lambda self: "https://www.youtube.com/"},
    )()
    current_tab = FakeResultWrapper("YouTube - Home", "TabItem")
    current_tab.element_info.runtime_id = (99,)
    current_tab.iface_selection_item = type(
        "Selection", (), {"CurrentIsSelected": True}
    )()
    target_tab = FakeResultWrapper("Blinding Lights - YouTube", "TabItem")
    target_tab.element_info.runtime_id = (42,)
    target_tab.iface_selection_item = type(
        "Selection", (), {"CurrentIsSelected": False}
    )()
    monkeypatch.setattr(
        executor, "_find_browser_address_bar",
        lambda timeout_seconds=0.6: (active, address, "Address and search bar"),
    )
    monkeypatch.setattr(
        executor, "_active_ui_controls",
        lambda allowed: (active, [
            (current_tab, "TabItem", "YouTube - Home", "youtube home"),
            (target_tab, "TabItem", "Blinding Lights - YouTube", "youtube video"),
        ]),
    )
    monkeypatch.setattr(
        executor, "_wait_for_browser_url",
        lambda url, timeout_seconds=12.0, stable_seconds=0.35: (active, "Blinding Lights - YouTube"),
    )
    monkeypatch.setattr(
        executor, "_wait_for_active_window_settled",
        lambda timeout_seconds=5.0, stable_seconds=0.7: True,
    )
    monkeypatch.setattr(executor, "_remember_active_browser_tab", lambda: None)
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)

    executor._ensure_last_target_tab_active()

    assert target_tab.clicked == 1
    assert current_tab.clicked == 0


def test_open_app_waits_for_real_foreground_window_not_start_menu(monkeypatch):
    executor = DesktopActionExecutor(backend=FakeKeyboardBackend())
    states = iter([
        (2, 20, "Start"),
        (2, 20, "Start"),
        (3, 30, "Album - Music Player"),
        (3, 30, "Album - Music Player"),
    ])
    monkeypatch.setattr(executor, "_foreground_window_identity", lambda: next(states))
    monkeypatch.setattr(
        executor, "_wait_for_active_window_settled",
        lambda timeout_seconds=4.0, stable_seconds=0.75: True,
    )

    found = executor._wait_for_app_foreground(
        "Music Player",
        (1, 10, "JARVIS Desktop"),
        timeout_seconds=2.0,
        stable_seconds=0.1,
    )

    assert found == (3, 30, "Album - Music Player")


def test_open_url_launches_browser_only_when_no_browser_is_foregrounded(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    opened = []
    monkeypatch.setattr(
        executor, "_find_browser_address_bar",
        lambda timeout_seconds=1.5: (_ for _ in ()).throw(
            DesktopAgentError("No browser window is foregrounded")
        ),
    )
    monkeypatch.setattr(
        executor, "_wait_for_browser_url",
        lambda url, timeout_seconds=25.0, stable_seconds=0.8: (object(), "Example"),
    )
    monkeypatch.setattr(
        executor, "_wait_for_active_window_settled",
        lambda timeout_seconds=12.0, stable_seconds=0.8: True,
    )
    monkeypatch.setattr(
        desktop_bridge.webbrowser, "open_new_tab",
        lambda url: opened.append(url) or True,
    )
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)

    detail = executor._navigate_browser_to_url("https://example.test/", backend)

    assert "opened the target in a new browser tab" in detail
    assert opened == ["https://example.test/"]
    assert not any(event[:2] == ("hotkey", ("ctrl", "l")) for event in backend.events)


def test_inaccessible_in_page_search_scopes_browser_fallback_to_current_host(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    executor._last_opened_url = "https://music.example.test/library"
    monkeypatch.setattr(desktop_bridge.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        executor,
        "_find_search_field",
        lambda timeout_seconds=8.0: (_ for _ in ()).throw(
            DesktopAgentError("No accessible search field is exposed yet.")
        ),
    )
    monkeypatch.setattr(
        executor,
        "_find_browser_address_bar",
        lambda timeout_seconds=1.5: (object(), object(), "Address and search bar"),
    )
    monkeypatch.setattr(executor, "_capture_result_snapshot", lambda: ("pre-search",))
    monkeypatch.setattr(
        executor,
        "_find_result_control",
        lambda target="", timeout_seconds=20.0: (FakeUIControl(), "artist album title"),
    )

    result = executor.execute([
        {"type": "smart_type", "text": "artist album title", "target": "search"},
    ])

    assert result["ok"] is True
    assert ("write", "site:music.example.test artist album title") in backend.events
    assert executor._last_search_query == "artist album title"
    assert "site-restricted" in result["completed"][0]["detail"]


def test_redundant_enter_keeps_pre_search_snapshot_for_result_wait(monkeypatch):
    backend = FakeKeyboardBackend()
    executor = DesktopActionExecutor(backend=backend)
    executor._last_search_query = "sample query"
    executor._last_result_snapshot = ("results-before-query",)
    executor._last_search_submitted_at = 123.0
    executor._search_navigation_completed = True

    result = executor.execute([{"type": "hotkey", "keys": ["enter"]}])

    assert result["ok"] is True
    assert executor._last_result_snapshot == ("results-before-query",)
    assert executor._last_search_submitted_at == 123.0
    assert executor._search_navigation_completed is False
    assert not any(event[0] == "press" and event[1] == "enter" for event in backend.events)


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
