from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard" / "index.html"
APP_JS = ROOT / "dashboard" / "assets" / "app.js"
FORMATTER_JS = ROOT / "dashboard" / "assets" / "result-format.js"

MARKET_PULSE_OUTPUT = """MARKET PULSE — INDIA — SUCCESS Root task: e7bdc929-7ced-481c-bd74-c43cffed58c5 Quant task: 7c40c753-18cf-4754-8731-509045fa26c6 · status=succeeded Company searches attempted: 8 Usable company quotes: 8 Usable index quotes: 2/2 Research candidates: 0 Report: C:/Users/Hp/Downloads/JARVIS_Agent_Network_Phase3_Scribe_Athena_Planner/data/reports/market_pulse_india.md ## Stage records - stage=quant_market_data; agent_id=trading.quant; capability=trading.market_analysis; task_id=7c40c753-18cf-4754-8731-509045fa26c6; status=succeeded - stage=evidence_review; agent_id=research.athena; capability=research.synthesize; task_id=d3bc6d55-46a1-43e2-b523-0efb07a37f7a; status=succeeded - stage=scribe_report_draft; agent_id=documentation.scribe; capability=writing.report; task_id=f80ee5fc-98d5-4703-be39-0c31ef7e579e; status=succeeded ## Limitations Use exchange-issued market hours/holiday data to confirm session status. No such status endpoint was used here. The market feed is not guaranteed real-time. An empty shortlist is valid when no candidates meet the screen. No brokerage account was accessed and no orders were placed."""


def test_dashboard_loads_result_formatter_before_main_app():
    html = HTML.read_text(encoding="utf-8")
    formatter_tag = '<script src="/assets/result-format.js" defer></script>'
    app_tag = '<script src="/assets/app.js" defer></script>'
    assert formatter_tag in html
    assert html.index(formatter_tag) < html.index(app_tag)


def test_mission_result_surfaces_a_formatted_answer_and_raw_details():
    app = APP_JS.read_text(encoding="utf-8")
    assert "renderTaskResult(latest.output)" in app
    assert "renderTaskResult(output, { compact: true })" in app
    assert "renderTaskResult(r.output || r.error || r.summary || 'No output')" in app
    assert "data-open-result-report" in app
    assert "openResultReport" in app
    assert "if (state.currentView === 'overview' && state.lastHudTask?.output) openHudPanel('mission')" in app


def _run_node_json(expression: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; browser JavaScript tests are unavailable")
    script = f"""
      global.window = {{}};
      require({json.dumps(str(FORMATTER_JS))});
      const output = {json.dumps(MARKET_PULSE_OUTPUT)};
      const value = {expression};
      process.stdout.write(JSON.stringify(value));
    """
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8", timeout=10)
    return json.loads(result.stdout)


def test_market_pulse_result_is_parsed_into_readable_metrics_and_stages():
    parsed = _run_node_json("window.JARVISResultFormatter.parseMarketPulse(output)")
    assert parsed["kind"] == "market-pulse"
    assert parsed["status"] == "success"
    assert parsed["searched"] == 8
    assert parsed["usable"] == 8
    assert parsed["indexesUsable"] == 2 and parsed["indexesTotal"] == 2
    assert parsed["candidates"] == 0
    assert parsed["reportName"] == "market_pulse_india.md"
    assert len(parsed["stages"]) == 3
    assert parsed["stages"][0]["agent"] == "trading.quant"


def test_market_pulse_renderer_explains_empty_shortlist_and_links_full_report():
    html = _run_node_json("window.JARVISResultFormatter.render(output)")
    assert "COMPANY SEARCHES" in html
    assert "USABLE QUOTES" in html
    assert "2 / 2" in html
    assert "No qualifying stocks in this run" in html
    assert "Verify market context before relying on the result" in html
    assert "Exchange session open/closed status was not independently verified" in html
    assert "data-open-result-report=\"market_pulse_india.md\"" in html
    assert "Full runtime output" in html
    assert "exchange-issued market hours" in html


def test_generic_result_output_is_escaped_and_formatted_with_raw_fallback():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; browser JavaScript tests are unavailable")
    malicious = "# Result\n<script>alert(1)</script>\n- Check one\nStatus: completed"
    script = f"""
      global.window = {{}};
      require({json.dumps(str(FORMATTER_JS))});
      const html = window.JARVISResultFormatter.render({json.dumps(malicious)});
      process.stdout.write(html);
    """
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8", timeout=10)
    html = result.stdout
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "<h4>Result</h4>" in html
    assert "Check one" in html
    assert "Full runtime output" in html


def test_dashboard_server_exposes_formatter_asset():
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen

    import dashboard_server

    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/assets/result-format.js", timeout=3) as response:
            body = response.read().decode("utf-8")
            assert response.status == 200
            assert "JARVISResultFormatter" in body
            assert "javascript" in response.headers.get("Content-Type", "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_markdown_report_renderer_formats_headings_tables_and_escapes_html():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; browser JavaScript tests are unavailable")
    markdown = "# Market report\n\n## Company quotes\n\n| Company | Price |\n| --- | ---: |\n| Reliance | ₹1,234.50 |\n\n- Source timestamp verified\n\n<script>alert(1)</script>"
    script = f"""
      global.window = {{}};
      require({json.dumps(str(FORMATTER_JS))});
      process.stdout.write(window.JARVISResultFormatter.renderMarkdownDocument({json.dumps(markdown)}));
    """
    result = subprocess.run([node, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8", timeout=10)
    html = result.stdout
    assert "<h2>Market report</h2>" in html
    assert "<h3>Company quotes</h3>" in html
    assert "<table class=\"result-table\">" in html
    assert "Reliance" in html and "₹1,234.50" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_markdown_report_viewer_uses_the_safe_document_renderer():
    html = HTML.read_text(encoding="utf-8")
    app = APP_JS.read_text(encoding="utf-8")
    css = (ROOT / "dashboard" / "assets" / "styles.css").read_text(encoding="utf-8")
    assert '<div id="viewer-content" class="viewer-content">' in html
    assert "window.JARVISResultFormatter.renderMarkdownDocument(text)" in app
    assert "viewer.classList.add('is-markdown')" in app
    assert ".markdown-document" in css and ".result-table" in css


def test_mission_drawer_expands_for_readable_results():
    css = (ROOT / "dashboard" / "assets" / "styles.css").read_text(encoding="utf-8")
    assert '.hud-drawer[data-panel="mission"]{width:min(700px,53vw)' in css
    assert '.hud-drawer[data-panel="mission"] .result-kpi strong{font-size:27px}' in css
