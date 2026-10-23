from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from agents.trading_agent.agent import TradingAgent, create_handler


def _fake_chart(ticker: str, *, start: float = 100.0, daily_step: float = 1.0, name: str | None = None):
    rows = []
    today = date(2026, 10, 19)
    for index in range(25):
        rows.append({
            "session": (today - timedelta(days=24-index)).isoformat(),
            "close": start + index * daily_step,
            "volume": 1000 + index * 5,
        })
    return {
        "ticker": ticker,
        "name": name or ticker,
        "currency": "INR",
        "rows": rows,
        "source_url": f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
    }


class _FakeContext:
    class _Runtime:
        @staticmethod
        def has_handler(agent_id: str) -> bool:
            return agent_id == "search.scout"

    runtime = _Runtime()

    async def delegate(self, prompt, **kwargs):
        return SimpleNamespace(
            succeeded=True,
            output={
                "retrieved_at": "2026-10-20T02:30:00+00:00",
                "results": [{
                    "title": "Dated market news",
                    "url": "https://example.test/market-news",
                    "snippet": "An unverified search snippet for test coverage.",
                    "domain": "example.test",
                }],
            },
            error=None,
            summary="ok",
        )


def test_factory_returns_real_executable_handler():
    handler = create_handler()
    assert isinstance(handler, TradingAgent)
    assert callable(handler)


def test_manifest_activates_quant_as_read_only_handler():
    manifest = json.loads((Path(__file__).parents[1] / "agents/trading_agent/manifest.json").read_text(encoding="utf-8"))
    assert manifest["metadata"]["profile_only"] is False
    assert manifest["metadata"]["places_trades"] is False
    assert "daily market brief" in manifest["routing_keywords"]
    assert "stocks to watch" in manifest["routing_keywords"]


def test_metrics_use_completed_daily_closes_and_do_not_invent_longer_history():
    data = _fake_chart("TEST.NS")
    metrics = TradingAgent._metrics(data)
    assert metrics is not None
    assert metrics["session"] == "2026-10-19"
    assert metrics["change_1d_pct"] > 0
    assert metrics["change_5d_pct"] > 0
    assert metrics["change_20d_pct"] > 0
    assert metrics["volume_vs_recent_median"] is not None
    assert TradingAgent._metrics({**data, "rows": data["rows"][:3]})["change_20d_pct"] is None


def test_momentum_screen_only_returns_positive_five_and_twenty_session_candidates():
    stocks = [
        {"ticker": "A.NS", "name": "A", "close": 10, "change_1d_pct": 1, "change_5d_pct": 2, "change_20d_pct": 4, "relative_5d_vs_nifty_pct": 1},
        {"ticker": "B.NS", "name": "B", "close": 10, "change_1d_pct": 1, "change_5d_pct": -2, "change_20d_pct": 4, "relative_5d_vs_nifty_pct": -3},
        {"ticker": "C.NS", "name": "C", "close": 10, "change_1d_pct": 1, "change_5d_pct": 2, "change_20d_pct": -1, "relative_5d_vs_nifty_pct": 1},
    ]
    candidates = TradingAgent._select_research_candidates(stocks)
    assert [item["ticker"] for item in candidates] == ["A.NS"]
    assert "risk_checks" in candidates[0]


def test_daily_brief_reports_scope_news_and_research_not_buy_orders(monkeypatch):
    agent = TradingAgent(universe={"TEST.NS": "Test Company"}, max_concurrency=2)

    def fake_fetch(ticker):
        if ticker == "^NSEI":
            return _fake_chart(ticker, start=22000, daily_step=10, name="Nifty 50")
        if ticker == "^BSESN":
            return _fake_chart(ticker, start=73000, daily_step=20, name="BSE Sensex")
        return _fake_chart(ticker, start=100, daily_step=2, name="Test Company")

    monkeypatch.setattr(agent, "_fetch_chart_data", fake_fetch)
    result = asyncio.run(agent(SimpleNamespace(prompt="Daily Indian stock market brief"), _FakeContext()))
    assert result["report_type"] == "daily_india_market_brief"
    assert result["stocks_screened"] == 1
    assert result["latest_session"] == "2026-10-19"
    assert "monitored universe" in result["summary"].lower()
    assert "not buy instructions" in result["summary"].lower()
    assert "example.test/market-news" in result["summary"]
    assert result["research_candidates"][0]["ticker"] == "TEST.NS"
    assert "No trades were placed" in result["summary"]


def test_data_failures_are_explicit_and_do_not_generate_fake_movers(monkeypatch):
    agent = TradingAgent(universe={"BROKEN.NS": "Broken Co"})

    def fail_fetch(ticker):
        raise RuntimeError("offline")

    monkeypatch.setattr(agent, "_fetch_chart_data", fail_fetch)
    result = asyncio.run(agent(SimpleNamespace(prompt="daily market brief"), _FakeContext()))
    assert result["stocks_screened"] == 0
    assert "No stock prices were retrieved" in result["summary"]
    assert "BROKEN.NS" in result["data_errors"]
    assert result["research_candidates"] == []


def test_specific_ticker_request_uses_market_history(monkeypatch):
    agent = TradingAgent(universe={"INFY.NS": "Infosys"})
    monkeypatch.setattr(agent, "_fetch_chart_data", lambda ticker: _fake_chart(ticker, name="Infosys"))
    result = asyncio.run(agent(SimpleNamespace(prompt="Analyze stock ticker INFY"), _FakeContext()))
    assert result["report_type"] == "single_stock_review"
    assert result["ticker"] == "INFY.NS"
    assert "not personalized financial advice" in result["summary"]


def test_no_news_handler_does_not_claim_news_was_verified(monkeypatch):
    agent = TradingAgent(universe={"TEST.NS": "Test Company"})
    monkeypatch.setattr(agent, "_fetch_chart_data", lambda ticker: _fake_chart(ticker))
    context = SimpleNamespace(runtime=SimpleNamespace(has_handler=lambda agent_id: False))
    result = asyncio.run(agent(SimpleNamespace(prompt="market brief"), context))
    assert result["news"]["status"] == "unavailable"
    assert "not independently verified" in result["summary"] or "Recent news unavailable" in result["summary"]
