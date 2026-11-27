# Architecture

## Layers

| Layer | Path | Role |
| --- | --- | --- |
| Contracts | `core/contracts.py` | Typed identities, tasks, results, messages and events. Every task carries task, parent and correlation IDs. |
| Registry | `core/registry.py` | Discovers agents from `agents/*/manifest.json`. Tracks `registered` versus `ready`. |
| Router | `core/router.py` | Scores capabilities and keywords against a request. Preview with `python main.py route "..."`. |
| Runtime | `core/runtime.py` | Runs handlers asynchronously with bounded concurrency, per-task timeouts, delegation and recovery. |
| Workflow | `core/workflow.py` | Dependency-aware task graph. Independent steps run together. |
| Event bus | `core/event_bus.py`, `core/observability.py` | In-process events and a metadata-only audit log. Prompts and results are not logged. |
| Hardening | `core/hardening.py`, `core/policy.py` | Input limits, permission checks and output limits. |
| Agents | `agents/` | Ten specialists, one folder each. |
| Dashboard | `dashboard_server.py`, `dashboard/` | HTTP server and web UI. Mission records go to SQLite. |
| Hosted | `supabase/`, `vercel.json` | Optional Supabase mission persistence and Vercel front end that proxies to the Python API. |

## Request flow

1. A request arrives from the CLI, voice or the dashboard.
2. JARVIS classifies it. Social chat is answered directly. Task requests go to the router.
3. The router picks agents by capability. Compound requests become a workflow graph.
4. The runtime runs each agent, passing a context that lets it delegate to others.
5. Results come back typed and are synthesized into one readable answer, with stage records showing which agent did what.

## Adding an agent

Copy `templates/agent_template/` to `agents/<name>/`, fill in `manifest.json`, implement `create_handler()`, add tests, then run `python main.py list`.
