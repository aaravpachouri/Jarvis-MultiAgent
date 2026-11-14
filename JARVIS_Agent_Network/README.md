# JARVIS Agent Network

A **new, standalone project** for an expandable multi-agent AI network. It is not an overlay and does not depend on the existing `JARVIS-X` desktop repository. You can extract this project anywhere and develop it independently.

## Phase 2: JARVIS Voice Agent

- JARVIS remains the single front door for typed requests and spoken requests.
- `python main.py voice` enables microphone input and spoken responses.
- Wake-word mode is on by default: say **"Jarvis"** followed by your request. Saying only "Jarvis" arms the next phrase for your command.
- `python main.py voice --no-wake-word` handles each recognized phrase without requiring the wake word.
- The audio layer is optional and isolated in `agents/voice_agent/audio_interface.py`. The main handler and text interface continue to work without microphone or TTS dependencies.
- JARVIS keeps a small, bounded conversation history in memory for the current process and forwards relevant history as context to specialist tasks. This history is not persisted to disk in this phase.
- The main handler can coordinate clearly compound requests across the applicable agents once those agents have real executable handlers; not-yet-active workstreams are reported rather than fabricated.
- An optional OpenAI-compatible model adapter enables more flexible general conversation and task planning. Without credentials/model configuration, deterministic routing still works and no API call is made.

### Voice privacy and connectivity

The default speech-to-text adapter uses the `SpeechRecognition` package's Google web recognition endpoint. The captured speech is sent to that external service for transcription, so use text mode instead for sensitive requests. An internet connection is required for that transcription path. Text-to-speech uses `pyttsx3` with the local system speech engine where available. A future phase can add a local/offline speech-recognition adapter behind the same interface.

### Optional main-handler model

Copy `.env.example` to `.env` and set `JARVIS_LLM_BASE_URL`, `JARVIS_LLM_API_KEY`, and `JARVIS_LLM_MODEL` to a supported OpenAI-compatible provider. Use an OpenAI-compatible local endpoint such as Ollama by setting the base URL to your local endpoint and the model name; local endpoints on `localhost` do not require an API key in this adapter. The model answers open-ended chat directly in ordinary prose when deterministic routing finds no confident specialist match. Specialist execution continues to use deterministic capability routing and runtime handler checks; open-ended conversation no longer depends on the model producing a valid JSON task plan. The configured provider receives the request and recent in-memory conversation context.

Common social conversation works without an API key. The local handler recognises messages such as `how r u`, `how are you`, `are you doing alright`, `how's it going`, thank-yous, and basic greetings. A configured chat model is still needed for flexible answers to arbitrary questions and sophisticated task planning. When Scout returns search results, JARVIS now passes the retrieved titles/snippets to that model to synthesize a grounded answer with numbered source links. Without a configured model, it clearly falls back to an extractive snippet summary rather than pretending it synthesized or verified the pages.

## What the network includes

- A shared, typed core for agent identities, tasks, results, messages, status and events.
- A persistent registry in `data/agent_registry.json`, seeded from the individual agent manifests.
- Filesystem-based agent discovery: each specialist has a dedicated folder containing `manifest.json`, `agent.py`, and `instructions.md`.
- Capability and keyword routing, asynchronous task execution, bounded concurrency, per-task timeouts, task correlation IDs, agent delegation and targeted agent inboxes.
- Nine specialist folders: JARVIS Voice/Main Handler, Scout Search, Forge Coding, Atlas Desktop, Quant Trading, Athena Research, Scribe Documentation, Launch Deployment and Planner.
- Scribe creates evidence-grounded documentation drafts from supplied context without writing files or claiming verification.
- Athena can analyze supplied notes and conversation context offline; source-linked web research remains an optional Scout-backed mode.
- Planner creates ordered task plans, dependencies, checklists, risks and validation gates without requiring web search or code execution.
- A template outside the live agent registry at `templates/agent_template/` for adding further specialists.
- Automated offline tests; the core does not require an AI API key for tests.

## Current implementation status (important)

JARVIS's text-first main handler and optional microphone/speech output interface are implemented. Scribe, Athena and Planner have executable handlers and offline tests. Athena's web-research mode delegates to Scout; context-only analysis does not. Forge remains present but is intentionally out of scope for this development pass. Atlas and Launch remain profile-only until their real handlers are implemented; Quant now has a read-only market-research handler. The CLI marks an identity as `registered` rather than `ready` until its handler is real and callable. JARVIS will not fake research, code changes, trades, desktop actions, documentation updates or deployments.

