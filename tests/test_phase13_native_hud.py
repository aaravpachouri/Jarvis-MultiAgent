"""Regression checks for the native-built Phase 13 JARVIS command environment."""
from __future__ import annotations

import re
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


def test_overview_is_a_native_built_hud_not_a_flattened_reference_image() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    assert 'id="hud-main-board"' in html
    assert 'class="mk-suit-drawing"' in html
    assert 'class="mk-reactor"' in html
    assert 'class="mk-connection-svg"' in html
    assert 'class="mk-panel mk-focus-panel"' in html
    assert 'class="mk-panel mk-mission-panel"' in html
    assert 'class="mk-panel mk-intelligence-panel"' in html
    # The supplied image is visual inspiration only, never the dashboard background.
    overview = html.split('<section class="view active" id="view-overview">', 1)[1].split('<section class="view" id="view-agents">', 1)[0]
    assert 'jarvis-main-hud.png' not in overview
    assert 'jarvis-hud-reference.png' not in overview
    assert '<img class="hud-main-art"' not in overview
    assert 'mk-suit-drawing' in css and 'mk-reactor-spin' in css and 'mk-scan' in css
    assert 'function renderLivingNetwork()' in js
    assert 'class="mk-agent-node' in js


def test_all_ten_runtime_agents_are_mapped_to_interactive_native_nodes() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    found = set(re.findall(r"'([a-z]+\.[a-z]+)': \[\d+, \d+\]", js[js.index('const positions = {'):js.index('const colors = {')]))
    assert EXPECTED_AGENT_IDS.issubset(found)
    assert 'data-agent-id="${node.id}"' in js
    assert "state.selectedAgentId = agentId" in js
    assert "renderHudFocusPanel()" in js


def test_main_board_task_and_runtime_panels_use_existing_local_apis() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")
    assert 'id="hud-command-form"' in html
    assert 'id="hud-direct-form"' in html
    assert 'id="hud-mission-progress-bar"' in html
    assert 'id="recent-activity"' in html
    assert 'id="market-overview"' in html
    assert "api('/api/overview')" in js
    assert "api('/api/tasks'" in js
    assert "document.body.classList.add('mission-running')" in js
    assert "data-focus-tab" in html


def test_hud_layout_has_animated_runtime_states_and_responsive_rules() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "@keyframes mk-rotate" in css
    assert "@keyframes mk-link-flow" in css
    assert ".mission-running .mk-reactor" in css
    assert "@media(max-width:1050px)" in css
    assert "@media(prefers-reduced-motion:reduce)" in css


def test_collaboration_paths_are_derived_from_persisted_parent_tasks() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    assert re.search(r"filter\(item => item\.parent_task_id && taskById\.has\(item\.parent_task_id\)\)", js)
    assert 'mk-handoff-link' in js and 'is-live-handoff' in js
    assert "RECORDED HANDOFFS" in js
    assert ".mk-handoff-link" in css
    assert "is-collaborating" in js


def test_running_mission_uses_indeterminate_progress_not_fake_percentage() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "bar.classList.toggle('indeterminate', running)" in js
    assert "EXECUTING · RUNTIME TASK STARTED" in js
    assert "QUEUED · WAITING FOR RUNTIME" in js
    assert "running ? '62%'" not in js
    assert ".mk-mission-progress span.indeterminate" in css


def test_flattened_reference_assets_are_not_loaded_by_the_main_board() -> None:
    # When installing over an older release, ZIP extraction cannot remove obsolete
    # files already on disk. The invariant is that current HTML/CSS/JS never loads them.
    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")
    assert "jarvis-main-hud.png" not in html + css + js
    assert "jarvis-hud-reference.png" not in html + css + js
    assert "ironman-core.png" not in html + css + js


def test_live_activity_shows_action_trace_and_labels_audit_only_context() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    # Phase 16 shows individual action/message spans instead of collapsing every
    # runtime event into one lifecycle row per task.
    assert "OBSERVED ACTION SPAN" in js
    assert "OBSERVED AGENT MESSAGE" in js
    assert "let context = result?.summary || ''" in js
    assert "resultById.get(item.task_id)" in js
    assert "PERSISTED AUDIT · METADATA ONLY" in js
    assert "No additional detail is exposed for this event." in js
    assert ".mk-activity-entry.is-active-event" in css
