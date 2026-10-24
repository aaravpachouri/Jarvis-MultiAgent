from agents.research_agent.agent import ResearchAgent


def test_build_queries_splits_release_notes_and_pypi_metadata():
    prompt = (
        "Use Athena to research the official FastAPI release notes and PyPI package metadata "
        "through Scout and Tavily. Return relevant source URLs and snippets marked UNVERIFIED "
        "until directly checked. If sufficient primary-source evidence is available, delegate "
        "to Forge only to create files. Never simulate execution."
    )
    queries = ResearchAgent._build_queries(prompt)
    assert any("FastAPI" in query and "release notes" in query.casefold() for query in queries)
    assert any("FastAPI" in query and "PyPI" in query for query in queries)
    assert all("delegate" not in query.casefold() for query in queries)
    assert all("never simulate" not in query.casefold() for query in queries)
    assert len(queries) <= 3
    assert all(len(query) < 180 for query in queries)


def test_build_queries_does_not_repeat_full_multistep_prompt():
    prompt = "Research official Python documentation. Return URLs. Then edit files, run tests, and report the exit code."
    queries = ResearchAgent._build_queries(prompt)
    assert queries
    assert all("run tests" not in query.casefold() for query in queries)


def test_build_queries_removes_generic_downstream_steps():
    prompt = "Research official Python documentation. Return URLs. Then edit files, run tests, and report the exit code."
    queries = ResearchAgent._build_queries(prompt)
    assert queries
    assert all("run tests" not in query.casefold() for query in queries)
    assert all("edit files" not in query.casefold() for query in queries)
    assert all("report the exit code" not in query.casefold() for query in queries)
    assert any("Python documentation" in query for query in queries)


def test_build_queries_removes_orchestration_text_and_targets_primary_sources():
    prompt = (
        "Research the official FastAPI release notes and the package's PyPI metadata.\n"
        "Delegate the research to Scout using Tavily where available.\n"
        "Preserve the three separate Scout research delegations and gather enough evidence to determine "
        "the package's current release information.\n"
        "If the evidence is sufficient, delegate implementation only to Forge.\n"
        "Do not delegate to Atlas."
    )
    queries = ResearchAgent._build_queries(prompt)
    assert len(queries) == 3
    assert "release notes" in queries[0].casefold()
    assert "site:fastapi.tiangolo.com" in queries[0]
    assert "FastAPI" in queries[1] and "PyPI" in queries[1]
    assert "site:pypi.org/project/fastapi" in queries[1]
    assert "site:github.com/fastapi/fastapi" in queries[2]
    assert all(len(query) < 180 for query in queries)
    assert all(not any(term in query.casefold() for term in (
        "preserve", "gather enough evidence", "delegate", "forge", "atlas", "do not delegate"
    )) for query in queries)


def test_build_queries_rejects_a_standalone_orchestration_instruction():
    assert ResearchAgent._build_queries("Preserve three separate Scout research delegations") == []
