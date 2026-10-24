from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard" / "index.html"
CSS = ROOT / "dashboard" / "assets" / "styles.css"
APP = ROOT / "dashboard" / "assets" / "app.js"


def test_mission_drawer_contains_small_native_interactive_armory_suit():
    html = HTML.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    js = APP.read_text(encoding="utf-8")
    assert 'id="hud-mini-suit"' in html
    assert 'class="hud-mini-suit"' in html
    assert "viewBox=\"0 0 100 112\"" in html
    assert 'id="hud-mini-suit-label"' in html
    assert 'aria-label="Armory profile: Mark III classic red and gold.' in html
    assert '.hud-mini-suit {\n  --mini-red:' in css
    assert 'position:absolute; z-index:3; top:4px; right:53px;' in css
    assert '.hud-drawer[data-panel="mission"] .hud-mini-suit' in css
    assert 'function cycleHudSuitProfile()' in js
    assert "if (event.target.closest('#hud-mini-suit'))" in js


def test_armory_profiles_have_distinct_real_iron_man_colorways():
    css = CSS.read_text(encoding="utf-8")
    js = APP.read_text(encoding="utf-8")
    for profile in ("mark-iii", "mark-v", "mark-xlii", "war-machine"):
        assert f'data-suit-profile="{profile}"' in css
        assert f"id: '{profile}'" in js
    assert "Classic red and gold armor" in js
    assert "Crimson and silver suitcase armor" in js
    assert "Gold and red autonomous armor" in js
    assert "Graphite and steel tactical armor" in js


def test_cinematic_mission_banner_is_driven_by_live_runtime_state():
    html = HTML.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    js = APP.read_text(encoding="utf-8")
    assert 'id="hud-mission-cinematic"' in js
    assert "missionCinematicModel(last)" in js
    assert "missionCinematicMarkup(latest)" in js
    assert "const missionEvents = last?.taskId ? events.filter" in js
    assert "mission-cinematic-banner" in css
    assert "const missionState = running ? 'running' : succeeded ? 'success' : failed ? 'failed' : last ? 'recorded' : 'standby';" in js
    for stage in ("COMMAND", "RUNTIME", "COORDINATION", "RESULT"):
        assert f"'{stage}'" in js
    assert "observedHandoffs" in js
    assert "setAttribute('aria-valuenow'" in js
    assert 'id="hud-drawer"' in html


def test_mission_sequence_does_not_mark_result_returned_during_handoff():
    js = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "const stage = !running ? (succeeded || failed ? 2 : last ? 0 : -1) : rootStarted ? 1 : 0;" in js
    assert "COORDINATING · ${observedHandoffs} OBSERVED HANDOFF" in js
    assert "const phase = running ? (handoffs > 0 ? 3 : rootStarted ? 2 : 1) : last ? 4 : 0;" in js
    assert ".mission-cinema-stage.is-active" in css
    assert ".mission-cinema-stage.is-failed" in css


def test_mini_suit_and_mission_fx_respect_reduced_motion_and_do_not_reflow_hud():
    css = CSS.read_text(encoding="utf-8")
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert ".hud-mini-suit" in css and "position:absolute" in css
    assert "@keyframes mission-cine-spin" in css
    assert "@keyframes mission-cine-rail" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
