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

Without FYERS credentials, `JARVIS_MARKET_DATA_PROVIDER=auto` uses a zero-currency-cost-first setup: an official NSE MCP live snapshot when explicitly requested, plus Yahoo historical/delayed charts for technical research. The dedicated Quant Desk refresh button requests both stages. This is not a guaranteed free real-time tick feed: NSE describes CM Market Live data as roughly 1–3 minutes behind real time, availability may change, and provider terms/data-use limits still apply. Set `JARVIS_MARKET_DATA_PROVIDER=yahoo` to use historical/delayed data only or `JARVIS_MARKET_DATA_PROVIDER=fyers` to use configured FYERS market-data credentials. Keep `.env` private. See [`docs/QUANT_DESK.md`](docs/QUANT_DESK.md) for setup and limitations.

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

## Phase 13 — Native Iron Man-inspired command environment (historical implementation)

The Overview is now built from native HTML, CSS, and inline SVG. The supplied Iron Man/JARVIS reference is used for art direction only; no reference screenshot or user-supplied image is rendered as the main board. The central armored figure, glowing visor, arc reactor, targeting rings, network links, panels, status indicators, and command dock are individual UI elements with their own styling and motion.

- Ten selectable agent nodes use runtime identities and readiness returned by `/api/overview`.
- Selecting an agent updates the specialist panel. Ready handlers can be targeted directly through `/api/tasks`; registered-only identities route through JARVIS.
- The main command routes to `voice.jarvis`, preserving JARVIS auto-coordination. The microphone control uses browser speech recognition when available.
- Mission-running state reacts to the real task request lifecycle. While the backend is processing a request, the progress bar is indeterminate rather than presenting a fabricated percentage. The reactor/visor and active route respond to that state.
- Agent-to-agent lines appear only when recent persisted task-history entries contain a matching parent-child task relationship. Recent activity is drawn from the persisted task-history API; it does not invent intermediate tool messages.
- Quant/market summaries remain sourced from the saved report API and are not silently labelled as verified live quotes. Other existing views, Python runtime, and CLI remain part of the project.

Start from the project root with `python dashboard_server.py`, then open `http://127.0.0.1:8765`. The server is intended for local use only.

### Phase 13 package note

The Phase 13 source ZIP deliberately omits runtime-generated `data/agent_registry.json`, `data/task_history.jsonl`, `data/reports/`, personal memory notes, `.env` and caches, so extracting it over an existing working folder does not replace your local agent registry, task history, reports, or credentials. `.env.example` remains included. The existing `data` folder is created/seeded by runtime bootstrap when needed.

## Phase 14 — Live runtime intelligence HUD

This phase keeps the native three-column Iron Man-inspired HUD composition and adds a live execution layer driven by the existing Python runtime. The reference image remains art direction only; it is not rendered as a flat main-board background.

- The dashboard observes actual `TaskEvent` and `AgentMessage` records from the runtime event bus and exposes a cursor-based, bounded event feed at `GET /api/events?after=<sequence>`.
- New mission submissions use `POST /api/missions`, return a task ID immediately, execute asynchronously against the existing runtime, and expose status/results at `GET /api/missions/<task_id>`. The legacy synchronous `POST /api/tasks` endpoint remains for CLI/dashboard compatibility.
- The browser polls the event endpoint, shows real queued/running/succeeded/failed lifecycle events, and updates the mission panel, selected agent panel, activity stream, and network view. Progress remains indeterminate during execution rather than inventing completion percentages.
- Handoff links are drawn only from observed `agent.task.delegated` messages or a verified parent-task relationship. The dashboard does not fabricate agent-to-agent conversations.
- Event payloads are allow-listed to safe metadata. Prompts, results, arbitrary message payloads, and raw error text are not echoed into the live activity API. Full task results remain available through the existing result path when the task finishes.
- The UI polls the runtime for updates and renders them; this is event-driven observation, not token-by-token streaming from every underlying provider/tool. A task that only emits start/terminal events will show those events without synthetic intermediate steps.

### Phase 14 verification

- `python -m pytest -q`: **211 passed**.
- `node --check dashboard/assets/app.js`: passed.
- `python -m py_compile dashboard_server.py`: passed.
- Local HTTP checks on the Phase 14 process: Overview and dashboard assets returned HTTP 200; `/api/overview` reported ten identities/eight executable handlers; `/api/events` reported the observer connected.
- A real read-only `memory.notes` mission returned HTTP 202, reached `succeeded`, and yielded the expected queued/running/runtime-start/runtime-success/result event sequence through the live event API.
- Automated browser navigation was blocked by the build environment's Chromium policy, so the API lifecycle was verified over HTTP and frontend JS syntax was checked, but a fresh automated visual-browser pass is not claimed for this phase.

