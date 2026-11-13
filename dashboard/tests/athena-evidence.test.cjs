const test = require("node:test");
const assert = require("node:assert/strict");
const { render } = require("../assets/athena-evidence");

function sampleReport() {
  return {
    benchmark: {
      name: "Athena Evidence Lab",
      version: "1.0",
      execution_mode: "deterministic_synthetic_fixtures",
      provider_calls: 0,
      created_at: "2026-11-12T17:55:42Z",
      python_version: "3.12.10",
      fixture_warning: "All source text is synthetic; fixture URLs were not fetched.",
      git_revision: "abc123",
    },
    metrics: {
      scenario_count: 1,
      status_accuracy: 1,
      completion_accuracy: 1,
      completion_precision: 1,
      completion_recall: 1,
      false_completion_count: 0,
      false_completion_rate: 0,
      technical_coverage_accuracy: 1,
      technical_coverage_case_count: 1,
      critical_gate: "PASS",
    },
    cases: [{
      case_id: "tavily_navigation_only",
      expected: { research_status: "insufficient_extracted_content", complete: false },
      actual: {
        research_status: "insufficient_extracted_content",
        complete: false,
        source_count: 3,
        primary_source_count: 3,
        extracted_content_count: 0,
        query_count: 3,
        source_urls: ["https://docs.example.test/navigation"],
        gate_results: [{
          gate_id: "substantive_content_threshold",
          label: "Substantive extracted content",
          status: "fail",
          detail: "0 distinct sources passed.",
        }],
        failed_gates: ["substantive_content_threshold"],
        query_provenance: [{
          query: "API response structure",
          status: "complete",
          provider: "Athena Benchmark Fixture",
          delegated_agent_id: "search.scout",
          result_count: 1,
          retrieval_status: "complete",
          delegation_task_id: "fixture-1",
        }],
      },
      checks: { status_correct: true, completion_correct: true },
    }],
  };
}

test("renders measured metrics, fixture limitations, failed gates and Scout provenance", () => {
  const html = render(sampleReport());
  assert.match(html, /SYNTHETIC FIXTURE RUN/);
  assert.match(html, /0 live provider calls/);
  assert.match(html, /STATUS ACCURACY/);
  assert.match(html, /Substantive extracted content/);
  assert.match(html, /SCOUT DELEGATION TRACE/);
  assert.match(html, /search\.scout/);
  assert.match(html, /docs\.example\.test/);
  assert.match(html, /CRITICAL GATE · PASS/);
});

test("escapes provider-controlled and source strings instead of creating executable markup", () => {
  const unsafe = sampleReport();
  unsafe.cases[0].actual.query_provenance[0].provider = '<img src=x onerror="alert(1)">';
  unsafe.cases[0].actual.source_urls = ["<script>alert(1)</script>"];
  const html = render(unsafe);
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;img src=x/);
  assert.match(html, /&lt;script&gt;/);
});

test("keeps source fixture URLs as inert text, not verified clickable citations", () => {
  const html = render(sampleReport());
  assert.match(html, /SOURCE URL FIXTURES · CLASSIFICATION ONLY/);
  assert.doesNotMatch(html, /<a\s/i);
});

test("renders an explicit unavailable state for malformed benchmark responses", () => {
  assert.match(render({}), /Evidence report unavailable/);
});