import pytest

from evaluation.athena_benchmark import run_benchmark


@pytest.fixture(scope="module")
def benchmark_report():
    return run_benchmark()


def test_athena_benchmark_cases_match_their_expected_outcomes(benchmark_report):
    cases = benchmark_report["cases"]
    assert len(cases) == 10
    assert all(case["checks"]["status_correct"] for case in cases)
    assert all(case["checks"]["completion_correct"] for case in cases)


def test_athena_benchmark_has_no_false_completion(benchmark_report):
    metrics = benchmark_report["metrics"]
    assert metrics["false_completion_count"] == 0
    assert metrics["false_completion_rate"] == 0.0
    assert metrics["critical_gate"] == "PASS"


def test_athena_benchmark_measures_expected_failure_modes(benchmark_report):
    by_id = {case["case_id"]: case for case in benchmark_report["cases"]}

    assert by_id["tavily_missing_response_structure"]["actual"]["technical_coverage_complete"] is False
    assert by_id["tavily_navigation_only"]["actual"]["extracted_content_count"] == 0
    assert by_id["tavily_irrelevant_official_integrations"]["actual"]["extracted_content_count"] == 0
    assert by_id["tavily_duplicate_source_cannot_meet_source_threshold"]["actual"]["primary_source_count"] == 1
    assert by_id["tavily_partial_provider_retrieval"]["actual"]["research_status"] == "partial"
    assert by_id["all_queries_fail_closed"]["actual"]["complete"] is False
    assert by_id["fastapi_official_sources_remain_topic_scoped"]["actual"]["complete"] is True
    assert by_id["tavily_lookalike_domains_are_not_primary"]["actual"]["primary_source_count"] == 0


def test_athena_benchmark_records_scout_provenance_and_declares_fixture_limits(benchmark_report):
    metadata = benchmark_report["benchmark"]
    assert metadata["execution_mode"] == "deterministic_synthetic_fixtures"
    assert metadata["provider_calls"] == 0
    assert "synthetic" in metadata["fixture_warning"].casefold()

    for case in benchmark_report["cases"]:
        assert case["actual"]["query_count"] == 3
        assert case["actual"]["provenance_count"] == 3
        assert case["actual"]["delegated_agents"] == ["search.scout"]
        assert case["actual"]["providers"] == ["Athena Benchmark Fixture"]


def test_athena_benchmark_metrics_are_bounded_and_reproducible(benchmark_report):
    metrics = benchmark_report["metrics"]
    for key in (
        "status_accuracy",
        "completion_accuracy",
        "completion_precision",
        "completion_recall",
        "false_completion_rate",
        "technical_coverage_accuracy",
    ):
        assert 0.0 <= metrics[key] <= 1.0
    assert metrics["status_accuracy"] == 1.0
    assert metrics["completion_accuracy"] == 1.0
    assert metrics["technical_coverage_accuracy"] == 1.0
