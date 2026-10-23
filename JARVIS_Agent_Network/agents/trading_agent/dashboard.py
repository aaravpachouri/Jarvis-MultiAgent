"""Small dependency-free HTML/SVG market dashboard writer."""
from __future__ import annotations

import csv
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _svg_line_chart(title: str, rows: list[Mapping[str, Any]], *, width: int = 760, height: int = 220) -> str:
    points = []
    for row in rows:
        try:
            value = float(row.get("close"))
            if value > 0:
                points.append((str(row.get("session") or row.get("timestamp_utc") or ""), value))
        except (TypeError, ValueError):
            continue
    if len(points) < 2:
        return f'<section class="chart"><h3>{html.escape(title)}</h3><p class="muted">Not enough verified price points for a chart.</p></section>'
    values = [p[1] for p in points]
    low, high = min(values), max(values)
    spread = high - low or max(abs(high) * .01, 1.0)
    pad_x, pad_y = 48, 24
    plot_w, plot_h = width - pad_x - 20, height - pad_y - 30
    xy = []
    for index, value in enumerate(values):
        x = pad_x + (index / max(1, len(values) - 1)) * plot_w
        y = pad_y + ((high - value) / spread) * plot_h
        xy.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(xy)
    first, last = values[0], values[-1]
    change = (last / first - 1) * 100 if first else 0
    return (f'<section class="chart"><h3>{html.escape(title)} <span class="change">{change:+.2f}% over chart window</span></h3>'
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)} price history">'
            f'<line x1="{pad_x}" y1="{pad_y}" x2="{pad_x}" y2="{height-30}" class="axis"/>'
            f'<line x1="{pad_x}" y1="{height-30}" x2="{width-20}" y2="{height-30}" class="axis"/>'
            f'<polyline points="{polyline}" class="price-line" fill="none"/>'
            f'<text x="8" y="{pad_y+4}" class="axis-label">{high:,.2f}</text>'
            f'<text x="8" y="{height-30}" class="axis-label">{low:,.2f}</text>'
            f'<text x="{pad_x}" y="{height-8}" class="axis-label">{html.escape(points[0][0])}</text>'
            f'<text x="{width-150}" y="{height-8}" class="axis-label">{html.escape(points[-1][0])}</text>'
            f'</svg><p class="muted">Last value: {last:,.2f}; chart points: {len(points)}.</p></section>')


