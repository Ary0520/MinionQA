"""
Deterministic Assertion Engine (Test Oracle) for MinionQA.

Adapted from Leaka AI (backend/app/assertions.py & schemas.py) into an
OpenMinion @tool with enhanced e-commerce and revenue-flow checks.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from openminion import tool


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class AssertionResult(BaseModel):
    """Result of an evaluated assertion."""
    assertion_type: str
    target: str
    expected_value: str
    actual_value: Optional[str] = None
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return self.model_dump() if hasattr(self, "model_dump") else self.dict()


# Session assertion tracker to store results during an agent run
_session_assertion_history: List[AssertionResult] = []


def get_assertion_history() -> List[AssertionResult]:
    """Retrieve all assertions evaluated during the current session."""
    return list(_session_assertion_history)


def clear_assertion_history() -> None:
    """Clear the session assertion history."""
    _session_assertion_history.clear()


def record_assertion(result: AssertionResult) -> None:
    """Record an assertion result into the session history."""
    _session_assertion_history.append(result)



# ---------------------------------------------------------------------------
# HTML -> visible text extractor (from Leaka AI)
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Collect visible text while skipping non-user-facing tags."""

    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def extract_visible_text(dom_html: Optional[str]) -> str:
    """Strip tags/scripts/styles from raw HTML and return normalized visible text."""
    if not dom_html:
        return ""
    try:
        parser = _TextExtractor()
        parser.feed(dom_html)
        return parser.get_text()
    except Exception:
        # Fallback: regex tag stripper
        return re.sub(r"<[^>]+>", " ", dom_html)


def _normalize_url(u: str) -> str:
    return (u or "").strip().rstrip("/").lower()


