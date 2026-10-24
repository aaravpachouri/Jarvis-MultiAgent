"""Zero-key Yahoo Finance symbol search and historical/latest-available stock lookup.

This adapter is read-only. It uses Yahoo Finance's publicly accessible web chart/search
endpoints without API keys, caches short-lived responses, and always exposes source and
retrieval timestamps separately. Availability, delay, and use remain subject to provider
terms and can change without notice.
"""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"
_RANGE_INTERVALS = {
    "1d": ("1d", "5m"),
    "5d": ("5d", "15m"),
    "1mo": ("1mo", "1d"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "1y": ("1y", "1wk"),
}
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9^=_][A-Za-z0-9.^=_-]{0,31}$")


class StockLookupError(RuntimeError):
    """Safe user-facing lookup/provider error."""


class StockLookupRateLimit(StockLookupError):
    """The local dashboard has received too many lookup requests."""


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _utc_iso(epoch: Any) -> str | None:
    try:
        value = int(float(epoch))
        if value <= 0:
            return None
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _age_seconds(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (ValueError, OverflowError):
        return None


def _freshness_label(age: int | None) -> str:
    if age is None:
        return "TIMESTAMP UNAVAILABLE"
    if age <= 300:
        return "RECENT SOURCE TIMESTAMP · PROVIDER DELAY NOT VERIFIED"
    if age <= 24 * 60 * 60:
        return "LATEST AVAILABLE SESSION · NOT GUARANTEED REAL-TIME"
    return "OLDER DATA · CHECK SOURCE BEFORE RELYING ON IT"


class YahooStockLookup:
    """Small cached stock-search adapter used by the local Quant Desk."""

    def __init__(self, *, timeout_seconds: float = 7.0, session: Any = None) -> None:
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 12.0))
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/json"})
        self._lock = threading.Lock()
        self._cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._request_times: list[float] = []

    def _guard_rate(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._request_times = [stamp for stamp in self._request_times if now - stamp < 60]
            if len(self._request_times) >= 30:
                raise StockLookupRateLimit("Quant stock lookup limit reached; wait a minute and try again.")
            self._request_times.append(now)

    def _get_cache(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock:
            item = self._cache.get(key)
            if not item:
                return None
            expiry, value = item
            if expiry < time.monotonic():
                self._cache.pop(key, None)
                return None
            # JSON round-trip clone avoids callers mutating cache contents.
            import json
            return json.loads(json.dumps(value))

    def _put_cache(self, key: tuple[Any, ...], value: dict[str, Any], ttl: float) -> dict[str, Any]:
        import json
        clone = json.loads(json.dumps(value))
        with self._lock:
            self._cache[key] = (time.monotonic() + ttl, clone)
        return value

    def search(self, query: str, *, region: str = "IN", limit: int = 12) -> dict[str, Any]:
        q = str(query or "").strip()
        if len(q) < 2:
            raise ValueError("Enter at least two letters or a ticker symbol.")
        if len(q) > 80 or any(ord(ch) < 32 for ch in q):
            raise ValueError("Search must be 2–80 printable characters.")
        selected_region = str(region or "IN").upper()
        if selected_region not in {"IN", "US", "GB", "AU", "CA", "SG", "HK"}:
            raise ValueError("Unsupported search region.")
        safe_limit = max(1, min(int(limit), 15))
        cache_key = ("search", q.casefold(), selected_region, safe_limit)
        cached = self._get_cache(cache_key)
        if cached is not None:
            cached["cached"] = True
            return cached
        self._guard_rate()
        try:
            response = self.session.get(SEARCH_URL, params={
                "q": q, "quotesCount": safe_limit, "newsCount": 0,
                "enableFuzzyQuery": "false", "region": selected_region,
            }, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise StockLookupError(f"Yahoo Finance search request failed ({type(exc).__name__}); check network access and retry.") from None
        except ValueError:
            raise StockLookupError("Yahoo Finance search returned invalid JSON.") from None
        if not isinstance(payload, dict):
            raise StockLookupError("Yahoo Finance search returned an unexpected response.")
        quotes = payload.get("quotes")
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in quotes if isinstance(quotes, list) else []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip()
            if not symbol or not _SYMBOL_RE.fullmatch(symbol) or symbol.upper() in seen:
                continue
            kind = str(item.get("quoteType") or "").upper()
            if kind and kind not in {"EQUITY", "ETF", "INDEX"}:
                continue
            name = str(item.get("longname") or item.get("shortname") or item.get("displayName") or symbol).strip()[:140]
            results.append({
                "symbol": symbol[:32],
                "name": name,
                "exchange": str(item.get("exchDisp") or item.get("exchange") or "Unknown exchange")[:80],
                "quote_type": kind or "UNKNOWN",
                "region": selected_region,
                "score": _finite(item.get("score")),
            })
            seen.add(symbol.upper())
            if len(results) >= safe_limit:
                break
        output = {
            "ok": True, "query": q, "region": selected_region, "results": results,
            "count": len(results), "provider": "Yahoo Finance search endpoint",
            "data_mode": "Symbol search only; selecting a result requests price data separately.",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cached": False,
        }
        return self._put_cache(cache_key, output, 90.0)

    def quote(self, symbol: str, *, chart_range: str = "3mo") -> dict[str, Any]:
        raw_symbol = str(symbol or "").strip()
        if not raw_symbol or len(raw_symbol) > 32 or not _SYMBOL_RE.fullmatch(raw_symbol):
            raise ValueError("Enter a valid ticker from the search results.")
        selected_range = str(chart_range or "3mo").lower()
        if selected_range not in _RANGE_INTERVALS:
            raise ValueError("Unsupported chart range.")
        normalized = raw_symbol.upper()
        cache_key = ("quote", normalized, selected_range)
        cached = self._get_cache(cache_key)
        if cached is not None:
            cached["cached"] = True
            cached["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cached["cache_note"] = "Chart/quote payload reused from short-lived local cache; source timestamp remains unchanged."
            return cached
        self._guard_rate()
        range_param, interval = _RANGE_INTERVALS[selected_range]
        url = f"{CHART_BASE_URL}/{quote(normalized, safe='')}"
        try:
            response = self.session.get(url, params={
                "range": range_param, "interval": interval, "includePrePost": "false", "events": "div,splits",
            }, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise StockLookupError(f"Yahoo Finance price request failed ({type(exc).__name__}); check network access and retry.") from None
        except ValueError:
            raise StockLookupError("Yahoo Finance price endpoint returned invalid JSON.") from None
        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict):
            raise StockLookupError("Yahoo Finance returned an unexpected chart response.")
        upstream_error = chart.get("error")
        results = chart.get("result")
        if upstream_error or not isinstance(results, list) or not results or not isinstance(results[0], dict):
            description = upstream_error.get("description") if isinstance(upstream_error, dict) else None
            raise StockLookupError(f"No Yahoo Finance price history was available for {normalized}. {str(description or 'Check the ticker and exchange.')[:180]}")
        result = results[0]
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        timestamps = result.get("timestamp") if isinstance(result.get("timestamp"), list) else []
        indicators = result.get("indicators") if isinstance(result.get("indicators"), dict) else {}
        quote_sets = indicators.get("quote") if isinstance(indicators.get("quote"), list) else []
        quote_data = quote_sets[0] if quote_sets and isinstance(quote_sets[0], dict) else {}
        opens, highs, lows, closes, volumes = (quote_data.get(key) if isinstance(quote_data.get(key), list) else [] for key in ("open", "high", "low", "close", "volume"))
        candles: list[dict[str, Any]] = []
        for index, stamp in enumerate(timestamps):
            close = _finite(closes[index]) if index < len(closes) else None
            timestamp = _utc_iso(stamp)
            if close is None or close <= 0 or timestamp is None:
                continue
            candles.append({
                "timestamp_utc": timestamp,
                "open": _finite(opens[index]) if index < len(opens) else None,
                "high": _finite(highs[index]) if index < len(highs) else None,
                "low": _finite(lows[index]) if index < len(lows) else None,
                "close": close,
                "volume": _finite(volumes[index]) if index < len(volumes) else None,
            })
        source_timestamp = _utc_iso(meta.get("regularMarketTime"))
        latest_candle = candles[-1] if candles else None
        price = _finite(meta.get("regularMarketPrice"))
        if price is None or price <= 0:
            price = latest_candle["close"] if latest_candle else None
            if source_timestamp is None and latest_candle:
                source_timestamp = latest_candle["timestamp_utc"]
        if price is None or price <= 0:
            raise StockLookupError(f"Yahoo Finance returned no valid price for {normalized}.")
        previous = _finite(meta.get("chartPreviousClose"))
        if previous is None or previous <= 0:
            previous = _finite(meta.get("previousClose"))
        change = (price - previous) if previous is not None and previous > 0 else None
        change_pct = (change / previous * 100.0) if change is not None and previous else None
        source_age = _age_seconds(source_timestamp)
        output = {
            "ok": True,
            "symbol": str(meta.get("symbol") or normalized)[:32],
            "name": str(meta.get("longName") or meta.get("shortName") or meta.get("instrumentType") or normalized)[:140],
            "exchange": str(meta.get("fullExchangeName") or meta.get("exchangeName") or "Unknown exchange")[:80],
            "currency": str(meta.get("currency") or "Unknown")[:12],
            "instrument_type": str(meta.get("instrumentType") or "UNKNOWN")[:30],
            "price": price,
            "previous_close": previous,
            "change": change,
            "change_pct": change_pct,
            "source_timestamp_utc": source_timestamp,
            "source_age_seconds": source_age,
            "freshness_label": _freshness_label(source_age),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": "Yahoo Finance chart endpoint",
            "data_mode": "Latest available provider price + historical candles; real-time status and provider delay are not guaranteed.",
            "chart_range": selected_range,
            "interval": interval,
            "candles": candles[-500:],
            "source_url": f"https://finance.yahoo.com/quote/{quote(normalized, safe='')}/",
            "cached": False,
            "disclaimer": "Informational research only. Price availability and delay depend on Yahoo Finance coverage and terms. No orders are placed.",
        }
        return self._put_cache(cache_key, output, 30.0)
