"""Compatibility checks for the Phase 12 HUD, updated for the native Phase 13+ design.

The old Phase 12 tests required a flattened image and static HTML agent hotspots.
The current design intentionally draws the suit in SVG and renders agent nodes from
runtime data, so these tests protect the current behavior instead of reverting it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "dashboard" / "index.html"
CSS_PATH = ROOT / "dashboard" / "assets" / "styles.css"
JS_PATH = ROOT / "dashboard" / "assets" / "app.js"
EXPECTED_AGENT_IDS = {
    "research.athena", "desktop.atlas", "coding.forge", "voice.jarvis",
    "deployment.launch", "memory.notes", "planning.planner", "trading.quant",
    "search.scout", "documentation.scribe",
}


def test_overview_uses_native_suit_art_and_hides_legacy_dashboard() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    assert '<body class="hud-active">' in html
    assert 'id="hud-main-board"' in html
    assert 'class="mk-suit-drawing"' in html
    assert 'class="mk-reactor"' in html
    assert 'class="mk-connection-svg"' in html
    assert 'class="mk-panel mk-focus-panel"' in html
    assert 'class="mk-panel mk-mission-panel"' in html
    assert 'class="mk-panel mk-intelligence-panel"' in html
    overview = html.split('<section class="view active" id="view-overview">', 1)[1].split('<section class="view" id="view-agents">', 1)[0]
    assert 'jarvis-main-hud.png' not in overview
    assert 'jarvis-hud-reference.png' not in overview
    assert '<img class="hud-main-art"' not in overview
    assert 'mk-suit-drawing' in css and 'mk-reactor-spin' in css and 'mk-scan' in css


def test_all_ten_runtime_agents_have_dynamic_clickable_hud_entry_points() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    for agent_id in EXPECTED_AGENT_IDS:
        assert f"'{agent_id}'" in js
    assert 'data-agent-id="${node.id}"' in js
    assert "state.selectedAgentId = agentId" in js
    assert "renderHudFocusPanel()" in js


def test_hud_command_and_specialist_panel_are_connected_to_runtime() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")
    assert 'id="hud-command-form"' in html
    assert 'id="hud-direct-form"' in html
    assert "api('/api/overview')" in js
    assert "api('/api/tasks'" in js
    assert "openHudPanel('agent', agentId)" not in js or "function openHudPanel(kind, agentId = null)" in js
    assert "renderHudFocusPanel()" in js