Start with `python dashboard_server.py` and open `http://127.0.0.1:8765`. The dashboard is intended for local use only; do not expose it to a public network.

## Phase 15 — Truthful mission and activity state

The HUD restores the most recent mission outcome from active runtime missions, current-session results, or persisted terminal audit metadata. A completed task is no longer hidden behind a generic “STANDING BY” label when there is no active mission; the main core and Mission Panel distinguish the last outcome from active execution. The active-mission count uses both the active-mission API and latest live task lifecycle events.

Live Activity shows the latest state per task while idle, keeps the current event-by-event trace visible during an active mission, and labels persisted metadata explicitly when the audit log has no prompt/output. Historical agent handoffs use quiet, static lines. Only observed parent/child links inside the current active task lineage receive animated red treatment. No agent conversations or task details are invented to make the interface appear busy.

Validation: **219 tests passed**; JavaScript syntax, Python compilation, local HTTP API/asset checks, and ZIP integrity passed.

### Phase 15 package safety

The Phase 15 ZIP is designed to extract over the existing project folder. It excludes `.env`, `.venv`, `data/agent_registry.json`, `data/task_history.jsonl`, `data/reports/`, local memory notes, Python caches, and test caches. Your local agent registry, task history, generated market reports, credentials, and virtual environment therefore remain untouched by normal ZIP extraction. `.env.example` is included.

## Phase 16 — True agent collaboration and action-level observability (current)

Phase 16 builds on the native three-column Iron Man-inspired HUD and makes its activity feed and network reflect actual work performed by handlers. The reference image remains art direction only; the main board is native HTML/CSS/SVG.

- Added `AgentActionScope` instrumentation for model calls, provider requests, filesystem operations, tests, and specialist workflows. Each operation emits a correlated `agent.action.started` record followed by a terminal success/failure/cancellation event with duration metadata.
- Instrumented the executable Scout, Forge, Quant, JARVIS, Memory & Notes, Scribe, Athena, and Planner workflows. Atlas and Launch remain accurately marked registered-only until executable handlers exist.
- Delegation produces directional `agent.task.delegated` and `agent.task.result` messages with task lineage and status metadata. Targeted `send_message`/`receive_message` also emits actual message-delivered and inbox-received events. The dashboard event serializer exposes an allow-list of metadata only; prompt, result, arbitrary message payloads, tokens, and provider response content are not included in live telemetry.
- The HUD renders separate action/message/task lifecycle rows, deduplicates using stable event IDs, orders events by occurrence time (sequence is a tie-breaker), and keeps historical connections subdued. A handoff is animated only when observed task lineage/message events establish a currently active collaboration.
- The existing async mission endpoints, `/api/events?after=<sequence>`, synchronous `/api/tasks`, CLI, read-only Quant providers, and local-only dashboard remain available.

