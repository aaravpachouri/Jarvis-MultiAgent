# JARVIS Quant: live market setup

## Provider recommendation

Start with FYERS API if you already have (or are willing to open) a FYERS account and the account's Market Data permission. FYERS documents quote snapshots, historical candles, and a WebSocket data socket. Standard-plan quote requests are limited to 50 symbols per request; this scanner respects that limit and conservatively paces quote batches. Verify current account entitlements and plan limits in your FYERS developer console before relying on the scanner.

Alternative: Zerodha Kite Connect provides real-time WebSocket quotes and historical candles on its paid Connect plan. This build's direct live adapter is FYERS; Yahoo remains a historical/delayed fallback, not a second broker adapter.

## Configure credentials safely

1. Create/configure a FYERS developer app in the official FYERS developer portal and enable the required Market Data permission for Quotes, Market Depth and Historical Data.
2. Complete the official FYERS login/OAuth process to obtain an access token. Tokens may expire and need refreshing according to FYERS authentication rules. This project deliberately does not automate login, handle OTPs, or store broker passwords.
3. Copy `.env.example` to `.env` if you do not already have a `.env`. Preserve all of your existing JARVIS variables.
4. Set these entries in `.env` using your own values (never commit `.env`):

   ```dotenv
   JARVIS_MARKET_DATA_PROVIDER=fyers
   FYERS_CLIENT_ID=your_app_client_id
   FYERS_ACCESS_TOKEN=your_current_access_token
   JARVIS_MARKET_SCAN_MAX_SYMBOLS=2500
   JARVIS_MARKET_HISTORY_SYMBOLS=50
   JARVIS_MARKET_MIN_TURNOVER_INR=50000000
   JARVIS_MARKET_MAX_SPREAD_PCT=0.5
   ```

5. Run one command from the project root:

   ```powershell
   python main.py run "Live market briefing: scan NSE, explain today's moves, chart indices and rank intraday and swing research setups"
   ```

   Or use the PowerShell report wrapper:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\run_daily_quant.ps1
   ```

## Outputs

- `data/reports/market_brief_latest.md` — readable briefing (also updated by the PowerShell wrapper)
- `data/reports/market_brief_latest.json` — structured metrics, timestamps, quote coverage, source limitations and candidates
- `data/reports/market_dashboard_latest.html` — local HTML dashboard with embedded SVG price charts and ranked setup table
- `data/reports/market_candidates_latest.csv` — candidate metrics and conditional setup fields
- `data/reports/market_brief_YYYY-MM-DD_HHmm.md` — timestamped Markdown copy from the PowerShell wrapper

Open the HTML dashboard locally in a browser. The report is read-only and never sends orders.

## What the scanner does and does not claim

- It downloads the provider's current NSE cash-market symbol master and scans quotes in batches, then retrieves daily candles for the highest-turnover names and 5-minute candles for leading candidates. History depth is configurable because a broad universe can produce many provider requests.
- It reports price, day change, turnover estimate, market breadth, daily returns, RSI(14), SMA(20/50), MACD, ATR(14) percentage, and 5-minute VWAP context where data is available.
- Quote freshness is evaluated from provider timestamps. Outside market hours or when timestamps are old/missing, the dashboard flags the feed instead of calling it live.
- Entry levels are conditional research triggers, with a volatility-derived invalidation and 2R target estimate before transaction costs. These are not personalized buy instructions, guaranteed predictions, or an automatic order signal. Skip any setup with stale quotes, wide spreads, low liquidity, unresolved event risk, or an unconfirmed trigger.
- Fundamental data such as current P/E, earnings revisions, balance-sheet ratios, and a complete event calendar are not guaranteed by the market-data provider. The report explicitly marks those gaps; validate company filings and other primary sources before making a decision.
- Scout news results remain search leads unless the source page is independently opened and verified.
- When no FYERS credentials are configured, `auto` mode uses Yahoo's historical chart endpoint and labels the result `DELAYED/HISTORICAL FALLBACK`. To fail closed instead of using fallback data, set `JARVIS_MARKET_DATA_PROVIDER=fyers`.

## Troubleshooting

- `FYERS_CLIENT_ID/FYERS_ACCESS_TOKEN are not configured`: set them in the project `.env`, then restart the process. Do not paste tokens into logs or chat.
- HTTP 401/403 or permission error: refresh the token through official FYERS login and verify Market Data permission.
- Quote timestamps missing or stale: do not treat candidate levels as actionable; check the broker terminal and provider status.
- Scan takes a while: broad scans respect provider request limits and can take a few minutes (the full quote scan and per-symbol candles are deliberately paced). Reduce `JARVIS_MARKET_SCAN_MAX_SYMBOLS` or `JARVIS_MARKET_HISTORY_SYMBOLS` for faster runs, accepting narrower coverage.
