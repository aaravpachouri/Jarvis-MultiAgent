from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.trading_agent.agent import TradingAgent
from agents.trading_agent.dashboard import persist_report_artifacts
from agents.trading_agent.providers import FyersMarketDataProvider, ProviderError


def _provider(tmp_path: Path) -> FyersMarketDataProvider:
    return FyersMarketDataProvider(client_id="test-client", access_token="test-token", cache_dir=tmp_path)


def test_fyers_provider_requires_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("FYERS_CLIENT_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    with pytest.raises(ProviderError, match="FYERS_CLIENT_ID/FYERS_ACCESS_TOKEN"):
        FyersMarketDataProvider(cache_dir=tmp_path)


def test_fyers_quote_batches_respect_limit_and_preserve_errors(monkeypatch, tmp_path):
    provider = _provider(tmp_path)
    calls = []

    def fake_get_json(endpoint, params):
        calls.append((endpoint, params["symbols"].split(",")))
        return {"code": 200, "s": "ok", "d": [
            {"n": symbol, "s": "ok", "v": {"lp": 100, "chp": 1.2, "tt": "1790000000"}}
            for symbol in params["symbols"].split(",") if symbol != "NSE:BAD-EQ"
        ]}

    monkeypatch.setattr(provider, "_get_json", fake_get_json)
    monkeypatch.setattr("agents.trading_agent.providers.time.sleep", lambda _seconds: None)
    symbols = [f"NSE:S{i}-EQ" for i in range(52)] + ["NSE:BAD-EQ"]
    quotes, errors = provider.fetch_quotes(symbols)
    assert len(calls) == 2
    assert all(len(batch) <= 50 for _, batch in calls)
    assert len(quotes) == 52
    assert "NSE:BAD-EQ" in errors


def test_fyers_history_maps_candle_fields(monkeypatch, tmp_path):
    provider = _provider(tmp_path)
    stamp = int(datetime(2026, 10, 19, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(provider, "_get_json", lambda endpoint, params: {
        "code": 200, "s": "ok", "candles": [[stamp, 100, 105, 99, 104, 12000]]
    })
    data = provider.fetch_history("NSE:TEST-EQ")
    assert data["rows"][0]["open"] == 100
    assert data["rows"][0]["high"] == 105
    assert data["rows"][0]["close"] == 104
    assert data["rows"][0]["volume"] == 12000


def test_fyers_symbol_master_parses_cash_equity_tickers(monkeypatch, tmp_path):
    provider = _provider(tmp_path)
    rows = []
    for ticker, name in [("NSE:RELIANCE-EQ", "RELIANCE INDUSTRIES"), ("NSE:INFY-EQ", "INFOSYS"), ("NSE:NIFTY50-INDEX", "NIFTY 50")]:
        row = ["token", name, "0", "1", "0.05", "isin", "09:15-15:30", "2026-10-14", "", ticker, "10", "10", "1"]
        rows.append(",".join(row))
    class FakeResponse:
        content = ("\n".join(rows) + "\n#" + "x" * 1200).encode()
        def raise_for_status(self):
            pass
    monkeypatch.setattr("agents.trading_agent.providers.requests.get", lambda *a, **k: FakeResponse())
    universe = provider.load_nse_equity_universe()
    assert "RELIANCE.NS" in universe
    assert "INFY.NS" in universe
    assert "^NSEI" not in universe


def test_metrics_calculate_rsi_moving_averages_macd_and_atr():
    rows = []
    for index in range(70):
        close = 100 + index * 0.8
        rows.append({"session": f"2026-01-{(index % 28) + 1:02d}", "open": close - 0.3,
                     "high": close + 1, "low": close - 1, "close": close, "volume": 1000 + index * 10})
    metrics = TradingAgent._metrics({"ticker": "TEST.NS", "rows": rows, "currency": "INR"})
    assert metrics["sma20"] is not None
    assert metrics["sma50"] is not None
    assert metrics["rsi14"] == 100.0
    assert metrics["atr14_pct"] > 0
    assert metrics["macd_histogram"] is not None


def test_missing_fyers_credentials_fail_closed_when_fyers_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_MARKET_DATA_PROVIDER", "fyers")
    monkeypatch.delenv("FYERS_CLIENT_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_MARKET_REPORTS_DIR", str(tmp_path / "reports"))
    agent = TradingAgent()
    result = asyncio.run(agent(SimpleNamespace(prompt="Live market briefing"), SimpleNamespace(runtime=None)))
    assert result["report_type"] == "market_brief_error"
    assert "No live prices or recommendations were fabricated" in result["summary"]


def test_dashboard_artifacts_are_persisted_with_svg_and_csv(tmp_path):
    report = {
        "generated_at_utc": "2026-10-20T00:00:00+00:00", "market_status": "TEST STATUS",
        "market_data_source": "TEST SOURCE", "freshness_note": "TEST FRESHNESS",
        "market_pulse": "Nifty +1%", "advances": 2, "declines": 1, "quote_count": 3,
        "index_chart_series": {"Nifty 50": [{"session": "2026-10-19", "close": 100}, {"session": "2026-10-20", "close": 102}]},
        "chart_series": {"TEST.NS": [{"session": "2026-10-19", "close": 10}, {"session": "2026-10-20", "close": 11}]},
        "research_candidates": [{"ticker": "TEST.NS", "close": 11, "score": 3, "entry_trigger": "above 11", "invalidation": 10, "target_2r": 13}],
    }
    paths = persist_report_artifacts(report, tmp_path)
    assert Path(paths["json"]).is_file()
    assert Path(paths["html_dashboard"]).is_file()
    assert Path(paths["candidates_csv"]).is_file()
    dashboard = Path(paths["html_dashboard"]).read_text(encoding="utf-8")
    assert "<svg" in dashboard
    assert "TEST STATUS" in dashboard
    loaded = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert loaded["market_status"] == "TEST STATUS"


def test_fyers_live_workflow_uses_broad_quotes_and_writes_dashboard(monkeypatch, tmp_path):
    import agents.trading_agent.agent as agent_module
    from datetime import date, timedelta

    now_epoch = int(datetime.now(timezone.utc).timestamp())
    symbols = {
        "TEST.NS": {"name": "Test Company", "symbol": "NSE:TEST-EQ"},
        "ALPHA.NS": {"name": "Alpha Company", "symbol": "NSE:ALPHA-EQ"},
    }

    def quote(symbol, price, change):
        return {"symbol": symbol, "lp": price, "chp": change, "tt": str(now_epoch), "volume": 2_000_000,
                "open_price": price * .99, "high_price": price * 1.02, "low_price": price * .98,
                "prev_close_price": price / (1 + change / 100), "bid": price - .01, "ask": price + .01,
                "description": symbol}

    quote_map = {
        "NSE:TEST-EQ": quote("NSE:TEST-EQ", 150, 2.0),
        "NSE:ALPHA-EQ": quote("NSE:ALPHA-EQ", 250, 1.0),
        "NSE:NIFTY50-INDEX": quote("NSE:NIFTY50-INDEX", 23000, 0.8),
        "BSE:SENSEX-INDEX": quote("BSE:SENSEX-INDEX", 74000, 0.6),
    }

    class FakeProvider:
        def __init__(self, **kwargs):
            pass
        @staticmethod
        def is_configured():
            return True
        def load_nse_equity_universe(self, *, max_symbols=2500):
            return symbols
        def fetch_quotes(self, requested):
            return quote_map, {}
        def fetch_chart_data(self, ticker, symbol, item=None, **kwargs):
            rows = []
            for index in range(70):
                close = 100 + index * (1.0 if ticker != "^NSEI" else 10.0)
                rows.append({"session": (date.today() - timedelta(days=69-index)).isoformat(), "open": close-.2,
                             "high": close+1, "low": close-1, "close": close, "volume": 100000 + index*1000})
            live = item or quote_map[symbol]
            rows[-1]["close"] = float(live.get("lp", rows[-1]["close"]))
            return {"ticker": ticker, "provider_symbol": symbol, "name": ticker, "currency": "INR", "rows": rows,
                    "source_url": "https://provider.test/history", "provider": "FYERS", "quote": live,
                    "quote_timestamp_utc": datetime.fromtimestamp(now_epoch, timezone.utc).isoformat(),
                    "quote_timestamp_epoch": now_epoch, "data_mode": "live_quote_plus_historical_candles"}
        def fetch_history(self, symbol, *, resolution="D", days=120):
            rows = []
            for index in range(30):
                close = 150 + index * .1
                rows.append({"timestamp_utc": datetime.fromtimestamp(now_epoch-index*300, timezone.utc).isoformat(),
                             "session": date.today().isoformat(), "epoch": now_epoch-index*300,
                             "open": close-.1, "high": close+.2, "low": close-.2, "close": close, "volume": 10000})
            return {"rows": rows}

    monkeypatch.setattr(agent_module, "FyersMarketDataProvider", FakeProvider)
    monkeypatch.setenv("JARVIS_MARKET_DATA_PROVIDER", "fyers")
    monkeypatch.setenv("JARVIS_MARKET_REPORTS_DIR", str(tmp_path / "reports"))
    agent = TradingAgent(universe={"TEST.NS": "Test Company"}, max_concurrency=2)
    context = SimpleNamespace(runtime=SimpleNamespace(has_handler=lambda _agent: False))
    result = asyncio.run(agent(SimpleNamespace(prompt="Live market briefing"), context))
    assert result["report_type"] == "live_india_market_intelligence"
    assert result["quote_count"] == 2
    assert result["market_data_source"] == "FYERS API quotes and historical candles"
    assert "Market breadth" in result["summary"]
    assert result["artifacts"]["html_dashboard"].endswith("market_dashboard_latest.html")
    assert Path(result["artifacts"]["html_dashboard"]).is_file()
