"""Regression tests for honest mission state and collaboration visualization."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "dashboard" / "assets" / "app.js"
CSS_PATH = ROOT / "dashboard" / "assets" / "styles.css"
SERVER_PATH = ROOT / "dashboard_server.py"


def test_dashboard_restores_latest_mission_status_on_a_fresh_page() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "function currentHudMission()" in js
    assert "state.activeMissions" in js
    assert "state.overview?.task_history?.recent_tasks" in js
    assert "LAST MISSION COMPLETE" in js
    assert "LAST MISSION FAILED" in js
    assert "prompt/output are not retained in audit metadata" in js


def test_active_mission_count_uses_runtime_missions_and_latest_task_lifecycle() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "activeTaskIds = new Set((state.activeMissions || []).map(item => item.task_id)" in js
    assert "data.active_missions" in js
    assert "activeTaskIds.size || (state.taskBusy ? 1 : 0)" in js


def test_historical_handoffs_are_muted_and_only_active_handoffs_animate() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "activeTaskIds.has(item.payload?.parent_task_id || '')" in js
    assert ".mk-handoff-link{fill:none;stroke:#5e8e9d" in css
    assert ".mk-handoff-link.is-live-handoff" in css
    historical, active = css.split(".mk-handoff-link.is-live-handoff", 1)
    assert "animation:none" in historical
    assert "animation:mk-handoff-flow 1.7s linear infinite" in active


def test_live_activity_does_not_mislabel_audit_only_records_as_live() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    assert "PERSISTED AUDIT · METADATA ONLY" in js
    assert "NO NEW LIVE EVENTS" in js
    assert "persistent audit stores lifecycle metadata, not prompt/output" in js
    assert "No additional detail is exposed for this event." in js
    assert "PERSISTED AUDIT · METADATA ONLY" in js


def test_active_mission_endpoint_remains_the_authority_for_dashboard_jobs() -> None:
    server = SERVER_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")
    assert '"active_missions": self.active_missions()' in server
    assert "const active = (state.activeMissions || []).find" in js
    assert "api(`/api/events?after=${encodeURIComponent(state.eventCursor)}`)" in js
