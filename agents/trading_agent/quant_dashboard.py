"""Data shaping for the local Quant Desk dashboard.

This module never fetches remote data itself. It reads provider-generated report
artifacts, preserves their provenance, and refuses to invent missing prices or
source timestamps. The web dashboard displays a ranked research watchlist, not
personalized trading instructions.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > 25_000_000:
            return None
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _age_seconds(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _safe_rows(value: Any, limit: int = 500) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[-limit:]:
        if not isinstance(item, dict):
            continue
        close = _number(item.get("close") or item.get("last") or item.get("price"))
        if close is None or close <= 0:
            continue
        rows.append({
            "session": str(item.get("session") or item.get("timestamp_utc") or item.get("timestamp") or ""),
            "timestamp_utc": str(item.get("timestamp_utc") or item.get("timestamp") or ""),
            "open": _number(item.get("open")),
            "high": _number(item.get("high")),
            "low": _number(item.get("low")),
            "close": close,
            "volume": _number(item.get("volume")),
        })
    return rows


def _iter_objects(value: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Walk provider response objects conservatively, including JSON text blocks."""
    if depth > 7:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_objects(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:3000]:
            yield from _iter_objects(child, depth + 1)
    elif isinstance(value, str):
        stripped = value.strip()
        if len(stripped) <= 200_000 and stripped[:1] in "[{":
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return
            if isinstance(parsed, (dict, list)):
                yield from _iter_objects(parsed, depth + 1)


