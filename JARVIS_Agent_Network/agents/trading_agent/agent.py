"""Quant: provider-aware Indian equity market intelligence and risk-aware screening.

With valid FYERS Market Data credentials, Quant scans provider quote snapshots
and historical candles. Without them, it uses a clearly-labelled Yahoo historical
fallback. Scout snippets remain unverified unless opened and checked. Quant is
read-only: it never places orders or presents a shortlist as personalized advice.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
from datetime import datetime, timezone
from statistics import median
from pathlib import Path
from typing import Any, Mapping

from agents.trading_agent.dashboard import persist_report_artifacts
from agents.trading_agent.providers import FyersMarketDataProvider, ProviderError
from agents.trading_agent.nse_mcp_provider import NSEMCPProvider, NSEMCPError
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

# A deliberately bounded, liquid large-cap universe. Results must never be
# described as exchange-wide top gainers/losers because this is not the full NSE.
DEFAULT_UNIVERSE: dict[str, str] = {
    "RELIANCE.NS": "Reliance Industries",
    "HDFCBANK.NS": "HDFC Bank",
    "ICICIBANK.NS": "ICICI Bank",
    "SBIN.NS": "State Bank of India",
    "AXISBANK.NS": "Axis Bank",
    "KOTAKBANK.NS": "Kotak Mahindra Bank",
    "INFY.NS": "Infosys",
    "TCS.NS": "Tata Consultancy Services",
    "HCLTECH.NS": "HCL Technologies",
    "LT.NS": "Larsen & Toubro",
    "BHARTIARTL.NS": "Bharti Airtel",
    "ITC.NS": "ITC",
    "SUNPHARMA.NS": "Sun Pharmaceutical",
    "TATAMOTORS.NS": "Tata Motors",
    "M&M.NS": "Mahindra & Mahindra",
    "MARUTI.NS": "Maruti Suzuki",
    "TATASTEEL.NS": "Tata Steel",
    "JSWSTEEL.NS": "JSW Steel",
    "ONGC.NS": "ONGC",
    "NTPC.NS": "NTPC",
    "BAJFINANCE.NS": "Bajaj Finance",
}

INDEXES: dict[str, str] = {"^NSEI": "Nifty 50", "^BSESN": "BSE Sensex"}


class MarketDataError(RuntimeError):
    """Raised when a market-data response is missing or malformed."""


class TradingAgent:
    """Read-only market intelligence, trend analysis, and research watchlist."""

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        universe: Mapping[str, str] | None = None,
        max_concurrency: int = 8,
    ) -> None:
        self.timeout_seconds = timeout_seconds or float(os.getenv("JARVIS_MARKET_DATA_TIMEOUT", "5"))
        if self.timeout_seconds <= 0:
            raise ValueError("Market data timeout must be positive.")
        self.universe = dict(universe or DEFAULT_UNIVERSE)
        self.max_concurrency = max(1, min(int(max_concurrency), 8))
        requested_provider = os.getenv("JARVIS_MARKET_DATA_PROVIDER", "auto").strip().lower()
        if requested_provider not in {"auto", "fyers", "yahoo", "nse_mcp"}:
            requested_provider = "auto"
        self.provider_mode = requested_provider
        self.provider_name = ("nse_mcp" if requested_provider == "nse_mcp" else
                              "fyers" if requested_provider == "fyers" or (requested_provider == "auto" and FyersMarketDataProvider.is_configured()) else "yahoo")
        self.reports_dir = os.getenv("JARVIS_MARKET_REPORTS_DIR", str(Path(__file__).resolve().parents[2] / "data" / "reports"))

    async def __call__(self, task: Any, context: Any) -> dict[str, Any]:
        prompt = str(getattr(task, "prompt", "") or "").strip()
        if not prompt:
            return self._error("Tell me which market or stock to analyze.")
        # NSE MCP is a dedicated snapshot mode. Keep the existing Yahoo/FYERS
        # stock-review and daily-brief workflows available even when the user
        # has selected NSE MCP for explicit live-snapshot requests.
        if self.provider_name == "nse_mcp" and re.search(r"\b(snapshot|live market snapshot)\b", prompt, re.I):
            return await self._daily_market_brief_nse_mcp()
        if self._is_single_ticker_request(prompt):
            ticker = self._extract_ticker(prompt)
            if ticker:
                return await self._single_stock_report(ticker)
        return await self._daily_market_brief(context)

    @staticmethod
    def _is_single_ticker_request(prompt: str) -> bool:
        return bool(re.search(r"\b(?:analy[sz]e|review|check|price|trend|performance)\b", prompt, re.I)
                    and re.search(r"\b(?:ticker|symbol|stock|share)\b", prompt, re.I))

    @staticmethod
    def _extract_ticker(prompt: str) -> str | None:
        explicit = re.search(r"\b(?:ticker|symbol)\s*[:=]?\s*([A-Z][A-Z0-9&.-]{0,14}(?:\.NS|\.BO)?)\b", prompt, re.I)
        if explicit:
            ticker = explicit.group(1).upper()
            return ticker if ticker.endswith((".NS", ".BO")) or ticker.startswith("^") else ticker + ".NS"
        for ticker, name in DEFAULT_UNIVERSE.items():
            if name.casefold() in prompt.casefold() or ticker.split(".")[0].casefold() in prompt.casefold():
                return ticker
        return None

    async def _single_stock_report(self, ticker: str) -> dict[str, Any]:
        try:
            data = await asyncio.to_thread(self._fetch_chart_data, ticker)
        except Exception as exc:
            return self._error(f"Could not retrieve market data for {ticker}: {self._safe_error(exc)}")
        series = self._metrics(data)
        if not series:
            return self._error(f"No usable daily price history was returned for {ticker}.")
        return {
            "report_type": "single_stock_review",
            "ticker": ticker,
            "data_timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "latest_session": series["session"],
            "market_data_source": data["source_url"],
            "metrics": series,
            "summary": self._format_single_stock(ticker, series, data["source_url"]),
            "disclaimer": self._disclaimer(),
        }

    async def _daily_market_brief(self, context: Any) -> dict[str, Any]:
        if self.provider_name == "fyers":
            return await self._daily_market_brief_fyers(context)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def fetch(ticker: str) -> tuple[str, dict[str, Any] | None, str | None]:
            async with semaphore:
                try:
                    result = await asyncio.to_thread(self._fetch_chart_data, ticker)
                    return ticker, result, None
                except Exception as exc:
                    return ticker, None, self._safe_error(exc)

        tickers = [*INDEXES.keys(), *self.universe.keys()]
        fetched = await asyncio.gather(*(fetch(ticker) for ticker in tickers))
        data_by_ticker = {ticker: data for ticker, data, error in fetched if data is not None}
        errors = {ticker: error for ticker, data, error in fetched if data is None}

        index_metrics: dict[str, dict[str, Any]] = {}
        for ticker, label in INDEXES.items():
            data = data_by_ticker.get(ticker)
            if data:
                metrics = self._metrics(data)
                if metrics:
                    index_metrics[label] = metrics

        nifty_metrics = index_metrics.get("Nifty 50", {})
        nifty_5d = nifty_metrics.get("change_5d_pct")
        stocks: list[dict[str, Any]] = []
        for ticker, name in self.universe.items():
            data = data_by_ticker.get(ticker)
            if not data:
                continue
            metrics = self._metrics(data)
            if not metrics:
                continue
            relative = None
            if metrics.get("change_5d_pct") is not None and nifty_5d is not None:
                relative = round(metrics["change_5d_pct"] - nifty_5d, 2)
            stocks.append({"ticker": ticker, "name": name, **metrics, "relative_5d_vs_nifty_pct": relative,
                           "source_url": data["source_url"]})

        latest_sessions = [item.get("session") for item in index_metrics.values() if item.get("session")]
        latest_session = max(latest_sessions) if latest_sessions else None
        news = await self._fetch_news(context)
        candidates = self._select_research_candidates(stocks)
        report = self._format_daily_brief(index_metrics, stocks, candidates, news, latest_session, errors)
        freshness_note = self._historical_freshness_note(latest_session)
        report = report.replace("Data source: Yahoo Finance chart data; may be delayed and is not an official exchange feed.",
                                "Data source: Yahoo Finance chart data; delayed/historical fallback, not a guaranteed live feed.\n" + freshness_note)
        report = report.replace("Scope: Nifty 50/Sensex plus a monitored 30-stock large-cap universe; not the entire NSE/BSE market.",
                                f"Scope: Nifty 50/Sensex plus a monitored {len(self.universe)}-stock fallback universe; not the entire NSE/BSE market.")
        payload = {
            "report_type": "daily_india_market_brief",
            "market": "India — NSE/BSE large-cap reference universe",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "latest_session": latest_session,
            "indexes": index_metrics,
            "stocks_screened": len(stocks),
            "stock_universe_size": len(self.universe),
            "gainers_in_monitored_universe": sorted(stocks, key=lambda x: x.get("change_1d_pct", -999), reverse=True)[:5],
            "losers_in_monitored_universe": sorted(stocks, key=lambda x: x.get("change_1d_pct", 999))[:5],
            "research_candidates": candidates,
            "news": news,
            "data_errors": errors,
            "summary": report,
            "market_data_source": "Yahoo Finance chart endpoint (historical/delayed fallback; not a guaranteed live feed)",
            "market_status": "DELAYED/HISTORICAL FALLBACK",
            "freshness_note": freshness_note,
            "quote_count": len(stocks),
            "advances": sum(1 for stock in stocks if (stock.get("change_1d_pct") or 0) > 0),
            "declines": sum(1 for stock in stocks if (stock.get("change_1d_pct") or 0) < 0),
            "stocks": stocks,
            "chart_series": {ticker: data.get("rows", []) for ticker, data in data_by_ticker.items() if data},
            "indexes": {**index_metrics},
            "index_chart_series": {label: data_by_ticker[ticker].get("rows", []) for ticker, label in INDEXES.items() if ticker in data_by_ticker},
            "market_pulse": self._market_pulse(index_metrics, stocks),
            "source_limitations": "Yahoo chart history can be delayed; fundamentals and exchange-verified news are not guaranteed by this feed.",
            "disclaimer": self._disclaimer(),
        }
        payload["artifacts"] = persist_report_artifacts(payload, self.reports_dir)
        payload["summary"] += "\n\nDashboard artifacts: " + ", ".join(payload["artifacts"].values())
        payload["summary"] = payload["summary"].replace("Data source: Yahoo Finance chart data; delayed/historical fallback, not a guaranteed live feed.\n" + freshness_note,
                                                          "Data source: Yahoo Finance chart data; delayed/historical fallback, not a guaranteed live feed.\n" + freshness_note)
        return payload

    @staticmethod
    def _fyers_symbol_for_ticker(ticker: str) -> str:
        aliases = {"^NSEI": "NSE:NIFTY50-INDEX", "^BSESN": "BSE:SENSEX-INDEX"}
        if ticker in aliases:
            return aliases[ticker]
        base = ticker[:-3] if ticker.endswith(".NS") else ticker
        return f"NSE:{base}-EQ"

    @staticmethod
    def _historical_freshness_note(latest_session: str | None) -> str:
        if not latest_session:
            return "FRESHNESS WARNING: No valid market session timestamp was returned."
        try:
            session_date = datetime.fromisoformat(latest_session).date()
            age = (datetime.now(timezone.utc).date() - session_date).days
        except (TypeError, ValueError):
            return "FRESHNESS WARNING: The provider session timestamp could not be parsed."
        if age >= 1:
            return f"FRESHNESS WARNING: Latest available price session is {age} calendar day(s) old ({latest_session}); not live intraday data."
        return f"Data session date: {latest_session}. Daily chart feed is not guaranteed real-time."

    @staticmethod
    def _market_pulse(indexes: Mapping[str, Mapping[str, Any]], stocks: list[dict[str, Any]]) -> str:
        index_parts = []
        for label, metrics in indexes.items():
            index_parts.append(f"{label} {metrics.get('change_1d_pct', 0):+.2f}% day")
        adv = sum(1 for stock in stocks if (stock.get("change_1d_pct") or 0) > 0)
        dec = sum(1 for stock in stocks if (stock.get("change_1d_pct") or 0) < 0)
        return "; ".join(index_parts) + (f". Monitored advance/decline: {adv}/{dec}." if stocks else ". No stock data available.")

    async def _daily_market_brief_nse_mcp(self) -> dict[str, Any]:
        """Fetch official NSE MCP live-market tools without broker credentials."""
        try:
            provider = NSEMCPProvider(timeout_seconds=max(self.timeout_seconds, 12))
            payload = await asyncio.to_thread(provider.get_live_market_brief)
        except (NSEMCPError, OSError, ValueError) as exc:
            return self._error(
                f"NSE MCP market data is unavailable: {self._safe_error(exc)}. "
                "No live prices were fabricated. Check internet access and the official NSE MCP endpoint."
            )
        reports_dir = Path(self.reports_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)
        generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload["generated_at_utc"] = generated
        payload["report_type"] = "nse_mcp_live_market_snapshot"
        payload["freshness_warning"] = (
            "NSE describes this feed as approximately 1–3 minutes behind real-time, but each returned "
            "tool's own timestamp/status must be checked. Retrieval time is not quote time."
        )
        json_path = reports_dir / "market_brief_latest.json"
        md_path = reports_dir / "market_brief_latest.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        # Keep the human-readable Markdown brief deliberately short. The full,
        # unabridged tool schemas/results remain in the companion JSON artifact.
        def preview(value: Any, limit: int = 420) -> str:
            def trim(item: Any, depth: int = 0) -> Any:
                if isinstance(item, str):
                    clean = re.sub(r"\s+", " ", item).strip()
                    return clean if len(clean) <= 100 else clean[:99] + "…"
                if isinstance(item, dict):
                    pairs = list(item.items())[:8]
                    result = {str(k): trim(v, depth + 1) for k, v in pairs}
                    if len(item) > len(pairs):
                        result["_omitted_fields"] = len(item) - len(pairs)
                    return result
                if isinstance(item, list):
                    result = [trim(v, depth + 1) for v in item[:5]]
                    if len(item) > len(result):
                        result.append(f"… {len(item) - len(result)} more items")
                    return result
                return item
            text = value if isinstance(value, str) else json.dumps(trim(value), ensure_ascii=False, default=str, separators=(",", ":"))
            text = re.sub(r"\s+", " ", text).strip()
            return text if len(text) <= limit else text[:limit - 1] + "…"

        selected_tools = payload.get("selected_tools") if isinstance(payload.get("selected_tools"), dict) else {}
        results = payload.get("results") if isinstance(payload.get("results"), dict) else {}
        tool_errors = payload.get("tool_errors") if isinstance(payload.get("tool_errors"), dict) else {}
        lines = [
            "# JARVIS Quant — NSE Snapshot", "",
            f"- **Status:** {payload.get('data_status')}",
            f"- **Generated (UTC):** {generated}",
            f"- **Provider:** {payload.get('provider')}",
            f"- **Tools discovered:** {payload.get('tools_discovered')}",
            f"- **Selected tools:** {', '.join(f'{k}: {v}' for k, v in selected_tools.items()) or 'none'}", "",
            "> **Freshness:** Retrieval time is not quote time. NSE describes CM Market Live as approximately 1–3 minutes behind real time; inspect source timestamps before use.", "",
            "## Snapshot results", "",
        ]
        if results:
            for key, value in results.items():
                lines.append(f"- **{key.replace('_', ' ').title()}:** {preview(value)}")
        else:
            lines.append("- No usable market results returned.")
        if tool_errors:
            lines += ["", "## Warnings", ""]
            for key, value in tool_errors.items():
                lines.append(f"- **{key}:** {preview(value, 240)}")
        lines += ["", "## Full data", "Open `market_brief_latest.json` for complete results, discovered tools, and metadata.", "",
                  str(payload.get("disclaimer")), "", "No orders were placed."]
        md_path.write_text("\n".join(lines), encoding="utf-8")
        payload["artifacts"] = {"markdown": str(md_path), "json": str(json_path)}
        payload["summary"] = (f"NSE MCP snapshot status: {payload.get('data_status')}; "
                              f"discovered {payload.get('tools_discovered')} tools; "
                              f"saved Markdown and JSON reports. Check report timestamps before use.")
        return payload

    async def _daily_market_brief_fyers(self, context: Any) -> dict[str, Any]:
        """Broad quote scan + deeper candles for the most liquid candidates."""
        try:
            provider = FyersMarketDataProvider(timeout_seconds=max(self.timeout_seconds, 8),
                                               cache_dir=Path(__file__).resolve().parents[2] / "data" / "quant")
            max_scan = int(os.getenv("JARVIS_MARKET_SCAN_MAX_SYMBOLS", "2500"))
            max_history = max(20, min(int(os.getenv("JARVIS_MARKET_HISTORY_SYMBOLS", "50")), 500))
            master = await asyncio.to_thread(provider.load_nse_equity_universe, max_symbols=max_scan)
            index_symbols = {"^NSEI": "NSE:NIFTY50-INDEX", "^BSESN": "BSE:SENSEX-INDEX"}
            symbols = [entry["symbol"] for entry in master.values()]
            quote_map, quote_errors = await asyncio.to_thread(provider.fetch_quotes, [*symbols, *index_symbols.values()])
        except (ProviderError, ValueError, OSError) as exc:
            return self._error(f"Live FYERS market data is unavailable: {self._safe_error(exc)}. No live prices or recommendations were fabricated.")

        quote_by_ticker: dict[str, dict[str, Any]] = {}
        for ticker, info in master.items():
            quote_data = quote_map.get(info["symbol"])
            if quote_data:
                quote_by_ticker[ticker] = {**quote_data, "ticker": ticker, "name": info["name"], "provider_symbol": info["symbol"]}
        # Quote coverage is broad; historical indicators are calculated only for the most liquid names.
        min_turnover = float(os.getenv("JARVIS_MARKET_MIN_TURNOVER_INR", "50000000"))
        ranked_quotes = []
        for ticker, item in quote_by_ticker.items():
            try:
                price = float(item.get("lp") or 0)
                volume = float(item.get("volume") or 0)
                change = float(item.get("chp") or 0)
            except (TypeError, ValueError):
                continue
            turnover = price * volume
            bid = _float(item.get("bid"))
            ask = _float(item.get("ask"))
            spread_pct = ((ask - bid) / price * 100) if price > 0 and bid > 0 and ask >= bid else None
            max_spread = float(os.getenv("JARVIS_MARKET_MAX_SPREAD_PCT", "0.5"))
            if price <= 0 or volume <= 0 or turnover < min_turnover:
                continue
            if spread_pct is not None and spread_pct > max_spread:
                continue
            item["spread_pct"] = round(spread_pct, 3) if spread_pct is not None else None
            ranked_quotes.append((ticker, item, turnover, change))
        ranked_quotes.sort(key=lambda entry: (entry[2], abs(entry[3])), reverse=True)
        selected = ranked_quotes[:max_history]

        semaphore = asyncio.Semaphore(self.max_concurrency)
        async def history_one(ticker: str, item: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
            async with semaphore:
                try:
                    quote_snapshot = item if item.get("lp") is not None else None
                    chart = await asyncio.to_thread(provider.fetch_chart_data, ticker, item["provider_symbol"], quote_snapshot)
                    return ticker, chart, None
                except Exception as exc:
                    return ticker, None, self._safe_error(exc)
        stock_history_results = await asyncio.gather(*(history_one(ticker, item) for ticker, item, _, _ in selected))
        stock_data = {ticker: data for ticker, data, error in stock_history_results if data is not None}
        errors = {ticker: error for ticker, data, error in stock_history_results if data is None}

        index_history_results = await asyncio.gather(*(history_one(ticker, {"provider_symbol": symbol, **quote_map.get(symbol, {})})
                                                       for ticker, symbol in index_symbols.items()))
        index_data = {ticker: data for ticker, data, error in index_history_results if data is not None}
        for ticker, error in [(ticker, error) for ticker, data, error in index_history_results if data is None]:
            errors[ticker] = error or "Index history unavailable"
        index_metrics: dict[str, dict[str, Any]] = {}
        index_labels = {"^NSEI": "Nifty 50", "^BSESN": "BSE Sensex"}
        for ticker, data in index_data.items():
            metrics = self._metrics(data)
            if metrics:
                index_metrics[index_labels[ticker]] = metrics

        nifty_5d = index_metrics.get("Nifty 50", {}).get("change_5d_pct")
        stocks = []
        for ticker, data in stock_data.items():
            metrics = self._metrics(data)
            if not metrics:
                continue
            relative = None if metrics.get("change_5d_pct") is None or nifty_5d is None else round(metrics["change_5d_pct"] - nifty_5d, 2)
            stocks.append({"ticker": ticker, "name": data.get("name") or master.get(ticker, {}).get("name", ticker),
                           **metrics, "relative_5d_vs_nifty_pct": relative, "source_url": data.get("source_url")})
        candidates = self._select_research_candidates(stocks, limit=10)
        news = await self._fetch_news(context)

        # Add 5-minute candles to the leading candidates for intraday VWAP/context.
        intraday_metrics = {}
        async def intraday_one(candidate: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
            ticker = candidate["ticker"]
            info = master.get(ticker)
            if not info:
                return ticker, None
            try:
                result = await asyncio.to_thread(provider.fetch_history, info["symbol"], resolution="5", days=5)
                rows = result.get("rows", [])
                if not rows:
                    return ticker, None
                recent = rows[-30:]
                total_volume = sum(float(row.get("volume") or 0) for row in recent)
                vwap = None
                if total_volume > 0:
                    vwap = sum(((float(row.get("high") or row["close"]) + float(row.get("low") or row["close"]) + float(row["close"])) / 3)
                               * float(row.get("volume") or 0) for row in recent) / total_volume
                last = recent[-1]
                prev = recent[-2] if len(recent) > 1 else last
                return ticker, {"last_5m_close": last["close"], "change_5m_pct": round((last["close"] / prev["close"] - 1) * 100, 2) if prev["close"] else None,
                                "vwap": round(vwap, 2) if vwap else None,
                                "above_vwap": bool(vwap and last["close"] > vwap), "candle_timestamp_utc": last.get("timestamp_utc")}
            except Exception:
                return ticker, None
        intraday_results = await asyncio.gather(*(intraday_one(candidate) for candidate in candidates[:10]))
        intraday_metrics = {ticker: value for ticker, value in intraday_results if value}
        for candidate in candidates:
            item = intraday_metrics.get(candidate["ticker"])
            if item:
                candidate["intraday_metrics"] = item
                candidate["intraday_setup"] = ("5m price above VWAP; only consider a breakout confirmation above the session high with volume confirmation."
                                               if item.get("above_vwap") else "No intraday long setup: price is not above computed 5m VWAP or VWAP is unavailable.")
            else:
                candidate["intraday_setup"] = "Intraday candle confirmation unavailable; do not treat as an intraday signal."

        quote_timestamps = []
        now_utc = datetime.now(timezone.utc)
        for item in quote_by_ticker.values():
            try:
                stamp = int(float(item.get("tt")))
                if stamp > 0:
                    quote_timestamps.append(datetime.fromtimestamp(stamp, timezone.utc))
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        newest_quote = max(quote_timestamps) if quote_timestamps else None
        age_seconds = (now_utc - newest_quote).total_seconds() if newest_quote else None
        # India has a fixed UTC+05:30 offset and no daylight-saving changes.
        # Using a fixed offset avoids Windows installations failing when the
        # optional IANA tzdata package is absent.
        from datetime import timedelta
        ist_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
        is_market_hours = ist_now.weekday() < 5 and (ist_now.hour, ist_now.minute) >= (9, 15) and (ist_now.hour, ist_now.minute) <= (15, 30)
        if newest_quote and is_market_hours and age_seconds is not None and 0 <= age_seconds <= 180:
            market_status = "LIVE QUOTES — latest provider tick within 3 minutes"
            freshness_note = f"Newest quote timestamp: {newest_quote.isoformat(timespec='seconds')}; age {int(age_seconds)} seconds. Per-symbol timestamps may differ."
        elif newest_quote:
            market_status = "MARKET CLOSED OR FEED STALE — verify before acting"
            freshness_note = f"Newest quote timestamp: {newest_quote.isoformat(timespec='seconds')}; age {int(age_seconds or 0)} seconds. This is not confirmed live market data at generation time."
        else:
            market_status = "STALE/UNVERIFIED — provider quote timestamps missing"
            freshness_note = "Provider returned no parseable quote timestamps; live freshness could not be confirmed."

        quote_only_stocks = []
        for ticker, item, turnover, change in ranked_quotes:
            quote_only_stocks.append({"ticker": ticker, "name": master[ticker]["name"], "close": float(item.get("lp") or 0),
                                      "change_1d_pct": change, "turnover_inr": round(turnover, 2), "volume": item.get("volume"),
                                      "day_high": item.get("high_price"), "day_low": item.get("low_price"),
                                      "spread_pct": item.get("spread_pct"),
                                      "quote_timestamp_utc": _quote_timestamp(item.get("tt"))})
        advances = sum(1 for item in quote_by_ticker.values() if _float(item.get("chp")) > 0)
        declines = sum(1 for item in quote_by_ticker.values() if _float(item.get("chp")) < 0)
        latest_sessions = [metric.get("session") for metric in index_metrics.values() if metric.get("session")]
        latest_session = max(latest_sessions) if latest_sessions else None
        errors.update({key: value for key, value in quote_errors.items() if key in index_symbols.values()})
        lines = [
            "JARVIS QUANT — LIVE INDIA MARKET INTELLIGENCE", f"Provider: FYERS market-data API (quotes + historical candles)",
            f"Market status: {market_status}", f"Freshness: {freshness_note}", f"Generated (UTC): {now_utc.isoformat(timespec='seconds')}",
            f"Latest available index session: {latest_session or 'unavailable'}", "Universe: live quote scan of NSE cash-equity symbol master; history/indicators for top liquid symbols only.",
            f"Quote scan: {len(quote_by_ticker)} / {len(master)} NSE equity symbols; history indicators: {len(stocks)}; liquidity filter: turnover >= ₹{min_turnover:,.0f}.",
            f"Market breadth: {advances} advances / {declines} declines; quote timestamps are checked, not assumed live.", "", "## Index pulse"
        ]
        if index_metrics:
            lines.extend(self._format_metric_line(label, metric) for label, metric in index_metrics.items())
        else:
            lines.append("Index metrics unavailable; no index values were invented.")
        lines.extend(["", "## Broad NSE quote movers (filtered for positive price and volume)"])
        if quote_only_stocks:
            lines.append("Gainers:")
            lines.extend(f"- {item['name']} ({item['ticker']}): {item['change_1d_pct']:+.2f}% day; LTP ₹{item['close']:.2f}; turnover estimate ₹{item['turnover_inr']:,.0f}." for item in sorted(quote_only_stocks, key=lambda x: x['change_1d_pct'], reverse=True)[:10])
            lines.append("Losers:")
            lines.extend(f"- {item['name']} ({item['ticker']}): {item['change_1d_pct']:+.2f}% day; LTP ₹{item['close']:.2f}; turnover estimate ₹{item['turnover_inr']:,.0f}." for item in sorted(quote_only_stocks, key=lambda x: x['change_1d_pct'])[:10])
        else:
            lines.append("No usable quote data; no movers were fabricated.")
        lines.extend(["", "## Ranked research candidates — conditional, not automatic buy signals"])
        if candidates:
            for candidate in candidates:
                intraday = candidate.get("intraday_metrics") or {}
                lines.append(f"- **{candidate['name']} ({candidate['ticker']})** — LTP ₹{candidate['close']:.2f}; day {candidate['change_1d_pct']:+.2f}%; 5d {self._fmt_pct(candidate.get('change_5d_pct'))}; 20d {self._fmt_pct(candidate.get('change_20d_pct'))}; RSI14 {candidate.get('rsi14','n/a')}; SMA20/50 {candidate.get('sma20','n/a')}/{candidate.get('sma50','n/a')}; MACD histogram {candidate.get('macd_histogram','n/a')}; ATR14 {candidate.get('atr14_pct','n/a')}%; score {candidate.get('score')}. Entry condition: {candidate.get('entry_trigger')}. Invalidation ₹{candidate.get('invalidation')}; 2R target ₹{candidate.get('target_2r')}. Intraday: {candidate.get('intraday_setup')} 5m VWAP: {intraday.get('vwap','n/a')}. Avoid if: {candidate.get('reasons_to_avoid')}")
        else:
            lines.append("No liquid stocks passed the momentum screen. Do not force a trade.")
        lines.extend(["", "## News and catalysts"])
        news_items = list(news.get("results") or []) if isinstance(news.get("results"), list) else []
        if news_items:
            lines.extend(f"- {item.get('title')} — {item.get('snippet')} Source: {item.get('url')}" for item in news_items[:6])
            lines.append("News snippets are leads, not independently verified facts; open primary filings before acting.")
        else:
            lines.append(f"News unavailable: {news.get('note', 'No source-linked results returned.')}")
        lines.extend(["", "## Data quality and limitations", f"Quote responses missing/failed: {len(quote_errors)}; historical fetch failures: {len(errors)}; {', '.join(list(errors)[:20]) if errors else 'no history failures recorded'}.",
                      "Fundamentals, analyst estimates, institutional flows, and exchange-verified event labels are not guaranteed by the market-data feed.",
                      "No orders placed. This is a conditional research screen, not personalized financial advice."])
        summary = "\n".join(lines)
        payload = {
            "report_type": "live_india_market_intelligence", "market": "NSE India", "generated_at_utc": now_utc.isoformat(timespec="seconds"),
            "latest_session": latest_session, "market_status": market_status, "freshness_note": freshness_note,
            "market_data_source": "FYERS API quotes and historical candles", "quote_count": len(quote_by_ticker),
            "quote_universe_size": len(master), "stocks_screened": len(stocks), "stock_universe_size": len(master),
            "advances": advances, "declines": declines, "market_pulse": self._market_pulse(index_metrics, stocks),
            "indexes": index_metrics, "index_chart_series": {index_labels[ticker]: data.get("rows", []) for ticker, data in index_data.items()},
            "stocks": stocks, "broad_quote_movers": quote_only_stocks,
            "chart_series": {ticker: data.get("rows", []) for ticker, data in stock_data.items()},
            "gainers_in_monitored_universe": sorted(quote_only_stocks, key=lambda x: x["change_1d_pct"], reverse=True)[:10],
            "losers_in_monitored_universe": sorted(quote_only_stocks, key=lambda x: x["change_1d_pct"])[:10],
            "research_candidates": candidates, "news": news, "data_errors": errors, "summary": summary,
            "source_limitations": "Live quote snapshots depend on FYERS market-data permissions; historical metrics cover only the most liquid filtered symbols. News snippets are not independently verified; fundamentals are not comprehensively sourced.",
            "disclaimer": self._disclaimer(),
        }
        payload["artifacts"] = persist_report_artifacts(payload, self.reports_dir)
        payload["summary"] += "\n\nSaved artifacts: " + ", ".join(payload["artifacts"].values())
        return payload

    def _fetch_chart_data(self, ticker: str) -> dict[str, Any]:
        if self.provider_name == "fyers":
            provider = FyersMarketDataProvider(timeout_seconds=self.timeout_seconds,
                                               cache_dir=Path(__file__).resolve().parents[2] / "data" / "quant")
            symbol = self._fyers_symbol_for_ticker(ticker)
            return provider.fetch_chart_data(ticker, symbol)
        url = YAHOO_CHART_URL.format(ticker=quote(ticker, safe="^.")) + "?range=3mo&interval=1d&events=history"
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            raise MarketDataError(f"Yahoo Finance returned HTTP {exc.code}.") from None
        except (TimeoutError, URLError, OSError, json.JSONDecodeError) as exc:
            raise MarketDataError(f"Market-data request failed ({type(exc).__name__}).") from None
        chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
        if chart.get("error"):
            raise MarketDataError(str(chart["error"].get("description") or "Provider reported an error."))
        results = chart.get("result") or []
        if not results or not isinstance(results[0], dict):
            raise MarketDataError("Provider returned no chart data.")
        item = results[0]
        timestamps = item.get("timestamp") or []
        indicators = item.get("indicators") or {}
        quote_data = (indicators.get("quote") or [{}])[0]
        closes = quote_data.get("close") or []
        volumes = quote_data.get("volume") or []
        rows: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            close = closes[index] if index < len(closes) else None
            if close is None or not isinstance(close, (int, float)) or not math.isfinite(float(close)):
                continue
            volume = volumes[index] if index < len(volumes) else None
            opens = quote_data.get("open") or []
            highs = quote_data.get("high") or []
            lows = quote_data.get("low") or []
            def _valid_price(values: list[Any]) -> float | None:
                value = values[index] if index < len(values) else None
                return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None
            rows.append({
                "session": datetime.fromtimestamp(int(timestamp), timezone.utc).date().isoformat(),
                "open": _valid_price(opens), "high": _valid_price(highs), "low": _valid_price(lows),
                "close": float(close),
                "volume": float(volume) if isinstance(volume, (int, float)) and math.isfinite(float(volume)) else None,
            })
        if len(rows) < 2:
            raise MarketDataError("Fewer than two valid daily closes were returned.")
        meta = item.get("meta") or {}
        return {
            "ticker": ticker,
            "name": meta.get("longName") or meta.get("shortName") or ticker,
            "currency": meta.get("currency") or "INR",
            "rows": rows,
            "source_url": url,
        }

    @staticmethod
    def _metrics(data: Mapping[str, Any]) -> dict[str, Any] | None:
        rows = list(data.get("rows") or [])
        if len(rows) < 2:
            return None
        closes = [float(row["close"]) for row in rows]
        latest = closes[-1]
        previous = closes[-2]
        if previous <= 0 or latest <= 0:
            return None

        def return_pct(period_sessions: int) -> float | None:
            # Return over N completed sessions, comparing with the close N
            # sessions before the latest close.
            index = max(0, len(closes) - 1 - period_sessions)
            base = closes[index]
            return round((latest / base - 1.0) * 100.0, 2) if base > 0 and len(closes) > period_sessions else None

        volumes = [row.get("volume") for row in rows[-21:] if isinstance(row.get("volume"), (int, float)) and row.get("volume") > 0]
        volume_ratio = None
        if len(volumes) >= 3:
            baseline = median(volumes[:-1]) if len(volumes) > 1 else 0
            if baseline > 0:
                volume_ratio = round(volumes[-1] / baseline, 2)
        def sma(period: int) -> float | None:
            if len(closes) < period:
                return None
            return round(sum(closes[-period:]) / period, 2)

        rsi = None
        if len(closes) >= 15:
            deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))][-14:]
            gains = sum(max(delta, 0.0) for delta in deltas) / 14
            losses = sum(max(-delta, 0.0) for delta in deltas) / 14
            rsi = 100.0 if losses == 0 and gains > 0 else (50.0 if losses == 0 else round(100 - (100 / (1 + gains / losses)), 2))

        atr_pct = None
        ranges = []
        for idx in range(max(1, len(rows) - 14), len(rows)):
            row = rows[idx]
            high, low = row.get("high"), row.get("low")
            prev_close = closes[idx - 1]
            if isinstance(high, (int, float)) and isinstance(low, (int, float)):
                ranges.append(max(float(high) - float(low), abs(float(high) - prev_close), abs(float(low) - prev_close)))
        if ranges and latest > 0:
            atr_pct = round((sum(ranges) / len(ranges)) / latest * 100, 2)

        # Simple EMA-based MACD; unavailable unless enough candles exist.
        def ema(values: list[float], period: int) -> list[float]:
            if not values:
                return []
            alpha = 2 / (period + 1)
            output = [values[0]]
            for value in values[1:]:
                output.append(alpha * value + (1 - alpha) * output[-1])
            return output
        macd_value = macd_signal = macd_hist = None
        if len(closes) >= 26:
            fast = ema(closes, 12)
            slow = ema(closes, 26)
            macd_series = [a - b for a, b in zip(fast, slow)]
            signal_series = ema(macd_series, 9)
            macd_value = round(macd_series[-1], 4)
            macd_signal = round(signal_series[-1], 4)
            macd_hist = round(macd_value - macd_signal, 4)

        result = {
            "session": str(rows[-1].get("session") or "unknown"),
            "close": round(latest, 2),
            "currency": str(data.get("currency") or "INR"),
            "change_1d_pct": round((latest / previous - 1.0) * 100.0, 2),
            "change_5d_pct": return_pct(5),
            "change_20d_pct": return_pct(20),
            "volume_vs_recent_median": volume_ratio,
            "sma20": sma(20), "sma50": sma(50), "rsi14": rsi,
            "atr14_pct": atr_pct, "macd": macd_value, "macd_signal": macd_signal,
            "macd_histogram": macd_hist,
        }
        live_quote = data.get("quote") if isinstance(data.get("quote"), dict) else None
        if live_quote:
            def quote_num(key: str) -> float | None:
                try:
                    value = float(live_quote.get(key))
                    return value if math.isfinite(value) else None
                except (TypeError, ValueError):
                    return None
            quote_epoch = data.get("quote_timestamp_epoch")
            quote_time = data.get("quote_timestamp_utc")
            result.update({
                "close": round(quote_num("lp") or latest, 2),
                "change_1d_pct": round(quote_num("chp") if quote_num("chp") is not None else result["change_1d_pct"], 2),
                "open_price": quote_num("open_price"), "day_high": quote_num("high_price"),
                "day_low": quote_num("low_price"), "volume": quote_num("volume"),
                "turnover_inr": round((quote_num("lp") or latest) * (quote_num("volume") or 0), 2),
                "bid": quote_num("bid"), "ask": quote_num("ask"),
                "spread_pct": round(((quote_num("ask") - quote_num("bid")) / (quote_num("lp") or latest)) * 100, 3)
                              if quote_num("ask") and quote_num("bid") and quote_num("ask") >= quote_num("bid") and (quote_num("lp") or latest) > 0 else None,
                "quote_timestamp_utc": quote_time, "quote_timestamp_epoch": quote_epoch,
                "data_mode": data.get("data_mode", "live_quote_plus_historical_candles"),
            })
        return result

    async def _fetch_news(self, context: Any) -> dict[str, Any]:
        runtime = getattr(context, "runtime", None)
        if runtime is None or not runtime.has_handler("search.scout"):
            return {"status": "unavailable", "results": [], "note": "Scout search handler is not executable."}
        try:
            result = await context.delegate(
                "Search the web for the latest Indian stock market news from the most recent completed trading session: earnings, RBI/macro news, crude oil, rupee, global cues, institutional flows, and sector movers. Prefer Reuters, exchange filings, NSE/BSE, company investor relations and established financial news. Return dated source-linked snippets; do not invent prices.",
                agent_id="search.scout",
                capabilities=("web.search",),
                context={"workstream": "daily_market_news", "source_quality": "search_snippets_not_independently_verified"},
                requested_by="trading.quant",
                timeout_seconds=25,
            )
            if not result.succeeded:
                return {"status": "unavailable", "results": [], "note": str(result.error or result.summary)}
            output = result.output
            if not isinstance(output, dict):
                return {"status": "unavailable", "results": [], "note": "Scout returned an unexpected response shape."}
            results = output.get("results") if isinstance(output.get("results"), list) else []
            return {
                "status": "search_snippets_not_independently_verified" if results else "no_results",
                "retrieved_at": output.get("retrieved_at"),
                "results": [self._clean_news_result(item) for item in results[:6] if isinstance(item, dict)],
                "note": "Search-result snippets are leads, not independently verified facts." if results else str(output.get("summary") or "No usable news results returned."),
            }
        except Exception as exc:
            return {"status": "unavailable", "results": [], "note": f"News search failed ({type(exc).__name__})."}

    @staticmethod
    def _clean_news_result(item: Mapping[str, Any]) -> dict[str, str]:
        return {
            "title": str(item.get("title") or "Untitled source")[:240],
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("snippet") or "")[:700],
            "domain": str(item.get("domain") or ""),
        }

    @staticmethod
    def _select_research_candidates(stocks: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        eligible = [stock for stock in stocks
                    if stock.get("change_5d_pct") is not None
                    and stock.get("change_20d_pct") is not None
                    and stock["change_5d_pct"] > 0
                    and stock["change_20d_pct"] > 0]
        eligible.sort(key=lambda stock: (
            (stock.get("relative_5d_vs_nifty_pct") or 0.0) * 0.35
            + (stock.get("change_20d_pct") or 0.0) * 0.65
        ), reverse=True)
        output = []
        for stock in eligible[:limit]:
            close = float(stock["close"])
            atr_pct = stock.get("atr14_pct")
            stop_pct = max(float(atr_pct or 1.5) * 1.5, 1.0)
            day_high = stock.get("day_high")
            entry = (float(day_high) * 1.001) if isinstance(day_high, (int, float)) and float(day_high) > 0 else close * 1.001
            stop = entry * (1 - stop_pct / 100)
            target = entry + 2 * (entry - stop)
            quote_time = stock.get("quote_timestamp_utc")
            output.append({
                "ticker": stock["ticker"],
                "name": stock["name"],
                "close": round(close, 2),
                "change_1d_pct": stock["change_1d_pct"],
                "change_5d_pct": stock["change_5d_pct"],
                "change_20d_pct": stock["change_20d_pct"],
                "relative_5d_vs_nifty_pct": stock.get("relative_5d_vs_nifty_pct"),
                "rsi14": stock.get("rsi14"), "sma20": stock.get("sma20"), "sma50": stock.get("sma50"),
                "macd_histogram": stock.get("macd_histogram"), "atr14_pct": atr_pct,
                "score": round((float(stock.get("relative_5d_vs_nifty_pct") or 0) * .3
                                 + float(stock.get("change_20d_pct") or 0) * .45
                                 + float(stock.get("change_5d_pct") or 0) * .25), 2),
                "intraday_setup": "Conditional momentum setup; require a fresh quote and confirmed breakout above the session high with adequate volume." if quote_time else "Not actionable intraday: no live broker quote configured.",
                "swing_setup": "Trend continuation candidate only while price holds above its defined invalidation level." if stock.get("sma20") is not None else "Insufficient indicator history for a swing setup.",
                "entry_trigger": f"Only consider after a confirmed breakout above ₹{entry:.2f} with volume; not an automatic order" if isinstance(day_high, (int, float)) else f"Research trigger above recent close ₹{entry:.2f}; not a live entry signal",
                "invalidation": round(stop, 2), "target_2r": round(target, 2), "risk_reward": "2:1 planned before costs, only if entry trigger confirms",
                "reasons_to_avoid": "; ".join([
                    reason for reason, condition in [
                        ("live quote missing/stale", not quote_time),
                        ("spread unavailable", stock.get("spread_pct") is None and quote_time is not None),
                        ("spread exceeds filter", isinstance(stock.get("spread_pct"), (int, float)) and stock["spread_pct"] > .5),
                        ("RSI overbought (>75)", isinstance(stock.get("rsi14"), (int, float)) and stock["rsi14"] > 75),
                        ("volume confirmation weak", isinstance(stock.get("volume_vs_recent_median"), (int, float)) and stock["volume_vs_recent_median"] < 1),
                        ("earnings/news event not cleared", True),
                        ("price below invalidation or risk budget exceeded", True),
                    ] if condition
                ]),
                "quote_timestamp_utc": quote_time,
                "screen_reason": "Positive 5-session and 20-session momentum, ranked by relative strength and trend metrics; not a buy instruction.",
                "risk_checks": ["Review latest results and valuation", "Check exchange filings and liquidity", "Define downside risk and position size", "Re-evaluate if momentum reverses"],
            })
        return output

    def _format_daily_brief(
        self,
        indexes: Mapping[str, Mapping[str, Any]],
        stocks: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        news: Mapping[str, Any],
        latest_session: str | None,
        errors: Mapping[str, str],
    ) -> str:
        lines = [
            "JARVIS QUANT — DAILY INDIA MARKET BRIEF",
            f"Latest available index session: {latest_session or 'unavailable'}",
            f"Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "Data source: Yahoo Finance chart data; may be delayed and is not an official exchange feed.",
            f"Scope: Nifty 50/Sensex plus a monitored {len(self.universe)}-stock large-cap fallback universe; not the entire NSE/BSE market.",
            "",
            "## Index pulse",
        ]
        if indexes:
            for label, metrics in indexes.items():
                lines.append(self._format_metric_line(label, metrics))
        else:
            lines.append("No index prices could be retrieved. No index figures are fabricated.")
        lines.extend(["", "## Biggest movers in the monitored universe"])
        if stocks:
            gainers = sorted(stocks, key=lambda x: x.get("change_1d_pct", -999), reverse=True)[:5]
            losers = sorted(stocks, key=lambda x: x.get("change_1d_pct", 999))[:5]
            lines.append("Gainers:")
            lines.extend(f"- {s['name']} ({s['ticker']}): {s['change_1d_pct']:+.2f}% day; 5-session {self._fmt_pct(s.get('change_5d_pct'))}; 20-session {self._fmt_pct(s.get('change_20d_pct'))}." for s in gainers)
            lines.append("Losers:")
            lines.extend(f"- {s['name']} ({s['ticker']}): {s['change_1d_pct']:+.2f}% day; 5-session {self._fmt_pct(s.get('change_5d_pct'))}; 20-session {self._fmt_pct(s.get('change_20d_pct'))}." for s in losers)
        else:
            lines.append("No stock prices were retrieved. Movers and recommendations are unavailable for this run.")
        lines.extend(["", "## Research candidates — not buy instructions"])
        if candidates:
            for stock in candidates:
                lines.append(f"- **{stock['name']} ({stock['ticker']})** — close ₹{stock['close']:.2f}; day {stock['change_1d_pct']:+.2f}%; 5-session {stock['change_5d_pct']:+.2f}%; 20-session {stock['change_20d_pct']:+.2f}%; relative 5-session vs Nifty {self._fmt_pct(stock.get('relative_5d_vs_nifty_pct'))}. Screen rationale: positive 5- and 20-session momentum. Risks: verify valuation/earnings/filings and define downside before considering any trade.")
        else:
            lines.append("No stocks in the monitored universe met the simple positive 5- and 20-session momentum screen. This is not a sell signal; it means no candidates passed this screen today.")
        lines.extend(["", "## News and drivers (search snippets)"])
        news_items = list(news.get("results") or []) if isinstance(news.get("results"), list) else []
        if news_items:
            for item in news_items[:6]:
                if item.get("url"):
                    lines.append(f"- {item.get('title', 'Untitled source')} — {item.get('snippet', '')} Source: {item['url']}")
            lines.append("News caveat: snippets have not been independently opened or verified; confirm important claims against exchange filings and primary sources.")
        else:
            lines.append(f"Recent news unavailable: {news.get('note', 'no usable search results')}")
        if errors:
            lines.extend(["", "## Data coverage and failures", f"Retrieved {len(stocks)}/{len(self.universe)} monitored stocks. Failed symbols: {', '.join(sorted(errors))}."])
        lines.extend(["", "## How to use this report", "This is a screening brief, not personalized financial advice. Momentum can reverse quickly. Validate fundamentals, valuation, liquidity, corporate filings and your own risk limits before making decisions. No trades were placed."])
        return "\n".join(lines)

    @staticmethod
    def _format_metric_line(label: str, metrics: Mapping[str, Any]) -> str:
        return (f"- {label}: {metrics['close']:,.2f} {metrics.get('currency', 'INR')} on {metrics['session']}; "
                f"day {metrics['change_1d_pct']:+.2f}%; 5-session {TradingAgent._fmt_pct(metrics.get('change_5d_pct'))}; "
                f"20-session {TradingAgent._fmt_pct(metrics.get('change_20d_pct'))}.")

    @staticmethod
    def _format_single_stock(ticker: str, metrics: Mapping[str, Any], source_url: str) -> str:
        return (f"{ticker} — latest available session {metrics['session']}. Close: {metrics['close']:,.2f} {metrics['currency']}; "
                f"day {metrics['change_1d_pct']:+.2f}%; 5-session {TradingAgent._fmt_pct(metrics.get('change_5d_pct'))}; "
                f"20-session {TradingAgent._fmt_pct(metrics.get('change_20d_pct'))}.\nData: {source_url}\n" + TradingAgent._disclaimer())

    @staticmethod
    def _fmt_pct(value: Any) -> str:
        return "n/a" if value is None else f"{float(value):+.2f}%"

    @staticmethod
    def _disclaimer() -> str:
        return ("For research and education only, not personalized financial advice. These are momentum-screen candidates, "
                "not guaranteed winners or instructions to buy. Prices may be delayed. Verify data and primary filings. "
                "No brokerage integration or trade execution is enabled.")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, MarketDataError):
            return str(exc)
        return f"{type(exc).__name__}: {str(exc)[:180]}"

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"report_type": "market_brief_error", "error": message, "summary": message}


def _float(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _quote_timestamp(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(float(value)), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def create_handler() -> TradingAgent:
    return TradingAgent()
