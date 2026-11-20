from __future__ import annotations

from core.bootstrap import discover_manifests


def test_every_registered_agent_has_a_complete_operator_guide():
    discovered = discover_manifests()
    assert len(discovered) == 10

    for _folder, spec in discovered:
        guide = spec.metadata.get("operator_guide")
        assert isinstance(guide, dict), f"{spec.agent_id} is missing operator_guide"
        functions = guide.get("functions")
        assert isinstance(functions, list) and functions, f"{spec.agent_id} has no documented functions"
        documented = [item.get("capability") for item in functions]
        assert len(documented) == len(set(documented)), f"{spec.agent_id} has duplicate function docs"
        assert set(documented) == set(spec.capabilities), (
            f"{spec.agent_id} guide must document every capability exactly once"
        )
        assert all(isinstance(item.get("description"), str) and item["description"].strip() for item in functions)
        assert isinstance(guide.get("best_for"), list) and guide["best_for"]
        assert isinstance(guide.get("boundaries"), list) and guide["boundaries"]


def test_profile_only_agents_are_explicit_about_non_execution():
    by_id = {spec.agent_id: spec for _folder, spec in discover_manifests()}
    for agent_id in ("systems.atlas", "deployment.launch"):
        guide = by_id[agent_id].metadata["operator_guide"]
        text = " ".join(guide["boundaries"]).casefold()
        assert "read-only" in text or "does not" in text
        assert "does not" in text or "cannot" in text


def test_quant_operator_guide_preserves_read_only_trading_boundary():
    by_id = {spec.agent_id: spec for _folder, spec in discover_manifests()}
    quant = by_id["trading.quant"]
    text = " ".join(quant.metadata["operator_guide"]["boundaries"]).casefold()
    assert "never places trades" in text
    assert "timestamps" in text or "stale" in text