Run and verify in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest -q
node --check dashboard/assets/app.js
python dashboard_server.py
```

The dashboard stays bound to loopback by default. Action spans make real operations visible, but this is not token-by-token streaming from model providers: only operations instrumented by a handler emit their own action events. No intermediate work or inter-agent conversation is fabricated to make the interface appear busy.

## Phase 18 — Intent-driven local agent-system audit

The local agent-system audit was previously detected with overly narrow phrase matching. A naturally worded objective such as “investigate the actual JARVIS codebase and verify its collaboration infrastructure” could fall through to ordinary capability routing and be handled as web research. The audit now uses independent intent and subject cues rather than requiring a particular mission title.

- `VoiceAgent._is_local_agent_collaboration_audit` looks for local project/code scope, diagnostic action, and agent-runtime/collaboration subject cues. External web research about unrelated projects named JARVIS does not trigger the local audit workflow.
- Once classified, JARVIS chooses each stage handler from the enabled registry by declared capability and executable handler availability. Planning, local source inspection, test execution, independent evidence review, and report writing no longer depend on fixed specialist IDs.
- The workflow passes the real prior-stage outputs into later delegated tasks and records actual child task IDs and outcomes. It keeps explicit test-output parsing separate from handler success and fails/returns partial results when evidence or capabilities are missing.
- The repository inspector is selected by a declared code-inspection capability, not by name. A web-search-only handler is not assigned local filesystem work.
- Report persistence remains tightly scoped: Scribe may create a new, uniquely named Markdown diagnostic report only when the request asks for a report. Writes stay under `data/reports`, use exclusive creation, and do not overwrite existing reports.
- The report distinguishes coordinator-passed context from direct inbox delivery. It does not claim that agents exchanged messages unless send/receive evidence exists.

For example, the routing accepts: “Investigate the actual JARVIS codebase, verify its collaboration infrastructure, and produce an evidence-backed report showing which agents genuinely worked, exchanged information, and returned results.” No special mission name is required.

The workflow is read-only except for the single explicitly authorized new diagnostic report. It does not use external search snippets as a substitute for local code evidence and cannot report success when required stages fail or are unavailable.

## Phase 19 — Evidence-validated local collaboration diagnostic

Phase 18's live run showed two concrete failure modes: Forge received the whole typed `TaskRequest` rendered as text instead of the request's actual `.prompt`, so unrelated parent-context wording could influence its intent classification; and a generic source-inspection answer could be accepted without verified citations to files in the current project. This phase addresses both at the execution and evidence-validation layers.

- `ForgeAgent.__call__` now extracts `TaskRequest.prompt` (and only uses dict/description compatibility paths when appropriate). Delegated `code.test` tasks therefore classify the actual child request instead of the dataclass representation plus unrelated parent context.
- The diagnostic coordinator builds a bounded, read-only local source evidence pack from the active project's `core/`, `agents/`, `tests/`, `dashboard/`, and `scripts/` directories. Secret-like filenames, environment files, caches, dependency directories, and oversized files are excluded.
- The source inspector receives the collected evidence and must cite at least two actual repository-relative paths and at least two class/function symbols found in parsed source. The coordinator validates those citations against the current files; unsupported generalities mark the stage unverified and prevent a successful mission result.
- Test-output extraction prefers explicit pytest/verification/stdout fields over wrapper summaries. The test result remains `unknown` unless a recognizable result and successful handler outcome are both present.
- Forge's read-only pytest invocation disables bytecode and pytest cache generation. It still executes the actual project's test suite.
- The report uses `data/reports/agent_collaboration_test.md` and exclusive creation. If that report already exists, Scribe selects a numbered variant instead of overwriting it.
- The HUD and Python CLI/runtime remain otherwise unchanged.

Verification on the working tree:

- `python -B -m pytest -q -p no:cacheprovider`: **237 passed**.
- Targeted diagnostic, Forge and Forge-classification tests: passed.
- `python -m py_compile agents/voice_agent/agent.py agents/coding_agent/agent.py agents/documentation_agent/agent.py tests/test_phase17_collaboration_diagnostic.py`: passed.
- Source-evidence validation tests cover a valid answer citing two real files/symbols and a generic answer that must remain unverified.
- A regression test confirms typed `TaskRequest` test prompts invoke the test runner even when the parent context contains unrelated audit wording.

Verification boundary: test fixtures prove the routing, evidence gate and report workflow deterministically. They do not guarantee that a user's configured model provider will return adequate source citations on every run. If it does not, the runtime now marks that stage unverified and the mission remains partial rather than claiming success.

## Phase 20 — Provider-independent local source inspection

The live Phase 19 diagnostic run showed that Forge's model-backed source review could time out (`All configured model providers failed`) even though JARVIS had already collected a bounded set of local file paths and parsed symbols. That prevented validation of evidence already available locally.

- During only the `local_agent_system_audit` / `local_source_inspection` stage, Forge now executes a deterministic, read-only source scan over the coordinator-supplied repository-relative paths. It validates paths inside the Forge workspace, reads existing files, parses Python symbols using `ast`, and emits bounded source-line evidence for collaboration-related names.
- This diagnostic-specific path does not call the LLM provider; normal Forge inspection, analysis and implementation requests keep their existing model-backed path.
- The output labels its execution mode as deterministic source analysis, explicitly distinguishing file/symbol verification from LLM semantic review. Athena remains the separate evidence-context review stage.
- If fewer than two source files or fewer than two parsed symbols are available, or the deterministic scan does not emit its explicit `Verification: PASSED` marker, the result is marked incomplete and the coordinator's evidence gate prevents diagnostic success.
- Workflow stage records now expose `execution_mode` when the deterministic diagnostic scan is used; the report does not imply a provider-backed Forge answer occurred.

Verification for this phase:

- `PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q -p no:cacheprovider`: **239 passed**.
- Targeted collaboration/Forge tests: **26 passed**.
- Tested directly against the current project evidence pack: 12 existing source paths scanned; the coordinator's two-path/two-symbol evidence validator passed.
- A test double that raises if the model provider is invoked confirmed this source-inspection stage did not make a provider call.
- A full diagnostic integration test used the real `AgentRuntime`, `ForgeAgent`, `PlannerAgent`, `ResearchAgent` context analysis, and `DocumentationAgent`; the read-only test stage executed successfully in a temporary fixture project, and the report included actual stage/task IDs and the deterministic inspection mode.
- Source-file hashes and the test workspace file list were unchanged after the inspection test.
- The package is verified after clean extraction; read the verification report for exact details and limits.

## Phase 21 — Preserve deterministic source-scan verdicts under output limits (2026-10-20)

A live diagnostic showed real source paths and AST symbols but marked the scan `unverified` because the final `Verification: PASSED` line was lost when the coordinator truncated a verbose child result to 12,000 characters. The scanner can inspect more files than fit in that prefix. The result extractor now preserves both the beginning of the evidence and the scanner's existing terminal verdict when bounding deterministic scan output. It does not add or infer a verdict: if the original output lacks a terminal verification line, the validation gate still rejects it.

Regression tests cover a long successful scan (the real terminal verdict remains visible) and a long result with no verification marker (no pass is manufactured).


## Phase 22 — Quant Desk and general complex-mission orchestration

### Separate Quant Desk

Open `http://127.0.0.1:8765/quant` from the local dashboard, or select **Quant Desk** from the main JARVIS HUD. The separate dark workspace shows index values, historical candle charts, timestamped snapshot quotes when the provider returns parseable quote data, a bounded monitored-universe table, conditional research candidates, risk/invalidation context, market-data provenance, and real Quant runtime action events. The main command center remains at `/`.

