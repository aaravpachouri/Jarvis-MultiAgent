from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
import requests

import dashboard_server
from agents.trading_agent.stock_lookup import (
    CHART_BASE_URL,
    SEARCH_URL,
    StockLookupError,
    YahooStockLookup,
)


class FakeResponse:
    def __init__(self, payload, status: int = 200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"status {self.status}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, *, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if not self.responses:
            raise AssertionError("Unexpected provider call")
        return self.responses.pop(0)


def test_search_returns_real_provider_symbol_candidates_and_caches():
    session = FakeSession([FakeResponse({"quotes": [
        {"symbol": "RELIANCE.NS", "shortname": "Reliance Industries", "longname": "Reliance Industries Limited", "exchDisp": "NSE", "quoteType": "EQUITY", "score": 90},
        {"symbol": "RELIANCE.BO", "shortname": "Reliance Industries", "exchDisp": "BSE", "quoteType": "EQUITY", "score": 70},
        {"symbol": "RELIANCE.NS", "shortname": "Duplicate", "quoteType": "EQUITY"},
        {"symbol": "REL-FUND", "shortname": "Fund", "quoteType": "MUTUALFUND"},
        {"symbol": "BAD/URL", "shortname": "Bad", "quoteType": "EQUITY"},
    ]})])
    lookup = YahooStockLookup(session=session)
    result = lookup.search("Reliance", region="IN")
    assert result["count"] == 2
    assert result["results"][0]["symbol"] == "RELIANCE.NS"
    assert result["results"][0]["name"] == "Reliance Industries Limited"
    assert result["results"][1]["symbol"] == "RELIANCE.BO"
    assert session.calls[0][0] == SEARCH_URL
    second = lookup.search("Reliance", region="IN")
    assert second["cached"] is True
    assert len(session.calls) == 1


def test_search_rejects_short_or_unsupported_query_without_network():
    session = FakeSession([])
    lookup = YahooStockLookup(session=session)
    with pytest.raises(ValueError):
        lookup.search("A")
    with pytest.raises(ValueError):
        lookup.search("Reliance", region="../../")
    assert not session.calls


def test_quote_returns_price_change_timestamp_and_real_candles():
    epoch_1 = 1792573200
    epoch_2 = epoch_1 + 86400
    session = FakeSession([FakeResponse({"chart": {"error": None, "result": [{
        "meta": {
            "symbol": "RELIANCE.NS", "longName": "Reliance Industries Limited",
            "fullExchangeName": "NSE", "currency": "INR", "instrumentType": "EQUITY",
            "regularMarketPrice": 1512.5, "regularMarketTime": epoch_2,
            "chartPreviousClose": 1500.0,
        },
        "timestamp": [epoch_1, epoch_2],
        "indicators": {"quote": [{
            "open": [1480, 1500], "high": [1510, 1520], "low": [1470, 1490],
            "close": [1495, 1512.5], "volume": [1000, 2500],
        }]},
    }]}})])
    lookup = YahooStockLookup(session=session)
    result = lookup.quote("RELIANCE.NS", chart_range="1mo")
    assert result["symbol"] == "RELIANCE.NS"
    assert result["price"] == 1512.5
    assert result["previous_close"] == 1500.0
    assert result["change"] == 12.5
    assert result["change_pct"] == pytest.approx(12.5 / 1500 * 100)
    assert result["source_timestamp_utc"] == datetime.fromtimestamp(epoch_2, timezone.utc).isoformat(timespec="seconds")
    assert result["candles"][-1]["close"] == 1512.5
    assert result["candles"][-1]["volume"] == 2500
    assert result["provider"] == "Yahoo Finance chart endpoint"
    assert "real-time status" in result["data_mode"] and "not guaranteed" in result["data_mode"]
    assert session.calls[0][0] == f"{CHART_BASE_URL}/RELIANCE.NS"
    assert session.calls[0][1]["interval"] == "1d"


def test_quote_rejects_untrusted_symbols_and_invalid_ranges():
    session = FakeSession([])
    lookup = YahooStockLookup(session=session)
    with pytest.raises(ValueError):
        lookup.quote("../../settings")
    with pytest.raises(ValueError):
        lookup.quote("TCS.NS", chart_range="10y")
    assert not session.calls


def test_quote_missing_provider_data_is_not_fabricated():
    session = FakeSession([FakeResponse({"chart": {"result": None, "error": {"description": "Not found"}}})])
    lookup = YahooStockLookup(session=session)
    with pytest.raises(StockLookupError, match="No Yahoo Finance price history"):
        lookup.quote("UNKNOWN.NS")


def test_stock_lookup_http_routes_return_provider_payload_and_validation(monkeypatch):
    class FakeLookup:
        def search(self, query, *, region="IN", limit=12):
            if len(query) < 2:
                raise ValueError("Enter at least two letters.")
            return {"ok": True, "query": query, "region": region, "results": [{"symbol": "TCS.NS", "name": "Tata Consultancy Services"}]}

        def quote(self, symbol, *, chart_range="3mo"):
            if symbol != "TCS.NS":
                raise ValueError("Enter a valid ticker.")
            return {"ok": True, "symbol": symbol, "price": 3210.5, "currency": "INR", "candles": [], "chart_range": chart_range}

    class FakeApp:
        stock_lookup = FakeLookup()

    monkeypatch.setattr(dashboard_server, "APP", FakeApp())
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/api/quant/stock-search?q=TCS&region=IN", timeout=3) as response:
            search = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert search["results"][0]["symbol"] == "TCS.NS"
        with urlopen(base + "/api/quant/stock-quote?symbol=TCS.NS&range=1mo", timeout=3) as response:
            quote = json.loads(response.read().decode("utf-8"))
            assert quote["price"] == 3210.5
            assert quote["chart_range"] == "1mo"
        try:
            urlopen(base + "/api/quant/stock-search?q=T", timeout=3)
        except HTTPError as exc:
            assert exc.code == 400
            assert "two letters" in json.loads(exc.read().decode("utf-8"))["error"]
        else:
            raise AssertionError("Short query should be rejected")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_quant_html_contains_accessible_company_lookup_and_chart_controls():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "dashboard" / "quant.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "dashboard" / "assets" / "quant.js").read_text(encoding="utf-8")
    css = (Path(__file__).resolve().parents[1] / "dashboard" / "assets" / "quant.css").read_text(encoding="utf-8")
    for token in ('id="q-stock-search-form"', 'id="q-stock-query"', 'id="q-stock-search-results"', 'id="q-stock-selected"', 'id="q-stock-chart"'):
        assert token in html
    assert "/api/quant/stock-search" in js
    assert "/api/quant/stock-quote" in js
    assert "source_timestamp_utc" in js
    assert "freshness_label" in js
    assert ".q-stock-results" in css
