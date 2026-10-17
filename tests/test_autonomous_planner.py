"""
Unit and Integration tests for MinionQA Autonomous Goal Planner.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from minion_qa.autonomous_planner import (
    AutonomousGoalPlanner,
    AutonomousPlanResult,
    PlannerStepTrace,
)
from minion_qa.config import MinionQAConfig


def test_planner_json_decision_parsing():
    """Verify that the planner parses clean JSON decisions."""
    planner = AutonomousGoalPlanner()

    raw_json = json.dumps({
        "thought": "Click the checkout button to proceed",
        "action": "click",
        "selector": "#checkout",
        "value": "",
    })

    decision = planner._parse_llm_decision(raw_json)
    assert decision["action"] == "click"
    assert decision["selector"] == "#checkout"
    assert "Click the checkout button" in decision["thought"]


def test_planner_markdown_wrapped_json_parsing():
    """Verify that the planner strips markdown fences and surrounding prose."""
    planner = AutonomousGoalPlanner()

    wrapped_text = """Here is my decision:
```json
{
  "thought": "Enter standard username",
  "action": "fill",
  "selector": "#user-name",
  "value": "standard_user"
}
```
Hope this helps!"""

    decision = planner._parse_llm_decision(wrapped_text)
    assert decision["action"] == "fill"
    assert decision["selector"] == "#user-name"
    assert decision["value"] == "standard_user"


def test_planner_self_healing_selector():
    """Verify that the planner auto-heals brittle selectors from DOM snapshots."""
    planner = AutonomousGoalPlanner()

    sample_dom = """
    BUTTON [data-test="add-to-cart-sauce-labs-backpack"] : "Add to cart"
    INPUT [id="user-name"] : ""
    A [class="shopping_cart_link"] : ""
    """

    # The agent attempted a brittle selector like '.btn-cart-backpack'
    healed = planner._self_heal_selector(".btn-cart-backpack", sample_dom, "click")
    assert healed == '[data-test="add-to-cart-sauce-labs-backpack"]'


def test_planner_step_trace_dataclass():
    """Verify PlannerStepTrace fields and status values."""
    trace = PlannerStepTrace(
        step_number=1,
        thought="Navigating to home page",
        action="navigate",
        value="https://example.com",
        status="PASSED",
        duration_s=0.45,
    )
    assert trace.step_number == 1
    assert trace.status == "PASSED"
    assert trace.duration_s == 0.45


def test_planner_mocked_execution_flow():
    """Verify that execute_goal runs through multiple turns until 'finish'."""
    planner = AutonomousGoalPlanner(max_turns=5)

    mock_decisions = [
        json.dumps({
            "thought": "Page loaded. Verify title.",
            "action": "assert_text",
            "selector": "",
            "value": "",
            "assertion_type": "page_contains_text",
            "expected": "Example Domain",
        }),
        json.dumps({
            "thought": "Everything verified. Goal complete.",
            "action": "finish",
            "selector": "",
            "value": "",
        }),
    ]

    with patch.object(planner, "call_llm", side_effect=mock_decisions), \
         patch("minion_qa.autonomous_planner.navigate_and_snapshot") as mock_nav, \
         patch("minion_qa.autonomous_planner.verify_assertion") as mock_assert, \
         patch("minion_qa.autonomous_planner.get_browser_session") as mock_get_browser:

        mock_nav.return_value = {
            "url": "https://example.com",
            "title": "Example Domain",
            "elements_summary": 'H1: "Example Domain"\nP: "This domain is for use in illustrative examples"',
        }
        mock_assert.return_value = "[PASSED] page_contains_text on body"
        mock_browser = MagicMock()
        mock_browser.navigate.return_value = {
            "url": "https://example.com",
            "title": "Example Domain",
            "error": None,
        }
        mock_browser.get_compact_dom.return_value = {
            "url": "https://example.com",
            "title": "Example Domain",
            "elements_summary": 'H1: "Example Domain"',
        }
        mock_browser.captured_screenshots = []
        mock_get_browser.return_value = mock_browser

        res = planner.execute_goal(
            goal="Verify the Example Domain page title",
            start_url="https://example.com",
        )

        assert res.status == "PASSED"
        assert res.total_steps == 2
        assert len(res.traces) == 2
        assert res.traces[0].action == "assert_text"
        assert res.traces[1].action == "finish"
