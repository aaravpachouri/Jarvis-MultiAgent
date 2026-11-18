from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from agents.trading_agent.agent import TradingAgent
from agents.trading_agent.nse_mcp_provider import NSEMCPError
from agents.trading_agent.quant_dashboard import read_quant_dashboard_data


class FakeYahooLookup:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def quote(self, symbol: str, *, chart_range: str = "3mo"):
        self.calls.append((symbol, chart_range))
        if self.fail:
            raise RuntimeError("synthetic provider failure for test")
        prices = {"^NSEI": 25123.45, "^BSESN": 82123.67}
        names = {"^NSEI": "Nifty 50", "^BSESN": "BSE Sensex"}
        return {
            "ok": True,
            "symbol": symbol,
            "name": names[symbol],
            "price": prices[symbol],
            "change_pct": 0.42,
            "source_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": "Yahoo Finance chart endpoint",
            "data_mode": "Latest available provider price; real-time status not guaranteed.",
            "currency": "INR",
            "source_url": f"https://finance.yahoo.com/quote/{symbol}/",
            "cached": False,
        }


def test_nse_mcp_failure_recovers_with_timestamped_yahoo_index_snapshot(tmp_path: Path, monkeypatch):
    lookup = FakeYahooLookup()
    monkeypatch.setenv("JARVIS_MARKET_REPORTS_DIR", str(tmp_path))
    agent = TradingAgent(stock_lookup=lookup)

    def unavailable(self):
        raise NSEMCPError("NSE MCP returned HTTP 404 during initialize at official endpoint")

    monkeypatch.setattr("agents.trading_agent.agent.NSEMCPProvider.get_live_market_brief", unavailable)
    result = asyncio.run(agent._daily_market_brief_nse_mcp())

    assert result["report_type"] == "yahoo_fallback_market_snapshot"
    assert result["data_status"] == "DELAYED_FALLBACK_DATA_RETURNED"
    assert result["data_class"] == "delayed_fallback"
    assert "not an official NSE feed" in result["provider"]
    assert "HTTP 404" in result["tool_errors"]["nse_mcp"]
    assert len(result["results"]["indexes"]) == 2
    assert all(item["timestamp"] for item in result["results"]["indexes"])
    assert set(lookup.calls) == {("^NSEI", "1d"), ("^BSESN", "1d")}
    stored = json.loads((tmp_path / "market_live_snapshot_latest.json").read_text(encoding="utf-8"))
    assert stored["data_class"] == "delayed_fallback"
    (tmp_path / "market_brief_latest.json").write_text(json.dumps({
        "report_type": "daily_india_market_brief",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_session": datetime.now(timezone.utc).date().isoformat(),
        "indexes": {
            "Nifty 50": {"close": 24900, "change_1d_pct": -0.1, "session": "2026-10-22"},
            "BSE Sensex": {"close": 81000, "change_1d_pct": -0.2, "session": "2026-10-22"},
        },
        "stocks": [], "research_candidates": [], "chart_series": {}, "index_chart_series": {},
        "market_data_source": "Yahoo Finance delayed historical charts",
        "market_status": "DELAYED/HISTORICAL FALLBACK",
    }), encoding="utf-8")
    dashboard = read_quant_dashboard_data(tmp_path)
    assert dashboard["live_snapshot"]["available"] is True
    assert dashboard["live_snapshot"]["data_class"] == "delayed_fallback"
    assert dashboard["live_snapshot"]["label"] == "YAHOO FALLBACK · PROVIDER DELAY NOT GUARANTEED"
    assert len(dashboard["live_snapshot"]["quotes"]) == 2
    assert dashboard["live_snapshot"]["quotes"][0]["source_timestamp"]
    assert "Yahoo Finance" in dashboard["live_snapshot"]["quotes"][0]["source"]
    nifty_tile = next(item for item in dashboard["indexes"] if item["name"] == "Nifty 50")
    assert nifty_tile["value"] == 25123.45
    assert nifty_tile["source_timestamp_utc"]


def test_nse_and_yahoo_failure_does_not_fabricate_prices(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JARVIS_MARKET_REPORTS_DIR", str(tmp_path))
    agent = TradingAgent(stock_lookup=FakeYahooLookup(fail=True))

    monkeypatch.setattr(
        "agents.trading_agent.agent.NSEMCPProvider.get_live_market_brief",
        lambda self: (_ for _ in ()).throw(NSEMCPError("HTTP 404")),
    )
    result = asyncio.run(agent._daily_market_brief_nse_mcp())
    assert result["report_type"] == "market_brief_error"
    assert "No live prices were fabricated" in result["error"]