def _parse_currency_amount(val: str) -> Optional[float]:
    """Extract float value from currency string (e.g. '$129.99 USD' -> 129.99)."""
    if not val:
        return None
    cleaned = re.sub(r"[^\d.-]", "", val)
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Core Assertion Evaluator
# ---------------------------------------------------------------------------
def evaluate_assertion(
    assertion_type: str,
    target: str,
    expected_value: str = "",
    *,
    visible_text: str = "",
    current_url: str = "",
    dom_html: str = "",
) -> AssertionResult:
    """
    Deterministically evaluates an assertion against actual browser state.
    Never crashes — reports malformed or failing assertions cleanly.
    """
    a_type = (assertion_type or "").strip().lower()
    target_clean = (target or "").strip()
    exp_clean = (expected_value or "").strip()

    result = AssertionResult(
        assertion_type=a_type,
        target=target_clean,
        expected_value=exp_clean,
        passed=False,
        detail="",
    )

    try:
        if a_type == "page_contains_text":
            raw_query = (target_clean or exp_clean).strip('\'"')
            norm_query = re.sub(r"\s+", " ", raw_query.lower()).strip()
            norm_visible = re.sub(r"\s+", " ", (visible_text or "").lower())
            norm_dom = re.sub(r"\s+", " ", (dom_html or "").lower())
            passed = (norm_query in norm_visible) or (norm_query in norm_dom)
            result.passed = passed
            result.detail = (
                f"Found text '{raw_query}' in page content."
                if passed
                else f"Text '{raw_query}' was NOT found in visible page content."
            )

        elif a_type == "page_not_contains_text":
            raw_query = target_clean.strip('\'"')
            norm_query = re.sub(r"\s+", " ", raw_query.lower()).strip()
            norm_visible = re.sub(r"\s+", " ", (visible_text or "").lower())
            passed = norm_query not in norm_visible
            result.passed = passed
            result.detail = (
                f"Text '{raw_query}' is correctly absent from page."
                if passed
                else f"Text '{raw_query}' was found on page but was expected to be absent."
            )

        elif a_type == "url_contains":
            passed = target_clean.lower() in (current_url or "").lower()
            result.actual_value = current_url
            result.passed = passed
            result.detail = (
                f"Current URL '{current_url}' contains '{target_clean}'."
                if passed
                else f"Current URL '{current_url}' does NOT contain '{target_clean}'."
            )

        elif a_type == "url_equals":
            passed = _normalize_url(current_url) == _normalize_url(target_clean)
            result.actual_value = current_url
            result.passed = passed
            result.detail = (
                f"Current URL '{current_url}' equals target."
                if passed
                else f"Current URL '{current_url}' does not equal '{target_clean}'."
            )

        elif a_type == "page_contains_regex":
            match = re.search(target_clean, visible_text, re.IGNORECASE)
            result.passed = match is not None
            if match:
                result.actual_value = match.group(0)
                result.detail = f"Page text matches regex pattern /{target_clean}/ (matched: '{match.group(0)}')."
            else:
                result.detail = f"Page text does NOT match regex pattern /{target_clean}/."

        elif a_type in ("price_equals", "revenue_amount_matches"):
            # Target is the text or locator value; expected_value is the numerical amount
            actual_amt = _parse_currency_amount(target_clean)
            expected_amt = _parse_currency_amount(exp_clean)

            if actual_amt is None or expected_amt is None:
                # Try finding expected amount in visible text if target is a label
                if expected_amt is not None and target_clean.lower() in visible_text.lower():
                    # Attempt to extract price near the label
                    pattern = rf"{re.escape(target_clean)}[^\d]*(\$?\s*[\d,]+\.?\d*)"
                    m = re.search(pattern, visible_text, re.IGNORECASE)
                    if m:
                        actual_amt = _parse_currency_amount(m.group(1))

            if actual_amt is not None and expected_amt is not None:
                passed = abs(actual_amt - expected_amt) < 0.01
                result.actual_value = f"${actual_amt:.2f}"
                result.passed = passed
                result.detail = (
                    f"Price validation passed: ${actual_amt:.2f} matches expected ${expected_amt:.2f}."
                    if passed
                    else f"Price mismatch: actual ${actual_amt:.2f} vs expected ${expected_amt:.2f}."
                )
            else:
                result.detail = f"Could not parse monetary amounts from target='{target_clean}' and expected='{exp_clean}'."

        elif a_type == "price_total_matches":
            # Format: target is "subtotal,tax,discount" and expected_value is "total"
            parts = [p.strip() for p in target_clean.split(",")]
            if len(parts) >= 2:
                subtotal = _parse_currency_amount(parts[0]) or 0.0
                tax = _parse_currency_amount(parts[1]) if len(parts) > 1 else 0.0
                discount = _parse_currency_amount(parts[2]) if len(parts) > 2 else 0.0
                computed_total = round(subtotal + tax - discount, 2)
                exp_total = _parse_currency_amount(exp_clean) or 0.0
                passed = abs(computed_total - exp_total) < 0.01
                result.actual_value = f"${computed_total:.2f}"
                result.passed = passed
                result.detail = (
                    f"Revenue total matches calculation: Subtotal (${subtotal:.2f}) + Tax (${tax:.2f}) - Discount (${discount:.2f}) = ${computed_total:.2f} (matches ${exp_total:.2f})."
                    if passed
                    else f"Revenue calculation mismatch: computed ${computed_total:.2f} != expected ${exp_total:.2f}."
                )
            else:
                result.detail = "Invalid format for price_total_matches: target must be 'subtotal,tax[,discount]'."

        elif a_type == "element_present":
            # Check presence in DOM
            passed = target_clean in dom_html or f'id="{target_clean}"' in dom_html or f'data-testid="{target_clean}"' in dom_html
            result.passed = passed
            result.detail = (
                f"Element '{target_clean}' found in page DOM."
                if passed
                else f"Element '{target_clean}' not found in page DOM."
            )

        else:
            result.detail = f"Unknown assertion type '{a_type}'."

    except Exception as exc:
        result.detail = f"Assertion evaluation error: {exc}"

    _session_assertion_history.append(result)
    return result


# ---------------------------------------------------------------------------
# OpenMinion @tool implementation
# ---------------------------------------------------------------------------
@tool(
    name="verify_assertion",
    description=(
        "Verifies a deterministic QA assertion (page_contains_text, page_not_contains_text, "
        "url_contains, url_equals, page_contains_regex, price_equals, price_total_matches, "
        "element_present) against the active browser state."
    ),
    tags=("qa", "assertion", "test-oracle"),
    idempotent=True,
)
def verify_assertion(assertion_type: str, target: str, expected_value: str = "") -> str:
    """
    OpenMinion tool callable by the agent during test execution.
    Inspects the active Playwright browser session state and records the verdict.
    """
    from minion_qa.tools.browser import get_browser_session

    browser = get_browser_session()
    visible_text = browser.get_visible_text()
    current_url = browser.get_current_url()
    dom_html = browser.get_dom_html()

    res = evaluate_assertion(
        assertion_type=assertion_type,
        target=target,
        expected_value=expected_value,
        visible_text=visible_text,
        current_url=current_url,
        dom_html=dom_html,
    )

    if not res.passed:
        browser.take_screenshot(f"fail_assert_{res.assertion_type}", is_failure=True)

    status_str = "PASSED" if res.passed else "FAILED"
    return f"[{status_str}] Assertion '{res.assertion_type}': {res.detail}"