- `GET /api/quant/dashboard` reads saved provider artifacts and never invents missing quotes/candles. `POST /api/quant/refresh` explicitly starts a real Quant refresh job; `GET /api/quant/refresh/{id}` reports its stage status. Opening the page does not start provider requests automatically.
- Default/auto mode with no FYERS credentials requests an official NSE MCP snapshot plus the historical/delayed technical screen. A snapshot payload is stored separately at `data/reports/market_live_snapshot_latest.json`; historical chart data remains in `market_brief_latest.json` after a successful full refresh.
- Chart series are verified saved candles, not a fabricated continuously streaming tick chart. The UI uses the quote's own source timestamp where available, treats retrieval time as distinct, and flags stale/unknown quote time.
- The list is a ranked **research watchlist**, not a “buy now” command or personalized advice. It may legitimately be empty. Quant does not place trades or expose order endpoints.
- The default stack requires no paid broker subscription or API key to attempt the NSE MCP snapshot + Yahoo historical fallback, but no-cost availability is not guaranteed. Exchange data can be delayed, endpoint access and coverage can change, and NSE terms restrict how data may be used or redistributed. Keep it personal/local and review provider terms before using or sharing outputs.

See [`docs/QUANT_DESK.md`](docs/QUANT_DESK.md) for operation and data limitations.

### Capability-driven complex missions

Long, explicitly complex/end-to-end requests can now route through `VoiceAgent._run_complex_mission` rather than the older flat compound fan-out. JARVIS delegates a planning task to an executable provider of `planning.breakdown`, selects workstreams from the live capability router and handler registry, then runs a validated dependency graph with independent workstreams parallelized (bounded to three). Sequential research-to-build wording adds actual upstream-result dependencies; downstream specialists receive the completed outputs in `TaskRequest.context`. If a required specialist or dependency fails, the root task returns failed/partial information rather than a success-looking string. Optional report requests add a final capability-discovered Scribe draft stage; generic orchestration does not claim that Scribe wrote a file unless a scoped persistence workflow actually did so.

The orchestration is intentionally not a universal autonomous agent. It is bounded by registered handlers/capabilities, task timeouts, approval policy and the underlying providers/tools. It does not bypass missing capabilities, invent tools, or guarantee that every complex request can be fully completed. Tests verify generic intent detection, capability selection, DAG dependencies, real child task results, final drafting, and failure propagation.

## Phase 23 — Search any company/ticker from the Quant Desk