The shared event bus is currently in-process. Durable cloud workers, authenticated cross-device transport, long-running monitoring, a live dashboard and 24/7 hosting remain later phases. Local voice mode requires the local machine and microphone to be available.

## Project structure

```text
JARVIS_Agent_Network/
├── agents/
│   ├── voice_agent/        # JARVIS main handler + optional audio adapters
│   ├── search_agent/       # Scout
│   ├── coding_agent/       # Forge
│   ├── desktop_agent/      # Atlas
│   ├── trading_agent/      # Quant
│   ├── research_agent/     # Athena
│   ├── documentation_agent/# Scribe
│   ├── planner_agent/      # Planner (offline task breakdown)
│   └── deployment_agent/  # Launch
├── core/                   # Registry, contracts, router, runtime, event bus
├── data/                   # Local registry state; not committed
├── templates/agent_template/
├── tests/
├── main.py
└── pyproject.toml
```

## Setup on Windows 11 + VS Code

From the standalone project root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest -q
```

Install voice support only when you are ready to use the microphone and speech output:

```powershell
python -m pip install -e ".[dev,voice]"
```

### Optional: Kokoro natural-sounding local speech

Kokoro is a local text-to-speech engine. It improves how JARVIS speaks; it does not replace the language model or speech recognition. Install the optional backend:

```powershell
python -m pip install -e ".[dev,voice,kokoro]"
New-Item -ItemType Directory -Force models
Invoke-WebRequest -Uri "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.int8.onnx" -OutFile "models/kokoro-v1.0.int8.onnx"
Invoke-WebRequest -Uri "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin" -OutFile "models/voices-v1.0.bin"
```

The model download is roughly 90–115 MB and the voice pack roughly 28 MB; the first speech may take longer while the model loads. If that release URL changes, use the model-files releases linked from the [kokoro-onnx project](https://github.com/thewh1teagle/kokoro-onnx). Set `JARVIS_TTS_ENGINE=kokoro` in your `.env` to require Kokoro, or leave `auto` to fall back to pyttsx3 until the files exist. `JARVIS_KOKORO_VOICE=af_heart` is the default voice; available voices depend on the installed voice pack.

If `py -3.12` is not available, create the environment using your Python 3.12 executable. The shared runtime uses the Python standard library; pytest is only for development tests.

## Run JARVIS

```powershell
# List identities and whether each has a real executable handler
python main.py list

# Preview capability routing without running any specialist
python main.py route "Search the web for my GitHub profile and inspect my repository code"

# One-shot text request through the main handler
python main.py run "Search the web for Git"

# Interactive text session
python main.py chat

# Microphone input + spoken response; say "Jarvis" once, then speak follow-ups during the conversation window
python main.py voice

# Process every utterance without a wake word (use only in a private/quiet environment)
python main.py voice --no-wake-word

