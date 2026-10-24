from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard" / "index.html"
CSS = ROOT / "dashboard" / "assets" / "styles.css"
APP = ROOT / "dashboard" / "assets" / "app.js"
ASSETS = ROOT / "dashboard" / "assets" / "agents"

AGENTS = {
    "voice.jarvis": "jarvis", "research.athena": "athena", "desktop.atlas": "atlas",
    "coding.forge": "forge", "trading.quant": "quant", "search.scout": "scout",
    "planning.planner": "planner", "documentation.scribe": "scribe",
    "memory.notes": "memory", "deployment.launch": "launch",
}

def test_ten_distinct_local_portraits_are_bundled_and_accessible_offline():
    app = APP.read_text(encoding="utf-8")
    assert len(list(ASSETS.glob("*.svg"))) == 10
    for agent_id, slug in AGENTS.items():
        portrait = ASSETS / f"{slug}.svg"
        assert portrait.is_file(), agent_id
        content = portrait.read_text(encoding="utf-8")
        assert "<svg" in content and "<title" in content and "<desc" in content
        assert "/assets/agents/" in app
        assert f"'{agent_id}'" in app

def test_agent_dock_and_mission_composer_use_existing_runtime_submission_path():
    html = HTML.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert 'id="mk-agent-cards"' in html
    assert 'id="mk-mission-form"' in html
    assert 'id="mk-mission-prompt"' in html
    assert "runTask(prompt, target)" in app
    assert "api('/api/missions'" in app
    assert "hudStatus(requestedAgent)" in app

def test_agent_cards_and_nodes_derive_state_from_runtime_not_fake_status():
    app = APP.read_text(encoding="utf-8")
    assert "const status = hudStatus(agent);" in app
    assert "const status = hudStatus(agent);" in app
    assert "const status = hudStatus(a);" in app
    assert "const status = hudStatus(node.agent);" not in app
    assert "state.liveEvents" in app and "state.activeMissions" in app
    assert 'data-hud-agent-select="${escapeHTML(agent.agent_id)}"' in app

def test_roster_display_modes_have_real_callbacks_and_specialist_portraits():
    html = HTML.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    for tab in ("overview", "capabilities", "tasks", "personalities"):
        assert f'data-dock-tab="{tab}"' in html
    assert "agentDockTab: 'overview'" in app
    for tab in ("capabilities", "tasks", "personalities"):
        assert f"tab === '{tab}'" in app
    assert "function renderAgentDock()" in app
    assert "hud-specialist-identity" in app
    assert ".mk-agent-card.is-busy" in css
    assert "@media (prefers-reduced-motion: reduce)" in css

def test_existing_permissions_and_market_read_only_notice_remain_in_place():
    html = HTML.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert "PERMISSIONS ENFORCED" in html
    assert "READ-ONLY MARKETS" in html
    assert "if (state.taskBusy)" in app
    assert "if (target !== 'voice.jarvis'" in app
    assert "production_write" not in app  # approval policy remains server-owned, not rewritten in UI
