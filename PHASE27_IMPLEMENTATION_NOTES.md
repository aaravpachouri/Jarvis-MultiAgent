# JARVIS Phase 27 — Cinematic HUD Overlay

## Apply

Extract `JARVIS_Phase27_Overlay.zip` into the **root of the existing JARVIS project**, allowing the listed dashboard files to overwrite their previous versions. The archive intentionally contains only the Phase 27 UI changes and assets; it does not replace Python backend files, the agent registry, environment/configuration, runtime history, reports, or other project data.

## Changes

- Reworked the main dashboard into a screenshot-matched HUD composition: left-side global view/activity/metrics/quick tools, the Mark XI suit interface in the centre, Mission Control and Current Mission on the right, and a ten-agent strip along the bottom.
- Added ten original, animated, locally bundled SVG portraits in `dashboard/assets/agents/`. They are stylized vector identities (not external or photorealistic stock images) and require no network fetches.
- Integrated the ten existing registry identities into the bottom agent strip, network nodes, Agents view cards, and specialist drawer/modal. The dock's Overview, Capabilities, Tasks, and Personalities controls are interactive.
- Agent status labels, filters, counters, and executable routing derive from the existing runtime agent statuses plus observed live events and active missions. The interface does not invent progress or agent availability.
- Added a Mission Control composer and Auto/Research/Code/Analyze/Plan/Execute routing tabs. Submission uses the existing `runTask()` path and `POST /api/missions`; non-executable specialist targets fall back to JARVIS coordination. The local-runtime, permissions-enforced, and no-live-trade-execution notices remain visible.
- Preserved the Python backend and its current routes/configuration/safety controls; the overlay does not add trade-order or filesystem-write routes.

## Verification performed

- `python -m pytest -q` — **282 passed** (final run: 4.24 seconds).
- `node --check dashboard/assets/app.js` — passed.
- `python -m compileall -q core agents dashboard_server.py main.py agent.py` — passed.
- CSS parsed with no top-level parser errors; all ten bundled SVGs parsed as XML; no duplicate HTML IDs were found.
- Started the local dashboard and confirmed its static route serves a portrait SVG with HTTP 200.
- Headless browser smoke test using data snapshots fetched from the actual local GET endpoints: ten cards and ten local portraits rendered; dock tabs worked; selecting an agent opened its specialist panel; the separate Agents view rendered ten identity cards and a portrait modal; mission submission and terminal-result display worked against a **stubbed** mission response. There were no browser JavaScript errors or failed asset requests.

The mission submission smoke test was stubbed deliberately to avoid launching a real agent task while verifying the UI path. Existing automated tests cover the server/runtime contracts; no production mission or trading action was executed during this verification.