def write_dashboard(report: Mapping[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    indexes = report.get("indexes") if isinstance(report.get("indexes"), dict) else {}
    series = report.get("chart_series") if isinstance(report.get("chart_series"), dict) else {}
    charts = []
    index_series = report.get("index_chart_series") if isinstance(report.get("index_chart_series"), dict) else {}
    for label, rows in index_series.items():
        if isinstance(rows, list):
            charts.append(_svg_line_chart(label, rows[-60:]))
    for label, data in indexes.items():
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            charts.append(_svg_line_chart(label, data["rows"][-60:]))
    for ticker, rows in list(series.items())[:8]:
        if isinstance(rows, list):
            charts.append(_svg_line_chart(ticker, rows[-60:]))
    candidates = report.get("research_candidates") if isinstance(report.get("research_candidates"), list) else []
    stock_rows = report.get("stocks") if isinstance(report.get("stocks"), list) else []
    market_status = str(report.get("market_status") or "STATUS UNKNOWN")
    provider = str(report.get("market_data_source") or "unknown provider")
    generated = str(report.get("generated_at_utc") or datetime.now(timezone.utc).isoformat())
    stale = str(report.get("freshness_note") or "Freshness could not be determined.")
    table_rows = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        intraday = item.get("intraday_metrics") if isinstance(item.get("intraday_metrics"), dict) else {}
        table_rows.append("<tr>" + "".join(f"<td>{html.escape(str(value if value is not None else 'n/a'))}</td>" for value in (
            item.get("ticker"), item.get("close"), item.get("change_1d_pct"), item.get("change_5d_pct"), item.get("change_20d_pct"),
            item.get("rsi14"), item.get("sma20"), item.get("sma50"), item.get("macd_histogram"), item.get("atr14_pct"),
            item.get("score"), intraday.get("vwap"), item.get("intraday_setup"), item.get("swing_setup"), item.get("entry_trigger"),
            item.get("invalidation"), item.get("target_2r"), item.get("reasons_to_avoid")
        )) + "</tr>")
    if not table_rows:
        table_rows.append('<tr><td colspan="18">No candidate passed the current filters. Do not force a trade.</td></tr>')
    html_doc = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS Quant Market Dashboard</title><style>
:root{{--bg:#0b1020;--panel:#141c2e;--ink:#e8edf8;--muted:#9ba9c4;--line:#283652;--accent:#68d9bd;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Segoe UI,Arial,sans-serif}}main{{max-width:1240px;margin:auto;padding:24px}}h1,h2,h3{{line-height:1.2}}h1{{margin-bottom:6px}}.muted{{color:var(--muted)}}.status{{display:inline-block;padding:5px 10px;border:1px solid var(--line);border-radius:999px;font-weight:700}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}.panel,.chart{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin:14px 0}}.chart svg{{width:100%;height:auto}}.axis{{stroke:var(--line);stroke-width:1}}.axis-label{{fill:var(--muted);font-size:10px}}.price-line{{stroke:var(--accent);stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}}.change{{font-size:12px;color:var(--accent);font-weight:400}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:1000px}}th,td{{text-align:left;border-bottom:1px solid var(--line);padding:9px;vertical-align:top}}th{{color:var(--muted)}}a{{color:var(--accent)}}.warn{{border-left:4px solid #f0b55b;padding:10px 14px;background:#241e16}}footer{{color:var(--muted);font-size:12px;margin:24px 0}}
</style></head><body><main>
<h1>JARVIS Quant</h1><p class="muted">India market intelligence · Generated {html.escape(generated)}</p>
<p><span class="status">{html.escape(market_status)}</span> <span class="status">{html.escape(provider)}</span></p>
<div class="warn"><strong>Data freshness:</strong> {html.escape(stale)}<br>Broker data is account/permission dependent. No orders are placed by this report.</div>
<section class="panel"><h2>Market pulse</h2><p>{html.escape(str(report.get("market_pulse") or "Market pulse unavailable."))}</p>
<p class="muted">Advances: {html.escape(str(report.get('advances','n/a')))} · Declines: {html.escape(str(report.get('declines','n/a')))} · Quotes received: {html.escape(str(report.get('quote_count','n/a')))}</p></section>
<h2>Price charts</h2>{''.join(charts) if charts else '<section class="panel">No chart data was available for this run.</section>'}
<h2>Ranked research candidates</h2><p class="muted">These are conditional research setups, not guaranteed winners or personalized instructions. If the trigger is not met or data is stale, skip.</p>
<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Last</th><th>Day %</th><th>5-session %</th><th>20-session %</th><th>RSI(14)</th><th>SMA20</th><th>SMA50</th><th>MACD hist.</th><th>ATR14 %</th><th>Score</th><th>5m VWAP</th><th>Intraday setup</th><th>Swing setup</th><th>Entry trigger</th><th>Invalidation</th><th>2R target</th><th>Reasons to avoid</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div>
<section class="panel"><h2>Source and limitations</h2><p>{html.escape(provider)}</p><p>{html.escape(str(report.get('source_limitations') or 'Fundamental data and independently verified news may be unavailable.'))}</p></section>
<footer>Research support only. Market data can be delayed, incomplete or interrupted. Verify provider timestamps and official exchange/company filings. Quant is read-only and does not execute trades.</footer>
</main></body></html>'''
    path.write_text(html_doc, encoding="utf-8")
    return path


def persist_report_artifacts(report: Mapping[str, Any], root: str | Path) -> dict[str, str]:
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    json_path = base / "market_brief_latest.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path = write_dashboard(report, base / "market_dashboard_latest.html")
    csv_path = base / "market_candidates_latest.csv"
    candidates = report.get("research_candidates") if isinstance(report.get("research_candidates"), list) else []
    fields = ["ticker", "name", "close", "change_1d_pct", "change_5d_pct", "change_20d_pct", "relative_5d_vs_nifty_pct",
              "volume", "turnover_inr", "spread_pct", "volume_vs_recent_median", "rsi14", "sma20", "sma50", "macd",
              "macd_signal", "macd_histogram", "atr14_pct", "score", "quote_timestamp_utc", "intraday_setup",
              "swing_setup", "entry_trigger", "invalidation", "target_2r", "risk_reward", "reasons_to_avoid"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in candidates:
            if isinstance(item, dict):
                writer.writerow({key: item.get(key, "") for key in fields})
    return {"json": str(json_path), "html_dashboard": str(html_path), "candidates_csv": str(csv_path)}