# Choose speech-recognition locale, e.g. en-US or en-IN
python main.py voice --language en-IN
```

The first run creates `data/agent_registry.json`. It contains agent configuration metadata, not API keys. The file is ignored by Git so local registry changes aren't accidentally committed.

## Add a new agent

1. Copy `templates/agent_template/` to `agents/<new_agent_name>/` using a valid Python package name such as `analytics_agent`.
2. Give `manifest.json` a unique stable `agent_id`, display name, purpose, capabilities, routing keywords, timeouts and permissions.
3. Implement `create_handler()` in `agent.py` to return a callable accepting `(task, context)`. Use `context.delegate(...)` or `context.delegate_many(...)` to collaborate with the network.
4. Add unit tests for normal results, failures, timeouts and agent handoffs.
5. Run `python main.py list` and `python main.py route "..."`. An identity is visible as `registered` until its real handler is implemented and returned by `create_handler()`.

## Current active starter agents

- **Scribe** (`documentation.scribe`): emails, reports, meeting notes, proposals and technical-document drafts based on supplied facts. Draft-only; it does not write files or independently verify claims.
- **Athena** (`research.athena`): offline analysis of supplied notes/context, or web research through Scout when research sources are needed. Search snippets are labeled unverified until the pages are independently checked.
- **Planner** (`planning.planner`): goal decomposition, ordered steps, dependencies, deliverables, verification checks and a checklist. It creates plans but does not execute them.

These handlers are intentionally designed so their basic workflows do not depend on Forge or web search. Use `python main.py list` to confirm handler readiness and `python main.py route "Make a step by step action plan"` to inspect routing.

New agent folders are discovered from manifests; the central router does not need a new hardcoded branch for every specialist.

## Engineering constraints

- Agent names can change; IDs remain stable.
- Shared task envelopes carry task and correlation IDs so handoffs can be traced.
- Agent availability is explicit, not inferred from the existence of a folder.
- Credentials belong behind specialist adapters and must be supplied through environment variables or an appropriate secret manager, never committed into source files.
- File-system changes, production deployments, external messages and live trading require appropriately scoped permissions and approval checks before their real handlers are enabled.
- Retrieved webpages, repository content and documents are untrusted input and must not override agent instructions or permission boundaries.
- No system can promise zero errors. Each specialist must verify its outputs and report what was actually tested.

## Memory & Notes (local-first)

The `memory.notes` specialist adds explicit, user-controlled local notes. It saves only when a request clearly asks JARVIS to remember or save something; ordinary conversation is not silently persisted. Notes are stored in `data/memory_notes.json` and are not synced to a remote service.

Examples:
- `Remember that the JARVIS demo is Friday`
- `Save a note: check the agent routing tests`
- `Show my notes`
- `Find my note about the demo`
- `Forget note <note-id>`

The agent refuses to save content that appears to contain credentials (such as passwords, API keys, or access tokens), limits notes to 4,000 characters, and requires a unique ID or unambiguous phrase to delete a note. Back up or delete `data/memory_notes.json` according to your own retention preferences.


## Phase 5 — Sequential multi-agent coordination

JARVIS now supports a bounded, dependency-driven `Athena -> Planner -> Scribe` workflow when a request explicitly asks it to research/analyze a topic, then create a plan, and finally write a report/brief/proposal.

Example:
`Research secure local notes storage, then create a step-by-step plan, and finally write a report.`

Execution behavior:
- Athena runs first. Its actual output and available source records are passed to Planner.
- Planner runs only if Athena succeeds and returns usable output.
- Scribe runs only if Planner succeeds and returns a usable plan; the report receives both the research and planning outputs.
- The response includes child task IDs, per-stage status, and the evidence level supplied by Athena.
- A failed stage stops the workflow; later agents are not invoked with missing or fabricated inputs.
- The generated report remains a draft. The workflow does not claim that proposed plan steps were executed or that Scribe wrote a file.
- Existing research-to-Forge workflows retain precedence rules and are regression-tested.

The deterministic integration tests use test handlers and a fake source record; they verify orchestration, data handoffs, task parent/correlation lineage, and failure-stop behavior without relying on live network services.

## Phase 6 — Dependency-aware workflow graph

`core.workflow.DependencyWorkflow` provides the next orchestration primitive for JARVIS: define workflow steps with explicit `depends_on` edges, run independent ready steps concurrently, and pass prerequisite outputs into dependent steps. The scheduler validates unique IDs, missing dependencies, and cycles before running. A failed step blocks its descendants while independent branches may finish; failures are surfaced in the result rather than replaced with invented outputs.

This is a reusable orchestration foundation. The existing Phase 5 Athena -> Planner -> Scribe CLI workflow remains supported and regression-tested. Next, specialist-specific workflows can be migrated to graph execution incrementally, without replacing the current runtime or voice pipeline.

## Phase 7 — Quant market intelligence (provider-aware upgrade)

Quant (`trading.quant`) supports two modes: **FYERS-backed live quote snapshots** when a valid FYERS Market Data token is configured, or a clearly labelled Yahoo historical/delayed fallback. It never silently describes the fallback as live. For setup and provider limitations, see [`docs/QUANT_LIVE_SETUP.md`](docs/QUANT_LIVE_SETUP.md).

One-command report:

```powershell
python main.py run "Live market briefing: scan NSE, explain today's moves, chart indices and rank intraday and swing research setups"
```

The FYERS mode loads the current NSE cash-equity symbol master, scans quote snapshots in provider-compliant batches, filters for price/volume/estimated turnover, fetches daily candles for the most liquid names, and retrieves 5-minute candles for leading candidates. It calculates day/5-session/20-session returns, RSI(14), SMA(20/50), MACD, ATR(14) percentage, relative strength, turnover estimates, market breadth, 5-minute VWAP context and provider timestamp freshness where data is available. Historical depth and scan size are configurable to balance breadth, rate limits and run time.

The run saves: `data/reports/market_brief_latest.md`, `market_brief_latest.json`, `market_dashboard_latest.html` (embedded SVG price charts), and `market_candidates_latest.csv`. The PowerShell wrapper additionally writes a timestamped Markdown report. The dashboard can be opened locally in a browser.

The ranked list is a **conditional research shortlist**, not a guarantee or personalized instruction to buy. Each candidate can include a trigger, volatility-derived invalidation, a planned 2R target before costs, intraday context and reasons to avoid a setup. Stale quotes, unavailable history, wide spreads, event risk, and unverified news must not be ignored. Current valuation, earnings, filings and other fundamentals are not comprehensively supplied by the price-data API; gaps are explicitly disclosed. Quant remains read-only and never places orders.

Without FYERS credentials, `JARVIS_MARKET_DATA_PROVIDER=auto` uses the Yahoo fallback and marks it `DELAYED/HISTORICAL FALLBACK`. Set `JARVIS_MARKET_DATA_PROVIDER=fyers` to fail closed if live credentials are absent or invalid. Keep `.env` private.

### Optional daily Windows report

From PowerShell opened at the project root, install the local scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_daily_quant_task.ps1
```

