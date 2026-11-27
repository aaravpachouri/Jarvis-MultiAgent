# Athena Evidence Lab

Athena Evidence Lab is a reproducible evaluation project for a specific reliability question:

> When a research agent delegates search to another agent, how reliably can it tell the difference between a genuinely supported finding and a plausible-looking but incomplete or irrelevant source result?

This is intentionally narrower than claiming to measure general intelligence or overall research quality. It studies whether Athena's evidence gates fail closed under known failure modes.

## What is implemented

The benchmark invokes the real `ResearchAgent` implementation with a scripted Scout context. It contains ten deterministic scenarios:

| Scenario family | Failure mode being tested |
|---|---|
| Positive Tavily case | Three relevant documentation pages support authentication, endpoint/method, request parameters and response structure |
| Missing dimension | Three substantive pages exist, but the response structure is unsupported |
| Navigation-only content | Extracted text contains documentation navigation rather than technical evidence |
| Irrelevant official pages | Integration guides and changelog pages share the official domain but do not document the Search API |
| Duplicate source | Three queries return the same URL and cannot satisfy a distinct-source threshold |
| Partial retrieval | One of three delegated retrievals fails |
| Total retrieval failure | All delegated retrievals fail and Athena must return insufficient evidence |
| Cross-topic regression | FastAPI official sources remain eligible under topic-specific primary-source rules |
| Cross-topic navigation | Official FastAPI URLs with navigation-only content do not pass the content gate |
| Host spoofing | A lookalike subdomain and third-party pages are not accepted as Tavily primary sources |

Every fixture's title, snippet and extracted text is explicitly labelled synthetic. The documentation URLs are classification-test inputs only. They are not fetched, opened or represented as quotations from the publishers.

## Run it

Use Python 3.12 from the repository root:

```powershell
python -m evaluation.athena_benchmark
python -m evaluation.athena_benchmark --output artifacts/athena-benchmark.json
python -m pytest -q
```

The benchmark has no provider calls, API-key requirements or network dependency. GitHub Actions runs it for relevant pull requests and pushes to `publish/jarvis-multiagent`, then publishes the JSON report as a workflow artifact.

## Metrics

- **Status accuracy:** fraction of cases where Athena returns the expected research status.
- **Completion accuracy:** fraction of cases where the `complete` decision matches the labelled expected decision.
- **Completion precision:** among cases Athena marks complete, the fraction that are labelled complete.
- **Completion recall:** among labelled-complete cases, the fraction Athena successfully marks complete.
- **False-completion count/rate:** incomplete scenarios that Athena incorrectly marks complete; this is the critical safety metric.
- **Technical-coverage accuracy:** accuracy of the four-dimension coverage gate on cases where that gate applies.

The benchmark's expected labels are hand-authored test expectations, not ground truth about live provider behavior. A perfect score means the implementation matches these ten fixtures. It does not prove the system is production-grade or that real extracted webpages are always classified correctly.

## Research protocol and next experiments

A credible follow-on study should preserve a strict separation between synthetic tests and live-provider measurements:

1. Build a versioned corpus of public documentation retrievals. Keep the exact query, provider, retrieval timestamp, URL, extracted text hash and software revision. Store source text only when its licensing and terms permit it.
2. Have two reviewers independently label whether each source is primary, topic-relevant, substantive, and sufficient for each technical claim. Resolve disagreements and report agreement; do not silently treat one annotator as ground truth.
3. Add adversarial cases such as stale versions, redirects, duplicated canonical URLs, conflicting documentation, malformed response examples, snippets that overstate the page, and claims whose citation points to the wrong page.
4. Compare the evidence gate against a simple baseline such as “three official-domain URLs with non-empty text.” Report the false-completion rate and source-relevance precision/recall for both.
5. Publish each benchmark run with its dataset version, code revision, annotation rules, excluded cases and limitations. Never combine fixture-only and live-provider scores into one headline metric.

Do not submit private user prompts, credentials, account data or sensitive research material to the corpus. Use public documentation and synthetic adversarial examples unless contributors have explicit permission to share a dataset item.

## How this becomes a meaningful student research activity

The technical work is only one part of the project. The real research contribution comes from maintaining the evaluation corpus, documenting annotation decisions, testing against live but public sources, publishing honest results, responding to external review, and showing that the tool helps real users avoid unsupported claims.

A useful public impact record should contain verifiable evidence such as:
- benchmark releases and reproducible run artifacts;
- external issue reports, reviews and merged contributions;
- pilot users who consent to provide feedback, plus clearly counted usage and task outcomes;
- before/after error measurements on a disclosed dataset;
- a short paper or technical report describing the method, results, limitations and failed experiments.

Do not claim adoption, accuracy improvements, research publication or user benefit until those outcomes have actually happened. This project is not an admissions formula or a guarantee of admission to any university. Its value is the depth, initiative, collaboration and measurable contribution demonstrated through sustained work.
