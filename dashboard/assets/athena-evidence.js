(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.JARVISAthenaEvidence = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);

  const titleCase = value => String(value || "unnamed scenario")
    .replace(/_/g, " ")
    .replace(/\b\w/g, char => char.toUpperCase());

  const percent = value => Number.isFinite(Number(value))
    ? (Number(value) * 100).toFixed(1) + "%"
    : "—";

  const gateClass = status => ({
    pass: "is-pass",
    fail: "is-fail",
    not_evaluated: "is-neutral"
  })[status] || "is-neutral";

  const gateLabel = status => ({
    pass: "PASS",
    fail: "FAILED",
    not_evaluated: "NOT EVALUATED"
  })[status] || String(status || "UNKNOWN").toUpperCase();

  function render(report) {
    const metadata = report && report.benchmark;
    const metrics = report && report.metrics;
    const cases = Array.isArray(report && report.cases) ? report.cases : [];
    if (!metadata || !metrics || !cases.length) {
      return '<div class="athena-error"><strong>Evidence report unavailable</strong><p>The benchmark response did not contain the expected metadata, metrics and scenario records.</p></div>';
    }

    const criticalPass = metrics.critical_gate === "PASS";
    const metric = (label, value, detail) =>
      '<article class="athena-metric"><span>' + escapeHTML(label) + '</span><strong>' +
      escapeHTML(value) + '</strong>' + (detail ? '<small>' + escapeHTML(detail) + '</small>' : "") +
      '</article>';

    const scenarioCards = cases.map(item => {
      const actual = item.actual || {};
      const checks = item.checks || {};
      const gates = Array.isArray(actual.gate_results) ? actual.gate_results : [];
      const provenance = Array.isArray(actual.query_provenance) ? actual.query_provenance : [];
      const sources = Array.isArray(actual.source_urls) ? actual.source_urls : [];
      const failed = gates.filter(gate => gate.status === "fail");
      const statusCorrect = checks.status_correct === true && checks.completion_correct === true;

      const gateMarkup = gates.length
        ? gates.map(gate =>
          '<div class="athena-gate-row"><span class="athena-gate-dot ' + gateClass(gate.status) +
          '"></span><div><strong>' + escapeHTML(gate.label || gate.gate_id) +
          '</strong><small>' + escapeHTML(gate.detail || "") +
          '</small></div><b class="' + gateClass(gate.status) + '">' +
          escapeHTML(gateLabel(gate.status)) + '</b></div>'
        ).join("")
        : '<p class="athena-muted">No gate diagnostics were returned for this scenario.</p>';

      const provenanceMarkup = provenance.length
        ? provenance.map(entry =>
          '<div class="athena-trace-row"><span class="athena-trace-state ' +
          (entry.status === "complete" ? "is-pass" : "is-fail") + '">' +
          escapeHTML(String(entry.status || "unknown").toUpperCase()) +
          '</span><div><strong>' + escapeHTML(entry.delegated_agent_id || "search.scout") +
          ' → ' + escapeHTML(entry.provider || "provider not recorded") +
          '</strong><small>' + escapeHTML(entry.query || "") +
          '</small><small>' + escapeHTML(entry.result_count ?? 0) +
          ' result(s) · ' + escapeHTML(entry.retrieval_status || "unknown") +
          (entry.delegation_task_id ? ' · ' + escapeHTML(entry.delegation_task_id) : "") +
          '</small>' + (entry.error ? '<small class="athena-error-text">' + escapeHTML(entry.error) + '</small>' : "") +
          '</div></div>'
        ).join("")
        : '<p class="athena-muted">No query-level provenance was returned.</p>';

      const sourceMarkup = sources.length
        ? '<div class="athena-source-list">' +
          sources.map(url => '<code>' + escapeHTML(url) + '</code>').join("") +
          '</div>'
        : '<p class="athena-muted">No distinct source URLs were returned.</p>';

      const gateSummary = failed.length
        ? '<span class="athena-case-failure">' + failed.length + ' failed gate' +
          (failed.length === 1 ? "" : "s") + '</span>'
        : '<span class="athena-case-pass">No failed evidence gates</span>';

      return '<article class="athena-case">' +
        '<div class="athena-case-top"><div><span class="athena-case-index">' +
        escapeHTML(item.case_id || "scenario") + '</span><h4>' +
        escapeHTML(titleCase(item.case_id)) + '</h4></div><span class="athena-case-result ' +
        (statusCorrect ? "is-pass" : "is-fail") + '">' +
        (statusCorrect ? "LABEL MATCH" : "LABEL MISMATCH") + '</span></div>' +
        '<div class="athena-case-stats">' +
        '<span><b>' + escapeHTML(actual.source_count ?? 0) + '</b> distinct</span>' +
        '<span><b>' + escapeHTML(actual.primary_source_count ?? 0) + '</b> primary</span>' +
        '<span><b>' + escapeHTML(actual.extracted_content_count ?? 0) + '</b> extracted</span>' +
        '<span><b>' + escapeHTML(actual.query_count ?? 0) + '</b> queries</span></div>' +
        '<div class="athena-case-bottom"><span>STATUS · ' +
        escapeHTML(String(actual.research_status || "unknown").replace(/_/g, " ").toUpperCase()) +
        '</span>' + gateSummary + '</div>' +
        '<details class="athena-case-details"><summary>Inspect evidence gates, sources and delegation</summary>' +
        '<section><h5>EVIDENCE GATES</h5>' + gateMarkup + '</section>' +
        '<section><h5>SCOUT DELEGATION TRACE</h5>' + provenanceMarkup + '</section>' +
        '<section><h5>SOURCE URL FIXTURES · CLASSIFICATION ONLY</h5>' + sourceMarkup + '</section>' +
        '</details></article>';
    }).join("");

    const runTime = metadata.created_at ? new Date(metadata.created_at) : null;
    const runLabel = runTime && !Number.isNaN(runTime.getTime())
      ? runTime.toLocaleString()
      : "Timestamp unavailable";
    const warning = metadata.fixture_warning ||
      "Synthetic fixture data; no live provider evaluation is implied.";

    return '<section class="athena-lab" aria-label="Athena Evidence Lab benchmark">' +
      '<div class="athena-lab-hero"><div class="athena-lab-hero-copy">' +
      '<span class="athena-lab-eyebrow">RESEARCH RELIABILITY / BENCHMARK ' +
      escapeHTML(metadata.version || "—") + '</span><h3>Evidence quality, made inspectable.</h3>' +
      '<p>Ten deterministic scenarios exercise the real Athena ResearchAgent through a scripted Scout context.</p>' +
      '</div><span class="athena-critical ' + (criticalPass ? "is-pass" : "is-fail") + '">' +
      (criticalPass ? "CRITICAL GATE · PASS" : "CRITICAL GATE · FAIL") + '</span></div>' +
      '<div class="athena-fixture-warning"><strong>SYNTHETIC FIXTURE RUN</strong><span>' +
      escapeHTML(warning) + '</span><small>' +
      escapeHTML(metadata.provider_calls ?? 0) + ' live provider calls · ' +
      escapeHTML(metadata.python_version || "Python version unavailable") + ' · ' +
      escapeHTML(runLabel) + '</small></div>' +
      '<div class="athena-metric-grid">' +
      metric("STATUS ACCURACY", percent(metrics.status_accuracy), (metrics.scenario_count ?? cases.length) + " labelled scenarios") +
      metric("COMPLETION ACCURACY", percent(metrics.completion_accuracy), "") +
      metric("COMPLETION PRECISION", percent(metrics.completion_precision), "") +
      metric("COMPLETION RECALL", percent(metrics.completion_recall), "") +
      metric("FALSE COMPLETIONS", String(metrics.false_completion_count ?? "—"), percent(metrics.false_completion_rate) + " of incomplete cases") +
      metric("TECHNICAL COVERAGE", percent(metrics.technical_coverage_accuracy), (metrics.technical_coverage_case_count ?? "—") + " evaluated cases") +
      '</div><div class="athena-section-heading"><div><span>SCENARIO INSPECTION</span>' +
      '<h3>Where Athena passes — and where it must stop.</h3></div>' +
      '<button type="button" id="athena-benchmark-refresh" class="athena-refresh">RERUN ↻</button></div>' +
      '<div class="athena-case-list">' + scenarioCards + '</div>' +
      '<footer class="athena-lab-footer"><span>MODE · ' +
      escapeHTML(metadata.execution_mode || "unknown") + '</span><span>REVISION · ' +
      escapeHTML(metadata.git_revision || "runtime revision not supplied") +
      '</span></footer></section>';
  }

  return { render, escapeHTML };
});