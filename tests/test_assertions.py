"""
Unit Tests for MinionQA Deterministic Assertion Engine (Test Oracle).

Verifies that assertions evaluate mathematically and semantically without
relying on the LLM, preventing false positives and hallucinations.
"""

import pytest
from minion_qa.tools.assertions import (
    evaluate_assertion,
    clear_assertion_history,
    get_assertion_history,
)


@pytest.fixture(autouse=True)
def clean_history():
    clear_assertion_history()
    yield
    clear_assertion_history()


def test_page_contains_text():
    sample_text = "Welcome to Swag Labs. Special 20% discount on Sauce Labs Backpack!"
    
    # Passing check
    res_pass = evaluate_assertion("page_contains_text", "Sauce Labs Backpack", visible_text=sample_text)
    assert res_pass.passed is True
    assert "Found text" in res_pass.detail

    # Failing check
    res_fail = evaluate_assertion("page_contains_text", "Out of Stock", visible_text=sample_text)
    assert res_fail.passed is False
    assert "NOT found" in res_fail.detail


def test_url_assertions():
    url = "https://www.saucedemo.com/checkout-step-two.html?session=abc"
    
    # url_contains
    res_contains = evaluate_assertion("url_contains", "checkout-step-two.html", current_url=url)
    assert res_contains.passed is True

    # url_equals (normalized trailing slash / case)
    res_eq = evaluate_assertion("url_equals", url, current_url=url)
    assert res_eq.passed is True

    # url_equals mismatch
    res_neq = evaluate_assertion("url_equals", "https://www.saucedemo.com/cart.html", current_url=url)
    assert res_neq.passed is False


def test_page_contains_regex():
    sample_text = "Invoice #INV-2026-9812 confirmed for $149.50 USD."
    
    res = evaluate_assertion("page_contains_regex", r"INV-\d{4}-\d{4}", visible_text=sample_text)
    assert res.passed is True
    assert res.actual_value == "INV-2026-9812"

    res_fail = evaluate_assertion("page_contains_regex", r"TXN-[A-Z]{5}", visible_text=sample_text)
    assert res_fail.passed is False


def test_price_equals_validation():
    # Direct price matching
    res = evaluate_assertion("price_equals", "$29.99", "29.99")
    assert res.passed is True

    # Floating point precision matching with currency symbols
    res_tax = evaluate_assertion("price_equals", "Tax: $2.40 USD", "2.40")
    assert res_tax.passed is True

    # Deliberate mismatch
    res_mismatch = evaluate_assertion("price_equals", "$32.39", "999.99")
    assert res_mismatch.passed is False
    assert "Price mismatch" in res_mismatch.detail


def test_price_total_matches_revenue_formula():
    """
    Validates revenue calculation: Subtotal + Tax - Discount == Total.
    Format: target = "subtotal, tax, discount", expected = "total"
    """
    # 29.99 + 2.40 - 0.00 = 32.39
    res = evaluate_assertion("price_total_matches", "29.99, 2.40, 0.00", "32.39")
    assert res.passed is True
    assert "Revenue total matches calculation" in res.detail

    # With discount: 100.00 + 8.00 - 20.00 = 88.00
    res_disc = evaluate_assertion("price_total_matches", "100.00, 8.00, 20.00", "88.00")
    assert res_disc.passed is True

    # Math discrepancy caught
    res_bad_math = evaluate_assertion("price_total_matches", "29.99, 2.40, 0.00", "30.00")
    assert res_bad_math.passed is False
    assert "Revenue calculation mismatch" in res_bad_math.detail


def test_element_presence():
    dom = '<div id="checkout-summary"><button data-testid="finish-btn">Finish</button></div>'
    
    assert evaluate_assertion("element_present", "finish-btn", dom_html=dom).passed is True
    assert evaluate_assertion("element_present", "checkout-summary", dom_html=dom).passed is True
    assert evaluate_assertion("element_present", "non-existent-modal", dom_html=dom).passed is False


def test_session_history_tracking():
    clear_assertion_history()
    evaluate_assertion("page_contains_text", "foo", visible_text="foo bar")
    evaluate_assertion("page_contains_text", "baz", visible_text="foo bar")
    
    history = get_assertion_history()
    assert len(history) == 2
    assert history[0].passed is True
    assert history[1].passed is False
