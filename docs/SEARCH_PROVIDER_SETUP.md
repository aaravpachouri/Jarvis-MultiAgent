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

Do not commit real API keys, put them in frontend variables, or paste them into chat. API-provider plans, quotas and terms apply. If only one key is configured, Scout uses that provider and falls back to DuckDuckGo. If neither key is configured, Scout uses DuckDuckGo only.

## Recovery behavior

- A timeout, network error, temporary provider error, authentication error, or empty/unparseable response is recorded against that provider.
- Scout moves to the next configured provider instead of ending the request immediately.
- The first provider that returns usable source links supplies the result set; Scout does not make extra paid calls merely to fill the requested result count.
- Partial result counts are explicit. Provider failures and attempted providers are retained in the output for diagnostics.
- If every provider fails, Scout returns a structured failure. The runtime marks it failed rather than showing a false success.
- Crossref results are scholarly metadata, not proof that a paper's claims are correct; full-text availability varies. Search snippets are leads, not verified facts. Research answers must open/check primary sources before presenting claims as confirmed.

## Operational boundary

This reduces failures caused by an individual provider. It cannot guarantee that every request will return results if the user's network is offline, all providers are unavailable, credentials are invalid, quotas are exhausted, or a query has no relevant results. The correct behavior in those cases is a clear failure with provider diagnostics, never fabricated sources.
