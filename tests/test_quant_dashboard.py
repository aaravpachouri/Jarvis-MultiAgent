from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import dashboard_server
from agents.trading_agent.quant_dashboard import read_quant_dashboard_data


def test_quant_dashboard_merges_historical_charts_candidates_and_live_snapshot(tmp_path: Path):
    (tmp_path / "market_brief_latest.json").write_text(json.dumps({
        "report_type": "daily_india_market_brief",
        "generated_at_utc": "2026-10-21T09:00:00+00:00",
        "latest_session": "2026-10-21",
        "market_data_source": "Yahoo Finance chart endpoint (historical/delayed fallback)",
        "market_status": "DELAYED/HISTORICAL FALLBACK",
        "indexes": {"Nifty 50": {"close": 25000, "change_1d_pct": 0.5, "session": "2026-10-21"}},
        "stocks": [{"ticker": "ABC.NS", "name": "ABC Ltd", "close": 120, "change_1d_pct": 1.2, "change_5d_pct": 2.3, "change_20d_pct": 4.1, "rsi14": 60}],
        "research_candidates": [{"ticker": "ABC.NS", "name": "ABC Ltd", "close": 120, "score": 4.2, "entry_trigger": "conditional trigger", "reasons_to_avoid": "event risk unverified"}],
        "index_chart_series": {"Nifty 50": [{"session": "2026-10-20", "close": 24900}, {"session": "2026-10-21", "close": 25000}]},
        "chart_series": {"ABC.NS": [{"session": "2026-10-20", "close": 118}, {"session": "2026-10-21", "close": 120}]},
        "advances": 12, "declines": 9,
        "disclaimer": "Research only; no orders placed.",
    }), encoding="utf-8")
    (tmp_path / "market_live_snapshot_latest.json").write_text(json.dumps({
        "report_type": "nse_mcp_live_market_snapshot",
        "provider": "NSE Market Data MCP",
        "data_status": "DATA_RETURNED",
        "retrieved_at_utc": "2026-10-21T09:01:00+00:00",
        "tools_discovered": 15,
        "selected_tools": {"index": "get_live_market_data_by_index"},
        "results": {
            "index": {
                "content": [{"type": "text", "text": json.dumps({"symbol": "NIFTY 50 INDEX", "lastPrice": 25010, "changePercent": 0.7, "timestamp": "2026-10-21T09:00:30+00:00"})}]
            }
        },
        "tool_errors": {},
        "freshness_warning": "Retrieval time is not quote time.",
        "disclaimer": "Informational only.",
    }), encoding="utf-8")

    data = read_quant_dashboard_data(tmp_path)
    assert data["available"] is True
    assert data["provider"].startswith("Yahoo Finance")
    assert data["indexes"][0]["name"] == "Nifty 50"
    assert data["candidates"][0]["ticker"] == "ABC.NS"
    assert len(data["charts"]["Nifty 50"]) == 2
    assert data["live_snapshot"]["available"] is True
    assert data["live_snapshot"]["status"] == "DATA_RETURNED"
    assert data["live_snapshot"]["quotes"][0]["symbol"] == "NIFTY 50 INDEX"
    assert data["live_snapshot"]["quotes"][0]["price"] == 25010
    # Quote's source timestamp is carried through; retrieval time is not substituted.
    assert data["live_snapshot"]["quotes"][0]["source_timestamp"] == "2026-10-21T09:00:30+00:00"


def test_quant_dashboard_never_invents_quotes_or_chart_points(tmp_path: Path):
    (tmp_path / "market_brief_latest.json").write_text(json.dumps({
        "report_type": "daily_india_market_brief", "indexes": {}, "stocks": [],
        "chart_series": {"EMPTY.NS": [{"session": "2026-10-21", "close": None}]},
    }), encoding="utf-8")
    (tmp_path / "market_live_snapshot_latest.json").write_text(json.dumps({
        "report_type": "nse_mcp_live_market_snapshot", "provider": "NSE Market Data MCP",
        "data_status": "DATA_RETURNED", "results": {"quotes": "unknown format"},
    }), encoding="utf-8")
    data = read_quant_dashboard_data(tmp_path)
    assert data["live_snapshot"]["available"] is True
    assert data["live_snapshot"]["quotes"] == []
    assert data["live_snapshot"]["label"] == "NO LIVE QUOTES PARSED"
    assert "EMPTY.NS" not in data["charts"]
    assert data["candidates"] == []


def test_quant_routes_serve_dedicated_page_assets_and_payload(monkeypatch):
    class FakeApp:
        def quant_dashboard(self):
            return {"ok": True, "available": False, "indexes": [], "stocks": [], "candidates": [], "charts": {}, "live_snapshot": {"available": False}}
        def quant_refresh_status(self, job_id):
            return {"job_id": job_id, "status": "running"} if job_id == "q-test" else None
        def start_quant_refresh(self):
            return {"job_id": "q-test", "status": "queued", "stages": []}

    monkeypatch.setattr(dashboard_server, "APP", FakeApp())
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for route, content_type in (("/quant", "text/html"), ("/assets/quant.css", "text/css"), ("/assets/quant.js", "text/javascript")):
            with urlopen(base + route, timeout=3) as response:
                assert response.status == 200
                assert content_type in response.headers.get("Content-Type", "")
                body = response.read()
                assert body
        with urlopen(base + "/api/quant/dashboard", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True and payload["available"] is False
        with urlopen(base + "/api/quant/refresh/q-test", timeout=3) as response:
            assert json.loads(response.read().decode("utf-8"))["job"]["status"] == "running"
        request = Request(base + "/api/quant/refresh", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=3) as response:
            result = json.loads(response.read().decode("utf-8"))
        assert response.status == 202 and result["job_id"] == "q-test"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