The Quant Desk at `/quant` now includes a **Find a share price** search workspace. Search for a company name or ticker, choose the relevant regional market/listing, and open the selected result to view its latest available provider price, previous-close change, currency, exchange, source timestamp, retrieval timestamp, freshness label, and selectable price history (1D, 5D, 1M, 3M, 6M where supported by the API, and 1Y; the UI exposes 1D/5D/1M/3M/1Y). The existing market-index trajectory and Quant research watchlist remain separate.

- `GET /api/quant/stock-search?q=...&region=IN` searches symbols through Yahoo Finance's public search endpoint, with regional options for India, US, UK, Australia, Canada, Singapore, and Hong Kong.
- `GET /api/quant/stock-quote?symbol=...&range=3mo` reads quote metadata and historical candles from Yahoo Finance's chart endpoint. Supported chart ranges: `1d`, `5d`, `1mo`, `3mo`, `6mo`, and `1y`.
- `agents/trading_agent/stock_lookup.py` validates query/symbol/range inputs, caps response sizes, uses bounded HTTP timeouts, applies a short in-memory cache and local rate guard, returns safe provider errors, and keeps source quote time separate from retrieval time.
- Search and quote lookups require no API key or brokerage account and expose no order endpoint. A cache hit never refreshes the source timestamp; it only updates local retrieval metadata.
- The UI explicitly labels freshness and provider mode. It does not call a quote “real-time” just because an endpoint returned a price. Missing price/candles are not fabricated, and a provider failure is shown to the user.

### Phase 23 verification

- Full test suite: `python -m pytest -q` — **256 passed**.
- Targeted stock-lookup and Quant Desk tests: **10 passed**.
- Python compilation and `node --check dashboard/assets/quant.js`: passed.
- Mock-provider tests verify Yahoo search/quote parsing, company-symbol mapping, historical candles, price-change calculation, source timestamp, cache behavior, symbol/range validation, no-price failure handling, and local HTTP route semantics.
- External Yahoo connectivity could not be verified from this build environment: an actual symbol-search smoke attempt failed with a connection error. The UI therefore still needs an external-provider check from the user's Windows installation. Public no-key endpoint availability, quote timing, coverage, rate limits and terms can change; this build does not promise uninterrupted or exchange-grade real-time feed access.
- No broker credentials are required by this feature. Quant remains read-only and does not place trades.

## Phase 24 — Market Pulse intent routing and multi-company research

### Routing correction

Market-oriented, multi-company research requests are now detected before the broad local collaboration audit and research-to-code routing branches. The detector uses the objective and market/company-search signals together, while explicit requests to inspect the local codebase/runtime/event bus continue to route to the collaboration diagnostic. This prevents a Market Pulse objective from being misclassified because it also mentions a report, specialist tasks, verification, or collaboration traces.

### Market Pulse workflow

- JARVIS discovers an executable Quant handler by declared capability and delegates the structured `market_pulse_india` workflow to it.
- Quant uses the existing zero-key Yahoo Finance search/chart client to search each requested company separately, select a defensible listing, request available history, and collect Nifty 50 and Sensex quotes. Provider failures, unknown session status and absent source timestamps remain explicit; missing values are not replaced with invented prices.
- Technical metrics are calculated only from returned historical candles. The conditional research shortlist has freshness/history gates and may be empty. Where comparable data exists, the report includes relative five-session performance versus Nifty; all entries remain research candidates, not buy instructions.
- Athena receives Quant's structured result for context-only evidence review, without substituting unrelated web results. Scribe drafts the report; the coordinator persists a new Markdown report under `data/reports/market_pulse_india.md`, using numbered filenames when needed to avoid overwriting prior reports.
- Mission success requires the critical specialist stages and report persistence to succeed, plus usable company and index data. Partial provider coverage is disclosed, and the root task fails closed when required coverage is absent.

### Phase 24 verification

- `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` — **263 passed**.
- Added `tests/test_market_pulse_routing.py`, including the original long Market Pulse objective, flattened-list company extraction, no-write behavior in Quant, stale-quote shortlist rejection, safe numbered report persistence, and a real `AgentRuntime` integration using deterministic provider fixtures for Quant → Athena → Scribe.
- Python source syntax/AST parse: **93 Python files parsed** without syntax errors.
- `node --check dashboard/assets/app.js` and `node --check dashboard/assets/quant.js` — passed.
- Existing Quant company search at `/quant` remains available. No broker login, API key or order endpoint was added.

### Live-provider boundary

