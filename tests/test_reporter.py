"""
Unit Tests for MinionQA Reporter & Rich Visualization Module.

Tests report compilation, assertion matrix formatting, visual artifact linking,
and OpenMinion tool invocation.
"""

import os
from pathlib import Path
import pytest

from minion_qa.config import default_config
from minion_qa.tools.assertions import (
    AssertionResult,
    record_assertion,
    clear_assertion_history,
)
from minion_qa.tools.browser import get_browser_session
from minion_qa.tools.reporter import (
    compile_markdown_report,
    print_rich_summary,
    generate_qa_audit_report,
)


@pytest.fixture(autouse=True)
def setup_teardown():
    clear_assertion_history()
    browser = get_browser_session()
    browser.step_history.clear()
    browser.captured_screenshots.clear()
    browser.console_logs.clear()
    yield
    clear_assertion_history()
    browser.step_history.clear()
    browser.captured_screenshots.clear()
    browser.console_logs.clear()


def test_compile_markdown_report_passed():
    record_assertion(
        AssertionResult(
            passed=True,
            assertion_type="price_equals",
            target="item total",
            expected_value="$29.99",
            actual_value="$29.99",
            detail="Item total matches $29.99 exactly",
        )
    )

    md = compile_markdown_report(
        test_name="E-Commerce Checkout Flow",
        run_summary="User added product to cart and verified payment breakdown.",
        status="PASSED",
        session_id="test-session-001",
        target_url="https://example.com/checkout",
    )

    assert "# MinionQA Audit Report: E-Commerce Checkout Flow" in md
    assert "✅ PASSED" in md
    assert "test-session-001" in md
    assert "https://example.com/checkout" in md
    assert "price_equals" in md
    assert "Item total matches $29.99 exactly" in md
    assert "Cortensor Ecosystem Relevance" in md


def test_compile_markdown_report_failed_by_assertion():
    record_assertion(
        AssertionResult(
            passed=False,
            assertion_type="price_total_matches",
            target="total calculation",
            expected_value="sum is 32.39",
            actual_value="total was 40.00",
            detail="Subtotal + Tax does not match displayed Total",
        )
    )

    # Even if status is passed, failed assertions force verdict to FAILED
    md = compile_markdown_report(
        test_name="Math Integrity Check",
        run_summary="Found pricing calculation anomaly.",
        status="PASSED",
        session_id="test-session-002",
        target_url="https://example.com/cart",
    )

    assert "❌ FAILED" in md
    assert "Subtotal + Tax does not match displayed Total" in md


def test_compile_markdown_report_with_browser_state():
    browser = get_browser_session()
    browser.step_history.append({
        "action": "click",
        "selector": "button#checkout",
        "value": "",
        "status": "success",
        "duration": 0.12,
    })
    browser.captured_screenshots.append({
        "filename": "step1.png",
        "path": "screenshots/step1.png",
        "url": "https://example.com/checkout",
        "is_failure": False,
    })
    browser.captured_screenshots.append({
        "filename": "FAIL_error.png",
        "path": "screenshots/FAIL_error.png",
        "url": "https://example.com/checkout",
        "is_failure": True,
    })
    browser.console_logs.append({
        "type": "error",
        "text": "Uncaught TypeError: Cannot read properties of null",
    })

    md = compile_markdown_report(
        test_name="Trace & Diagnostics Test",
        run_summary="Checking trace table and diagnostic logs.",
        status="FAILED",
    )

    assert "button#checkout" in md
    assert "step1.png" in md
    assert "FAIL_error.png" in md
    assert "⚠️ Failure Point" in md
    assert "Uncaught TypeError" in md


def test_print_rich_summary():
    record_assertion(
        AssertionResult(
            passed=True,
            assertion_type="page_contains_text",
            target="Welcome",
            expected_value="Welcome",
            actual_value="Welcome",
            detail="Header found",
        )
    )
    # Should run cleanly without raising exceptions
    print_rich_summary("Sample Test", "PASSED")
    print_rich_summary("Sample Test Fail", "FAILED")


def test_generate_qa_audit_report_tool(tmp_path):
    record_assertion(
        AssertionResult(
            passed=True,
            assertion_type="url_contains",
            target="cart",
            expected_value="cart",
            actual_value="https://example.com/cart",
            detail="URL verified",
        )
    )

    result_msg = generate_qa_audit_report(
        run_summary="Autonomous run finished cleanly.",
        status="PASSED",
    )

    assert "[REPORT_GENERATED]" in result_msg
    assert "Verdict: PASSED" in result_msg
