"""
Unit Tests for MinionQA Browser Automation & DOM Snapshotting Tool.

Tests headless Chromium lifecycle, interactive element extraction, action execution,
screenshot generation, and OpenMinion policy gating.
"""

from pathlib import Path
import pytest

from minion_qa.config import MinionQAConfig
from minion_qa.tools.browser import (
    BrowserSession,
    get_browser_session,
    reset_browser_session,
    navigate_and_snapshot,
    execute_action,
)


@pytest.fixture(autouse=True)
def cleanup_browser():
    reset_browser_session()
    yield
    reset_browser_session()


HTML_FIXTURE = """data:text/html,
<!DOCTYPE html>
<html>
<head><title>MinionQA Sandbox Test</title></head>
<body>
    <h1>Checkout Portal</h1>
    <input id="coupon-field" placeholder="Enter Promo Code" value="" />
    <button id="submit-btn" onclick="document.getElementById('status').innerText = 'Applied Promo';">Apply Promo</button>
    <div id="status">No Promo</div>
    <div class="price" id="subtotal-val">$99.00</div>
</body>
</html>
"""


def test_browser_navigation_and_dom_extraction():
    session = get_browser_session(headless=True)
    snapshot = session.navigate(HTML_FIXTURE)

    assert "Checkout Portal" in snapshot["visible_text_excerpt"]
    assert snapshot["title"] == "MinionQA Sandbox Test"
    assert len(snapshot["interactive_elements"]) >= 2

    # Check extracted interactive elements
    tags = [e["tag"] for e in snapshot["interactive_elements"]]
    assert "input" in tags
    assert "button" in tags


def test_browser_action_execution():
    session = get_browser_session(headless=True)
    session.navigate(HTML_FIXTURE)

    # Fill input field
    fill_res = session.execute_action("fill", "#coupon-field", "CORTENSOR20")
    assert fill_res["success"] is True

    # Click button
    click_res = session.execute_action("click", "#submit-btn")
    assert click_res["success"] is True

    # Verify DOM update
    assert "Applied Promo" in session.get_visible_text()


def test_screenshot_generation_and_storage(tmp_path):
    session = get_browser_session(headless=True)
    session.navigate(HTML_FIXTURE)

    shot_path = session.take_screenshot("test_snapshot")
    assert shot_path != ""
    assert Path(shot_path).exists()


def test_openminion_tool_decorators():
    """Verify OpenMinion tool declarations and metadata."""
    assert hasattr(navigate_and_snapshot, "tool_decl")
    assert navigate_and_snapshot.tool_decl.name == "navigate_and_snapshot"

    assert hasattr(execute_action, "tool_decl")
    assert execute_action.tool_decl.name == "execute_action"


def test_action_policy_gating():
    """Verify that sensitive destructive actions are intercepted by OpenMinion policy."""
    session = get_browser_session(headless=True)
    session.navigate(HTML_FIXTURE)

    # Attempt sensitive action
    result = execute_action("click", "#pay-real-card-submit", "transfer-real-funds")
    assert "[POLICY_DENIED]" in result
    assert "Intercepted by OpenMinion Action Policy Gating" in result