def _pick(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    lower_map = {str(key).casefold(): value for key, value in mapping.items()}
    for key in keys:
        value = lower_map.get(key.casefold())
        if value is not None and value != "":
            return value
    return None


def _extract_live_quotes(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, Mapping):
        return []
    raw_results = snapshot.get("results")
    found: dict[str, dict[str, Any]] = {}
    symbol_keys = ("symbol", "ticker", "symbolname", "security", "securityname", "tradingsymbol", "companysymbol")
    price_keys = ("lastprice", "last_price", "last", "ltp", "ltpprice", "lasttradedprice", "price", "close", "lasttradedprice")
    change_keys = ("changepct", "change_pct", "chang_percent", "changepercent", "pchange", "percentchange", "change_1d_pct")
    timestamp_keys = ("timestamp", "timestamp_utc", "lastupdatetime", "lasttradetime", "quotetimestamp", "time", "asof")
    for obj in _iter_objects(raw_results):
        symbol_raw = _pick(obj, symbol_keys)
        price = _number(_pick(obj, price_keys))
        if symbol_raw is None or price is None or price <= 0:
            continue
        symbol = str(symbol_raw).strip()[:80]
        # Avoid accidentally labelling nested non-quote result data as a quote.
        if not symbol or symbol.casefold() in {"nifty 50", "sensex", "nifty", "index"}:
            continue
        change = _number(_pick(obj, change_keys))
        timestamp = _pick(obj, timestamp_keys)
        key = symbol.upper()
        candidate = {
            "symbol": symbol,
            "name": str(_pick(obj, ("companyname", "name", "securityname", "symbolname")) or symbol)[:120],
            "price": price,
            "change_pct": change,
            "source_timestamp": str(timestamp)[:100] if timestamp is not None else None,
            "source": str(obj.get("source") or snapshot.get("provider") or "Provider market-data result")[:140],
        }
        current = found.get(key)
        if current is None or (candidate["source_timestamp"] and not current.get("source_timestamp")):
            found[key] = candidate
    return list(found.values())[:100]


def _summarize_provider_results(snapshot: Mapping[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(snapshot, Mapping):
        return []
    results = snapshot.get("results") if isinstance(snapshot.get("results"), dict) else {}
    summaries = []
    for name, value in list(results.items())[:8]:
        if isinstance(value, dict):
            keys = list(value.keys())[:8]
            preview = ", ".join(f"{key}: {str(value[key])[:80]}" for key in keys)
        elif isinstance(value, list):
            preview = f"{len(value)} returned items"
        else:
            preview = str(value)[:480]
        summaries.append({"name": str(name)[:80], "preview": preview[:600]})
    return summaries


def read_quant_dashboard_data(reports_dir: str | Path) -> dict[str, Any]:
    reports = Path(reports_dir)
    brief = _read_json(reports / "market_brief_latest.json")
    snapshot = _read_json(reports / "market_live_snapshot_latest.json")

    # Backwards compatibility: if the most recent brief is itself an MCP snapshot,
    # treat it as the live snapshot but don't pretend it has historical candle data.
    if isinstance(brief, dict) and "nse_mcp" in str(brief.get("report_type", "")).casefold():
        if not snapshot:
            snapshot = brief
        historical = None
    else:
        historical = brief

    historical = historical if isinstance(historical, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    generated = historical.get("generated_at_utc") or historical.get("generated_at")
    live_generated = snapshot.get("retrieved_at_utc") or snapshot.get("generated_at_utc")
    indexes_raw = historical.get("indexes") if isinstance(historical.get("indexes"), dict) else {}
    indexes = []
    for name, item in indexes_raw.items():
        if not isinstance(item, dict):
            continue
        indexes.append({
            "name": str(name),
            "value": _number(item.get("close") or item.get("last") or item.get("value")),
            "change_1d_pct": _number(item.get("change_1d_pct") or item.get("change_pct")),
            "change_5d_pct": _number(item.get("change_5d_pct")),
            "change_20d_pct": _number(item.get("change_20d_pct")),
            "session": str(item.get("session") or historical.get("latest_session") or ""),
        })

    stocks_raw = historical.get("stocks") if isinstance(historical.get("stocks"), list) else historical.get("broad_quote_movers")
    stocks = []
    for item in stocks_raw if isinstance(stocks_raw, list) else []:
        if not isinstance(item, dict):
            continue
        stocks.append({key: item.get(key) for key in (
            "ticker", "name", "close", "change_1d_pct", "change_5d_pct", "change_20d_pct",
            "relative_5d_vs_nifty_pct", "rsi14", "sma20", "sma50", "macd_histogram",
            "atr14_pct", "volume_vs_recent_median", "turnover_inr", "volume", "bid", "ask",
            "spread_pct", "quote_timestamp_utc", "data_mode", "session", "source_url"
        ) if key in item})

    candidates = historical.get("research_candidates") if isinstance(historical.get("research_candidates"), list) else []
    clean_candidates = []
    for item in candidates[:25]:
        if not isinstance(item, dict):
            continue
        clean_candidates.append({key: item.get(key) for key in (
            "ticker", "name", "close", "change_1d_pct", "change_5d_pct", "change_20d_pct",
            "relative_5d_vs_nifty_pct", "rsi14", "sma20", "sma50", "macd_histogram",
            "atr14_pct", "score", "intraday_metrics", "intraday_setup", "swing_setup",
            "entry_trigger", "invalidation", "target_2r", "risk_reward", "reasons_to_avoid",
            "screen_reason", "risk_checks", "quote_timestamp_utc"
        ) if key in item})

    chart_series_raw = historical.get("chart_series") if isinstance(historical.get("chart_series"), dict) else {}
    index_series_raw = historical.get("index_chart_series") if isinstance(historical.get("index_chart_series"), dict) else {}
    charts: dict[str, list[dict[str, Any]]] = {}
    for ticker, rows in chart_series_raw.items():
        clean = _safe_rows(rows)
        if len(clean) >= 2:
            charts[str(ticker)] = clean
    for label, rows in index_series_raw.items():
        clean = _safe_rows(rows)
        if len(clean) >= 2:
            charts[str(label)] = clean
    intraday_series_raw = historical.get("intraday_chart_series") if isinstance(historical.get("intraday_chart_series"), dict) else {}
    for label, rows in intraday_series_raw.items():
        clean = _safe_rows(rows)
        if len(clean) >= 2:
            charts[str(label)] = clean

    live_quotes = _extract_live_quotes(snapshot)
    # Prefer a returned, timestamped index snapshot over the last daily candle
    # for the two index tiles. The source/status labels below still make delays
    # and fallback providers visible; this changes the displayed value only when
    # a real provider quote was actually parsed.
    for quote in live_quotes:
        symbol_text = f"{quote.get('symbol', '')} {quote.get('name', '')}".casefold()
        if "nifty" in symbol_text:
            label = "Nifty 50"
        elif "sensex" in symbol_text:
            label = "BSE Sensex" if any("bse sensex" in str(item.get("name", "")).casefold() for item in indexes) else "Sensex"
        else:
            continue
        quote_price = _number(quote.get("price"))
        if quote_price is None or quote_price <= 0:
            continue
        matching = next((item for item in indexes if label.casefold() in str(item.get("name", "")).casefold()
                         or (label == "Sensex" and "sensex" in str(item.get("name", "")).casefold())), None)
        quote_timestamp = quote.get("source_timestamp")
        session_label = str(quote_timestamp or "")
        if session_label:
            try:
                session_label = datetime.fromisoformat(session_label.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                session_label = session_label[:10]
        if matching is None:
            matching = {"name": label, "value": quote_price, "change_1d_pct": _number(quote.get("change_pct")),
                        "change_5d_pct": None, "change_20d_pct": None, "session": session_label}
            indexes.append(matching)
        else:
            matching["value"] = quote_price
            if quote.get("change_pct") is not None:
                matching["change_1d_pct"] = _number(quote.get("change_pct"))
            if session_label:
                matching["session"] = session_label
        matching["source_timestamp_utc"] = quote_timestamp
        matching["source"] = quote.get("source")

    if not live_quotes:
        # FYERS report may already have verified quote snapshots merged with history.
        for item in stocks:
            if item.get("quote_timestamp_utc") and _number(item.get("close")) is not None:
                live_quotes.append({
                    "symbol": str(item.get("ticker") or ""), "name": str(item.get("name") or item.get("ticker") or ""),
                    "price": _number(item.get("close")), "change_pct": _number(item.get("change_1d_pct")),
                    "source_timestamp": item.get("quote_timestamp_utc"), "source": historical.get("market_data_source") or "configured provider",
                })
    quote_ages = [_age_seconds(item.get("source_timestamp")) for item in live_quotes]
    known_ages = [age for age in quote_ages if age is not None]
    max_quote_age = max(known_ages) if known_ages else None
    live_status = str(snapshot.get("display_status") or snapshot.get("data_status") or "NO_LIVE_SNAPSHOT")
    is_delayed_fallback = (
        snapshot.get("data_class") == "delayed_fallback"
        or "yahoo_fallback" in str(snapshot.get("report_type", "")).casefold()
    )
    if is_delayed_fallback and live_quotes:
        # A fresh Yahoo source timestamp does not establish that its feed is live
        # or exchange-authoritative. Keep this visibly amber in the client.
        live_label = "YAHOO FALLBACK · PROVIDER DELAY NOT GUARANTEED"
    elif live_quotes and known_ages:
        live_label = ("QUOTE TIMESTAMP WITHIN 3 MINUTES — feed may still be delayed"
                      if max_quote_age is not None and max_quote_age <= 180
                      else "STALE QUOTE TIMESTAMPS — verify before use")
    elif live_quotes:
        live_label = "DATA RETURNED — QUOTE TIMESTAMP UNVERIFIED"
    else:
        live_label = "NO LIVE QUOTES PARSED"

    news_raw = historical.get("news") if isinstance(historical.get("news"), dict) else {}
    news = []
    for item in news_raw.get("results", [])[:8] if isinstance(news_raw.get("results"), list) else []:
        if isinstance(item, dict):
            news.append({"title": str(item.get("title") or "Untitled")[:220], "url": str(item.get("url") or "")[:1000],
                         "domain": str(item.get("domain") or "")[:100], "snippet": str(item.get("snippet") or "")[:600]})

    if historical.get("market_status"):
        market_status = str(historical.get("market_status"))
    elif snapshot:
        if live_quotes and known_ages and max_quote_age is not None and max_quote_age <= 180:
            market_status = "SNAPSHOT RETURNED · SOURCE TIMESTAMP WITHIN 3 MINUTES · FEED MAY BE DELAYED"
        elif live_quotes:
            market_status = "SNAPSHOT RETURNED · QUOTE FRESHNESS UNVERIFIED OR STALE"
        else:
            market_status = "SNAPSHOT RETURNED · NO PRICE QUOTES COULD BE NORMALIZED"
    else:
        market_status = str(live_status or "STATUS UNKNOWN")
    source = str(historical.get("market_data_source") or historical.get("provider") or "No historical market report")
    if not historical:
        source = str(snapshot.get("provider") or source)
    snapshot_tools = snapshot.get("selected_tools") if isinstance(snapshot.get("selected_tools"), dict) else {}
    tool_errors = snapshot.get("tool_errors") if isinstance(snapshot.get("tool_errors"), dict) else {}

    return {
        "ok": True,
        "available": bool(historical or snapshot),
        "generated_at_utc": generated,
        "report_type": historical.get("report_type"),
        "latest_session": historical.get("latest_session"),
        "market": historical.get("market") or "India markets",
        "provider": source,
        "market_status": market_status,
        "freshness_note": historical.get("freshness_note") or snapshot.get("freshness_warning") or snapshot.get("provider_claimed_delay") or "Quote freshness was not provided by the saved report.",
        "report_age_seconds": _age_seconds(generated),
        "stocks_screened": historical.get("stocks_screened", len(stocks)),
        "stock_universe_size": historical.get("stock_universe_size", historical.get("quote_universe_size")),
        "quote_count": historical.get("quote_count", len(live_quotes)),
        "advances": historical.get("advances"),
        "declines": historical.get("declines"),
        "market_pulse": historical.get("market_pulse"),
        "indexes": indexes,
        "stocks": stocks,
        "candidates": clean_candidates,
        "charts": charts,
        "live_snapshot": {
            "available": bool(snapshot),
            "provider": snapshot.get("provider") or "NSE Market Data MCP",
            "status": live_status,
            "data_class": snapshot.get("data_class") or "official_live_or_unknown",
            "label": live_label,
            "retrieved_at_utc": live_generated,
            "age_seconds": _age_seconds(live_generated),
            "provider_claimed_delay": snapshot.get("provider_claimed_delay") or "",
            "freshness_warning": snapshot.get("freshness_warning") or "",
            "tools_discovered": snapshot.get("tools_discovered"),
            "selected_tools": snapshot_tools,
            "tool_errors": {str(key): str(value)[:220] for key, value in list(tool_errors.items())[:12]},
            "results_preview": _summarize_provider_results(snapshot),
            "quotes": live_quotes[:100],
            "quote_age_max_seconds": max_quote_age,
            "disclaimer": snapshot.get("disclaimer") or "Data is for informational use only; source reliability and timeliness are not guaranteed.",
        },
        "gainers": (historical.get("gainers_in_monitored_universe") or historical.get("gainers") or [])[:10],
        "losers": (historical.get("losers_in_monitored_universe") or historical.get("losers") or [])[:10],
        "news": news,
        "source_limitations": historical.get("source_limitations") or "Provider coverage varies. Historical candles do not establish a current intraday price.",
        "disclaimer": historical.get("disclaimer") or "Research support only, not personalized financial advice. No orders are placed.",
        "data_files": {
            "historical_report": "data/reports/market_brief_latest.json" if historical else None,
            "live_snapshot": "data/reports/market_live_snapshot_latest.json" if snapshot else None,
            "chart_count": len(charts),
        },
    }