It runs at 8:30 AM local time, requires the Windows user to be logged in and the PC awake, and writes timestamped reports plus the Markdown, JSON, HTML dashboard and CSV artifacts. A live FYERS scan can take a few minutes because the provider's request limits are respected. To run it once immediately:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_daily_quant.ps1
```

Remove the scheduled task with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_daily_quant_task.ps1
```

Reports are local outputs and are not committed to the project. The task does not trade or send orders.



### Compact NSE MCP reports
The human-readable `data/reports/market_brief_latest.md` is intentionally concise: it shows provider/status, retrieval time, selected tools, short result previews, warnings, and a pointer to JSON. Full untruncated NSE MCP results and discovered-tool metadata remain in `market_brief_latest.json`.

## JARVIS Intelligence Dashboard (Phase 8)

A cohesive dark command deck for the local agent network. The UI uses a responsive, shared-border layout rather than a pile of disconnected floating cards, and includes:

- Live runtime registry/status from the actual agent handlers
- A task composer that submits work through the existing `voice.jarvis` runtime or a selected ready specialist
- Agent constellation and capability inspector
- Persisted task lifecycle history plus full results for the current dashboard session
- Market-report provenance and timestamp-aware labeling (a saved report is not automatically treated as live data)
- A local report browser for Markdown, JSON, CSV and HTML artifacts
- Read-only market workflows; the dashboard exposes no broker/order endpoints

### Start the dashboard on Windows

From the project folder in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python dashboard_server.py
```

Or run `scripts\run_dashboard.ps1`. Then open **http://127.0.0.1:8765** in your browser. Press `Ctrl+C` in the terminal to stop it.

The server binds to `127.0.0.1` by default and has no authentication layer because it is intended for local use only. Do not bind it to a public interface or expose/port-forward it. You can override the local port with `$env:JARVIS_DASHBOARD_PORT = "8766"` before starting. Keep `JARVIS_DASHBOARD_HOST` on `127.0.0.1`.

The dashboard's status indicators come from the runtime registry. `ready` means an executable handler is registered; `registered` means the identity exists without a handler. The market view reads `data\reports\market_brief_latest.json` and labels its provenance based on the report metadata. Always inspect source timestamps before relying on market values.

### Phase 10 — Iron Man central command interface

The main neural command field now uses the selected Iron Man close-up artwork as its central visual, with the ten agent identities arranged around it. Select an agent node to open its existing agent detail and direct-task workflow. The artwork is included locally at `dashboard/assets/ironman-core.png`; no external image request is required. Agent status remains runtime-derived, and network lines are visual topology rather than proof of individual live messages.


### Phase 10.1 image visibility fix

The central image is served at `/assets/ironman-core.png`. If it appears missing, stop any old dashboard process, restart `python dashboard_server.py` from this extracted project folder, and open `http://127.0.0.1:8765/assets/ironman-core.png` directly. The command field now avoids an opaque central disc over the artwork and moves the ring/text overlay away from the helmet and reactor.

### Phase 11 main-board HUD

The Overview now uses the supplied landscape Iron Man HUD reference as its single main-board composition. Click one of the perimeter agent labels to open that agent's existing workspace/inspection and direct-task flow. The reference image is bundled at `dashboard/assets/jarvis-hud-reference.png`; no external image host is required. Other navigation views and backend functions remain available. The image's built-in graphics are visual artwork; live status values come from the runtime API.
