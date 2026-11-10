# Scout search reliability and provider setup

Scout now treats the selected search provider as a preference rather than a single point of failure.

## Provider order

- If `JARVIS_SEARCH_PROVIDER` is set, Scout tries that provider first.
- If it fails or returns no usable links, Scout tries other providers whose API keys are configured.
- DuckDuckGo HTML is retained as the broad web-search fallback. Crossref's public scholarly works API requires no API key and is used only for explicit scientific/academic paper discovery; it is not a substitute for general web search or live official data.
- Without API keys, general web queries use DuckDuckGo; explicit scientific/academic paper queries try Crossref and then DuckDuckGo. No code can guarantee a live result when the relevant public providers are offline or blocking requests.

Supported values: `brave`, `tavily`, `duckduckgo` (or `ddg`).

## Recommended configuration

For resilience, configure both independent API providers in the private local `.env` file and in the backend's secret settings:

```dotenv
JARVIS_SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=your_private_brave_key
TAVILY_API_KEY=your_private_tavily_key
JARVIS_SEARCH_TIMEOUT=8
JARVIS_SEARCH_RESULTS=5
```

Do not commit real API keys, put them in frontend variables, or paste them into chat. API-provider plans, quotas and terms apply. If only one key is configured, Scout uses that provider and falls back to DuckDuckGo. If neither key is configured, general web searches use DuckDuckGo and explicit academic searches try Crossref followed by DuckDuckGo.

## Recovery behavior

- A timeout, network error, temporary provider error, authentication error, or empty/unparseable response is recorded against that provider.
- Scout moves to the next configured provider instead of ending the request immediately.
- Scout accumulates valid, unique, titled links and continues to the next configured provider until the requested count is reached or every relevant provider has been tried. Duplicates, malformed URLs, provider-internal links, and untitled entries are discarded.
- Partial result counts and `retrieval_status` are explicit. Each returned source carries its provider. Provider failures, provider attempts, timestamps, and result counts are retained for diagnostics.
- If every provider fails, Scout returns a structured failure. The runtime marks it failed rather than showing a false success.
- Crossref results are scholarly metadata, not proof that a paper's claims are correct; full-text availability varies. Search snippets are discovery leads, not verified facts. Scout does not claim to have independently opened or verified pages. Source-opening/validation must be a separate explicit stage before claims are called verified.

## Operational boundary

This reduces failures caused by an individual provider. It cannot guarantee that every request will return results if the user's network is offline, all providers are unavailable, credentials are invalid, quotas are exhausted, or a query has no relevant results. The correct behavior in those cases is a clear failure with provider diagnostics, never fabricated sources.

## Result contract

Successful Scout responses include:
- `query`, `retrieved_at`, `provider`, `providers_attempted`, and `provider_failures`
- `results`, each with a validated HTTP(S) URL, title, domain, snippet, and source provider
- `requested_result_count`, `result_count`, `partial`, `retrieval_status`, and `evidence_level`

A `retrieval_status=complete` means the requested number of valid, unique search-result records were collected. It does **not** mean the target pages were fetched, read, or fact-checked. Athena should preserve per-query status and mark its overall research partial when any query fails or returns partial evidence.