Phase 24 automated market workflow checks use a deterministic Yahoo-compatible fixture; they validate routing, structured data handling, technical calculations, report generation and truthful failure gates, but do not prove that Yahoo Finance endpoints are reachable from the user's Windows machine on any particular day. Actual quote timestamps/freshness, source coverage and provider failures are decided from the live response on the user's installation. Yahoo public endpoint availability, request limits, quote delays and terms are not guaranteed. All outputs are read-only research, not personal financial advice or a promise that a stock is a good buy.

## Phase 25: Readable mission results

Mission results are rendered as readable dashboard cards rather than a single clipped monospaced paragraph. The Market Pulse summary separates company and index quote counts, shows whether the shortlist was empty, lists recorded specialist stages, and offers a safe link into the full saved Markdown report. The complete raw runtime output remains available in a collapsed detail section. Generic task outputs receive heading, list, and key/value formatting with HTML escaping. When a mission returns on the main HUD, the mission drawer opens to the formatted answer so it is immediately visible.


## Phase 25 — Readable mission output and Markdown report viewer

- Mission/task output is presented as a structured result card when the output contains a Market Pulse summary: status, company/index quote counts, candidate count, stage results, task IDs, data-quality notices, and a safe action to open the saved report. It preserves the exact raw output in a collapsed detail block.
- Generic outputs receive safe formatting rather than being emitted as one dense preformatted block. The same formatter is reused by the HUD mission drawer, selected-agent output and Task Center recent results.
- The report viewer renders Markdown reports as readable content, including headings, lists, tables, links, quotations and code blocks; unsafe HTML is escaped.
- If the mission was completed on the main HUD, the mission drawer opens to the readable result. The button label changes to `VIEW FORMATTED RESULT` when a result is available.
- This is display logic only: it formats the original output and never fabricates quotes, stage status or report data. Missing session status/freshness is shown as a warning, and an empty research shortlist is explained honestly.

### Phase 25 verification

- Full suite: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` — **272 passed**.
- Python AST parse: **94 files** passed.
- JavaScript syntax checks passed for `result-format.js`, `app.js` and `quant.js`.
- Chromium component render used the actual formatter and stylesheet on a fixture output; it verified the card, four metrics, three stages, report action, session/freshness warning, and no browser-console errors. Full browser navigation to the local app is blocked in the build environment, so no full-site browser pass is claimed.

## Phase 26 — Cinematic mission control and interactive suit bay

- Preserved the existing three-column JARVIS/X HUD and existing central armor illustration. The new small suit controller is positioned absolutely inside the mission drawer header, so it does not add a grid cell or reflow the main HUD.
- Added an original native-SVG miniature suit bay. Clicking the suit cycles four original colorway profiles: Mark III (classic red/gold), Mark V (crimson/silver), Mark XLII (gold/red), and War Machine (graphite/steel with a red core). The mini suit is an interface drawing, not a flattened screenshot or external asset.
- Added a cinematic Mission Control banner with an animated arc-reactor, scan/rim lighting, a phase readout and four stages (Command, Runtime, Coordination, Result). Its labels and progress are driven by observed runtime state, task IDs, terminal outcomes and observed delegation events; it does not invent precise completion percentages or treat an unobserved handoff as completed.
- When no mission is active, the interface displays an awaiting-directive/standby state. Running missions use an indeterminate progress animation until the runtime supplies a meaningful phase transition. Terminal success/failure changes the visual state from the actual task outcome. Reduced-motion preferences are respected.
- Added `tests/test_phase26_cinematic_mission_ui.py` to cover accessible suit markup, the four suit profiles, runtime-driven mission markup, correct handoff/result semantics, and reduced-motion/layout constraints.

### Phase 26 verification

- Full suite: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider` — **277 passed**.
- `node --check dashboard/assets/app.js`, `node --check dashboard/assets/result-format.js`, and `node --check dashboard/assets/quant.js` — passed.
- Embedded Chromium interaction check with deterministic `/api/overview` and `/api/events` fixtures: Mission Control opened, cinematic mission banner visible, miniature suit visible, clicking switched the suit profile from MARK III to MARK V, and no browser JavaScript exceptions occurred.
- The suit uses absolute positioning inside the existing drawer header; the header's width and height remained the same when toggling the suit. The dashboard grid wasn't given a new layout track.
- The full browser check used fixture API data because the build environment blocks direct navigation to the local server from Chromium. This verifies the actual UI markup, styling and interaction, not live provider behavior.
