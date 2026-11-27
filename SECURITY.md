# Security and safety notes

## Boundaries enforced in code

- Quant is research-only. There is no broker or order endpoint.
- Atlas and Launch produce read-only plans. They do not call external systems or deploy.
- Forge works only inside a bounded workspace, checks paths, backs up files, verifies writes and rolls back failed edits.
- Memory & Notes stores only notes the user saves explicitly, on the local filesystem.
- Retrieved web pages, repository text and documents are untrusted input and cannot change agent instructions or permissions.
- Dashboard errors do not expose exception details or environment values.

## Secrets

- `.env` and `.env.*` are git-ignored. Only `.env.example` (no values) is committed.
- Task history, mission databases, market reports, local notes and voice models are git-ignored.
- Never paste keys into issues or pull requests.

## Hosted deployment

- Set `JARVIS_DASHBOARD_ACCESS_KEY`. On Render, the server refuses to run without it.
- Sessions use signed cookies. Production responses set HSTS, CSP, X-Frame-Options and Permissions-Policy.
- There is one shared credential and no tenant isolation. Do not expose it as a multi-customer service.

## Reporting a problem

Open a GitHub issue without sensitive details, or contact the author through his GitHub profile.
