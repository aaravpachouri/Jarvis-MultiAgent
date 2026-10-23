# JARVIS Quant Desk

The Quant Desk is a separate local dashboard at `http://127.0.0.1:8765/quant`, served by the existing `dashboard_server.py`. It runs from local HTML/CSS/JavaScript and existing Python providers; no third-party JS CDN or paid charting library is required. Return to the Iron Man-inspired main HUD at `/`.

## Use it

1. Start the local server from the project root:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python dashboard_server.py
   ```

2. Open `http://127.0.0.1:8765/quant`.
3. Select **REFRESH MARKET DATA** to start a real Quant job. Page loading itself only reads saved reports.
4. Inspect the provider state, source timestamps, chart session, monitored-universe coverage, candidates and “Reasons to avoid” before relying on any output.

## Zero-cost-first providers

With `JARVIS_MARKET_DATA_PROVIDER=auto` and no configured FYERS credentials, a Quant Desk refresh runs two real specialist tasks in order:

1. `Live Indian market snapshot` through the official NSE Market Data MCP adapter.
2. If NSE MCP is unavailable (including HTTP 404), Quant tries Yahoo Finance's timestamped Nifty 50 and Sensex chart-provider values. This is clearly marked as a fallback—not official NSE data and not guaranteed real-time. The original NSE error is preserved in snapshot warnings.
3. A separate historical/delayed Yahoo chart/indicator screen for the monitored large-cap universe.

The two data classes are kept separate. The snapshot is saved to `data/reports/market_live_snapshot_latest.json`; historical indices, candles and candidate metrics are saved to `data/reports/market_brief_latest.json`. Yahoo fallback data includes the source timestamp as received, when available; the local retrieval time is never substituted for a missing source timestamp. The dashboard keeps fallback status amber and does not treat Yahoo fallback quotes as verified live quotes for candidate pricing. If both sources fail, Quant reports that no verified prices are available rather than fabricating values.

To use only the historical fallback, set `JARVIS_MARKET_DATA_PROVIDER=yahoo`. To use a configured FYERS market-data adapter, set `JARVIS_MARKET_DATA_PROVIDER=fyers` and supply valid credentials in your private `.env`. Do not paste credentials into chat or commit `.env`.

## What “live” means here

The NSE MCP endpoint is a zero-currency-cost-first option where reachable, not a guaranteed exchange-grade tick feed. NSE describes its CM Market Live output as approximately 1–3 minutes behind real time. Tool coverage, source timestamps and endpoint availability can vary. Official exchange data-use policies and terms apply; do not assume unrestricted personal, commercial or redistribution rights just because an endpoint can be accessed without an API key. Check current official NSE terms before sharing outputs or deploying this dashboard for other users.

The graph plots saved, verified candle rows; it is redrawn when the saved report is refreshed and is not a fabricated continuous tick stream. Timestamped snapshot values may be shown separately. A missing timestamp means quote freshness is unknown. A market-data retrieval time is not the same as a quote timestamp. A market closed day may leave the latest quote older than the 3-minute on-session freshness display rule.

## Research watchlist, not buy instructions

“Today's ranked research watchlist” scores candidate setups from data that the selected provider returned for a bounded, monitored stock universe. The default historical fallback does not prove a current intraday entry. Each candidate may include momentum, RSI, trend indicators, a conditional trigger, invalidation, a 2R planning level before costs and reasons to avoid. It is not full-NSE coverage, a guarantee of performance, or personalized financial advice.

Before any real decision, independently confirm the current quote, liquidity/spread, company filings, event/earnings risk, broader market context, valuation/fundamentals, transaction costs and suitability for the individual. Do not interpret a high screen score as an automatic instruction to buy. Quant is read-only and does not place orders.

## API endpoints

- `GET /api/quant/dashboard`: read the shaped, provenance-aware saved data.
- `POST /api/quant/refresh`: begin a refresh and return a job ID immediately.
- `GET /api/quant/refresh/{job_id}`: poll overall and per-stage status.
- `GET /api/events?after=<sequence>`: read real JARVIS runtime events; the Quant feed only shows observed events.

Provider failure, missing results or a snapshot with no usable quotes are visible conditions, not reasons to fabricate values. Keep the dashboard bound to loopback unless you separately implement authentication and secure deployment.
