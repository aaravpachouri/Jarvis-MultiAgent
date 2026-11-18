"""Read-only market-data adapters for Quant.

FYERS credentials are read only from environment variables. This module never
calls an order endpoint. Yahoo support remains a clearly-labelled delayed-data
fallback when no broker market-data provider is configured.
"""
from __future__ import annotations

import csv
import io
import os
import time
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import requests

FYERS_BASE_URL = "https://api-t1.fyers.in"
FYERS_NSE_CM_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"


class ProviderError(RuntimeError):
    """Market-data provider configuration or response error."""


class FyersMarketDataProvider:
    """FYERS read-only quotes and historical candles adapter."""

    def __init__(self, *, client_id: str | None = None, access_token: str | None = None,
                 timeout_seconds: float = 10.0, cache_dir: str | Path = "data/quant") -> None:
        self.client_id = (client_id if client_id is not None else os.getenv("FYERS_CLIENT_ID", "")).strip()
        self.access_token = (access_token if access_token is not None else os.getenv("FYERS_ACCESS_TOKEN", "")).strip()
        self.timeout_seconds = max(2.0, float(timeout_seconds))
        self.cache_dir = Path(cache_dir)
        if not self.client_id or not self.access_token:
            raise ProviderError(
                "FYERS is selected but FYERS_CLIENT_ID/FYERS_ACCESS_TOKEN are not configured in .env. "
                "Create a FYERS app, enable market-data permission, and store the client ID and current access token in .env."
            )
        self.session = requests.Session()
        self._history_lock = threading.Lock()
        self._last_history_request = 0.0
        self.session.headers.update({
            "Authorization": f"{self.client_id}:{self.access_token}",
            "Accept": "application/json",
            "User-Agent": "JARVIS-Quant/1.0",
        })

    @staticmethod
    def is_configured() -> bool:
        return bool(os.getenv("FYERS_CLIENT_ID", "").strip() and os.getenv("FYERS_ACCESS_TOKEN", "").strip())

    def _get_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(f"{FYERS_BASE_URL}{endpoint}", params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ProviderError(f"FYERS request failed ({type(exc).__name__}); check network, token, and market-data permission.") from None
        except ValueError:
            raise ProviderError("FYERS returned a non-JSON response.") from None
        if not isinstance(payload, dict):
            raise ProviderError("FYERS returned an unexpected response shape.")
        code = payload.get("code")
        status = str(payload.get("s", "")).lower()
        if (isinstance(code, int) and code not in (200, 0)) or status == "error":
            message = str(payload.get("message") or payload.get("errmsg") or "Provider rejected the request")
            raise ProviderError(f"FYERS API error: {message[:220]}")
        return payload

    def fetch_quotes(self, symbols: Iterable[str], *, batch_size: int = 50) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """Fetch quote snapshots in provider-compliant batches; missing quotes stay missing."""
        values = list(dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()))
        quotes: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        chunk_size = max(1, min(int(batch_size), 50))
        for start in range(0, len(values), chunk_size):
            batch = values[start:start + chunk_size]
            payload = self._get_json("/data/quotes", {"symbols": ",".join(batch)})
            for item in payload.get("d", []) if isinstance(payload.get("d"), list) else []:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("n") or item.get("symbol") or "")
                if str(item.get("s", "ok")).lower() != "ok" or not isinstance(item.get("v"), dict):
                    errors[symbol or "unknown"] = str(item.get("errmsg") or item.get("message") or "Quote unavailable")
                    continue
                quote = dict(item["v"])
                quote["symbol"] = symbol
                quotes[symbol] = quote
            # FYERS Standard documents 50 quote requests/minute; pace chunks
            # conservatively to avoid burst throttling on broad-market scans.
            if start + chunk_size < len(values):
                time.sleep(float(os.getenv("JARVIS_MARKET_QUOTE_BATCH_PAUSE_SECONDS", "1.21")))
        for symbol in values:
            if symbol not in quotes and symbol not in errors:
                errors[symbol] = "Provider returned no quote for requested symbol."
        return quotes, errors

    def fetch_history(self, symbol: str, *, resolution: str = "D", days: int = 120) -> dict[str, Any]:
        today = date.today()
        start = today - timedelta(days=max(10, int(days)))
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start.isoformat(),
            "range_to": today.isoformat(),
            "cont_flag": "1",
        }
        # Conservative per-request pacing for standard retail API rate limits.
        with self._history_lock:
            now = time.monotonic()
            pause = float(os.getenv("JARVIS_MARKET_HISTORY_PAUSE_SECONDS", "1.21")) - (now - self._last_history_request)
            if self._last_history_request and pause > 0:
                time.sleep(pause)
            self._last_history_request = time.monotonic()
            payload = self._get_json("/data/history", params)
        candles = payload.get("candles")
        if not isinstance(candles, list) or not candles:
            raise ProviderError(f"No {resolution} candles returned for {symbol}.")
        rows: list[dict[str, Any]] = []
        for candle in candles:
            if not isinstance(candle, (list, tuple)) or len(candle) < 6:
                continue
            try:
                stamp = int(candle[0])
                open_price, high, low, close, volume = [float(value) for value in candle[1:6]]
            except (TypeError, ValueError, OverflowError):
                continue
            if close <= 0:
                continue
            rows.append({
                "timestamp_utc": datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds"),
                "session": datetime.fromtimestamp(stamp, timezone.utc).date().isoformat(),
                "epoch": stamp,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume if volume >= 0 else None,
            })
        if not rows:
            raise ProviderError(f"No valid candles returned for {symbol}.")
        return {"symbol": symbol, "resolution": resolution, "rows": rows,
                "source_url": f"{FYERS_BASE_URL}/data/history?{urlencode(params)}"}

    def fetch_chart_data(self, ticker: str, symbol: str, quote: dict[str, Any] | None = None,
                         *, days: int = 120) -> dict[str, Any]:
        history = self.fetch_history(symbol, resolution="D", days=days)
        rows = history["rows"]
        live_quote = quote
        if live_quote is None:
            quotes, errors = self.fetch_quotes([symbol])
            live_quote = quotes.get(symbol)
            if live_quote is None:
                raise ProviderError(errors.get(symbol, f"No quote returned for {symbol}."))
        quote_epoch = _epoch(live_quote.get("tt"))
        quote_timestamp = datetime.fromtimestamp(quote_epoch, timezone.utc).isoformat(timespec="seconds") if quote_epoch else None
        latest_session = datetime.fromtimestamp(quote_epoch, timezone.utc).date().isoformat() if quote_epoch else date.today().isoformat()
        last_price = _number(live_quote.get("lp"))
        if last_price and last_price > 0:
            # Replace today's partial daily close with the current provider quote.
            if rows and rows[-1].get("session") == latest_session:
                rows[-1]["close"] = last_price
                rows[-1]["high"] = max(float(rows[-1].get("high") or last_price), _number(live_quote.get("high_price")) or last_price)
                rows[-1]["low"] = min(float(rows[-1].get("low") or last_price), _number(live_quote.get("low_price")) or last_price)
                rows[-1]["volume"] = _number(live_quote.get("volume")) or rows[-1].get("volume")
            elif quote_epoch and latest_session >= str(rows[-1].get("session", "")):
                rows.append({"timestamp_utc": quote_timestamp, "session": latest_session, "epoch": quote_epoch,
                             "open": _number(live_quote.get("open_price")) or last_price,
                             "high": _number(live_quote.get("high_price")) or last_price,
                             "low": _number(live_quote.get("low_price")) or last_price,
                             "close": last_price, "volume": _number(live_quote.get("volume"))})
        return {
            "ticker": ticker, "provider_symbol": symbol,
            "name": str(live_quote.get("description") or live_quote.get("short_name") or ticker),
            "currency": "INR", "rows": rows, "source_url": history["source_url"],
            "provider": "FYERS", "data_mode": "live_quote_plus_historical_candles",
            "quote_timestamp_utc": quote_timestamp,
            "quote_timestamp_epoch": quote_epoch,
            "quote": live_quote,
            "quote_retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def load_nse_equity_universe(self, *, max_symbols: int = 2500) -> dict[str, dict[str, str]]:
        """Load the provider's current NSE cash-market symbol master, caching it for 24h."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / "fyers_nse_cm.csv"
        use_cache = cache_path.is_file() and (time.time() - cache_path.stat().st_mtime) < 86400
        if use_cache:
            text = cache_path.read_text(encoding="utf-8-sig", errors="replace")
        else:
            try:
                response = requests.get(FYERS_NSE_CM_URL, timeout=self.timeout_seconds,
                                        headers={"User-Agent": "JARVIS-Quant/1.0"})
                response.raise_for_status()
                text = response.content.decode("utf-8-sig", errors="replace")
                if len(text) < 1000:
                    raise ProviderError("FYERS symbol master response was unexpectedly small.")
                cache_path.write_text(text, encoding="utf-8", newline="")
            except requests.RequestException:
                if cache_path.is_file():
                    text = cache_path.read_text(encoding="utf-8-sig", errors="replace")
                else:
                    raise ProviderError("Could not download FYERS NSE symbol master; no cached copy is available.") from None
        result: dict[str, dict[str, str]] = {}
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 10:
                continue
            symbol = row[9].strip()
            if not symbol.startswith("NSE:") or not symbol.endswith("-EQ"):
                continue
            # Ignore malformed names, duplicate symbols and non-standard tickers.
            ticker = symbol[4:-3].strip()
            if not ticker or ticker in result or any(ch in ticker for ch in " /\\"):
                continue
            name = row[1].strip() if len(row) > 1 else ticker
            result[ticker + ".NS"] = {"name": name or ticker, "symbol": symbol}
            if len(result) >= max(1, int(max_symbols)):
                break
        if not result:
            raise ProviderError("FYERS symbol master contained no recognizable NSE equity symbols.")
        return result


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _epoch(value: Any) -> int | None:
    try:
        stamp = int(float(value))
        return stamp if stamp > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None
