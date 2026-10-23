# Quant MCP HTTP 404 recovery

## What changed

- The NSE MCP adapter now includes the RPC operation and sanitized endpoint path in HTTP errors, and avoids printing URL query strings or embedded credentials.
- If `JARVIS_NSE_MCP_URL` is set to a stale/custom URL and it returns HTTP 404, the client retries the canonical official CM Market Live endpoint once: `https://mcp.nseindia.in/cmmkt/mcp`.
- If the official NSE MCP request still fails, Quant tries Yahoo Finance's chart endpoint for the Nifty 50 (`^NSEI`) and Sensex (`^BSESN`). It stores any returned provider price and source timestamp, and stores the NSE error in the snapshot's warnings.
- The UI calls this **Yahoo fallback data**, keeps its status amber, shows the original source status and does not treat these quotes as verified live prices for the research candidate cards.
- If neither provider returns a valid positive price, Quant returns an error. It never fills in a missing price or substitutes a local retrieval time for a missing provider timestamp.

NSE's official MCP documentation currently lists the CM Market Live URL above: <https://www.nseindia.com/nse-mcp>. A 404 from that canonical URL may therefore be a transient/provider-side issue; the fallback protects the rest of the Quant Desk but cannot repair the upstream NSE service itself.

## Apply and run

Extract the `JARVIS_Quant_404_Recovery_Overlay.zip` into the existing JARVIS project root and overwrite the matching files. Then stop the previous server with `Ctrl+C`, restart it from the project root, and hard-refresh `/quant` with `Ctrl+F5`.

If you have `JARVIS_NSE_MCP_URL` in `.env`, either remove it to use the default or set it to the canonical value above. Keep `.env` private. No change is required to `JARVIS_MARKET_DATA_PROVIDER=auto`; `JARVIS_MARKET_DATA_PROVIDER=yahoo` intentionally selects historical/delayed mode only.

## Verification and limitation

The combined project tests passed: **286 passed**. Python compilation and `node --check` for both dashboard JavaScript bundles passed. Provider failover tests use controlled responses, not synthetic market values in production.

A live request to NSE/Yahoo could not be verified from the build runner because external DNS/network access was unavailable there. Confirm provider reachability from the machine running JARVIS. Yahoo's own source timestamp is carried through, but its publication delay is not guaranteed; treat this as a fallback snapshot, not an official NSE live feed.
