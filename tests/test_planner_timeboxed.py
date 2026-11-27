"""Planner: duration-based goals produce one phase per period."""
from __future__ import annotations

from agents.planner_agent.agent import PlannerAgent


def test_four_week_goal_gets_four_weekly_phases():
    steps = PlannerAgent._timeboxed_steps("Prepare for the SAT in a 4-week study plan")
    titles = [s for s in steps if s.startswith("Week")]
    assert [t.split(":")[0] for t in titles] == ["Week 1", "Week 2", "Week 3", "Week 4"]
    assert steps[0].startswith("Baseline")
    assert steps[-1].startswith("Final review")


def test_no_duration_means_no_timeboxed_plan():
    assert PlannerAgent._timeboxed_steps("Make a plan to learn Python") == []


def test_out_of_range_duration_is_ignored():
    assert PlannerAgent._timeboxed_steps("A 1-week sprint") == []
    assert PlannerAgent._timeboxed_steps("A 40-week program") == []
