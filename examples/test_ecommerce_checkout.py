"""
End-to-End E-Commerce Checkout & Revenue Flow Validation Test.

Demonstrates MinionQA running headlessly with OpenMinion 0.1 primitives:
1. Headless Playwright browser automation
2. Dynamic DOM interaction and state navigation
3. Deterministic revenue-flow validation (item prices, sales tax, grand total)
4. Order placement confirmation
5. Automatic visual failure screenshot capture on anomalies
6. Full audit report generation exported to examples/sample_run_report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minion_qa.agent import MinionQAAgent
from minion_qa.config import MinionQAConfig


def build_ecommerce_checkout_flow(simulate_failure: bool = False) -> list[dict]:
    """Construct the step-by-step test sequence for SwagLabs checkout."""
    steps = [
        # --- Authentication ---
        {
            "type": "action",
            "action": "fill",
            "selector": "#user-name",
            "value": "standard_user",
            "description": "Enter test customer username",
        },
        {
            "type": "action",
            "action": "fill",
            "selector": "#password",
            "value": "secret_sauce",
            "description": "Enter customer credentials",
        },
        {
            "type": "action",
            "action": "click",
            "selector": "#login-button",
            "description": "Click Login button",
        },
        # Verify authenticated catalog state
        {
            "type": "assertion",
            "assertion_type": "url_contains",
            "target": "inventory.html",
            "description": "Assert redirect to product inventory catalog",
        },
        {
            "type": "assertion",
            "assertion_type": "page_contains_text",
            "target": "Sauce Labs Backpack",
            "description": "Assert target catalog item is visible in DOM",
        },
        # --- Cart Addition ---
        {
            "type": "action",
            "action": "click",
            "selector": "#add-to-cart-sauce-labs-backpack",
            "description": "Add Sauce Labs Backpack ($29.99) to cart",
        },
        {
            "type": "action",
            "action": "click",
            "selector": ".shopping_cart_link",
            "description": "Navigate to shopping cart",
        },
        {
            "type": "assertion",
            "assertion_type": "url_contains",
            "target": "cart.html",
            "description": "Assert current URL is cart page",
        },
        # --- Checkout Information ---
        {
            "type": "action",
            "action": "click",
            "selector": "#checkout",
            "description": "Proceed to checkout flow",
        },
        {
            "type": "action",
            "action": "fill",
            "selector": "#first-name",
            "value": "Cortensor",
            "description": "Input customer First Name",
        },
        {
            "type": "action",
            "action": "fill",
            "selector": "#last-name",
            "value": "Validator",
            "description": "Input customer Last Name",
        },
        {
            "type": "action",
            "action": "fill",
            "selector": "#postal-code",
            "value": "94107",
            "description": "Input billing Postal Code",
        },
        {
            "type": "action",
            "action": "click",
            "selector": "#continue",
            "description": "Continue to financial overview",
        },
        # --- Revenue Flow Validation ---
        {
            "type": "assertion",
            "assertion_type": "url_contains",
            "target": "checkout-step-two.html",
            "description": "Assert arrival at payment overview screen",
        },
        {
            "type": "assertion",
            "assertion_type": "page_contains_text",
            "target": "Payment Information",
            "description": "Assert payment terms displayed",
        },
        {
            "type": "assertion",
            "assertion_type": "price_equals",
            "target": "Item total: $29.99",
            "expected": "29.99",
            "description": "Verify item base subtotal equals $29.99",
        },
        {
            "type": "assertion",
            "assertion_type": "price_total_matches",
            "target": "29.99, 2.40, 0.00",
            "expected": "32.39",
            "description": "Verify subtotal ($29.99) + tax ($2.40) exactly matches total ($32.39)",
        },
    ]

    if simulate_failure:
        # Deliberate failure step to verify failure handling & screenshot capture
        steps.append({
            "type": "assertion",
            "assertion_type": "price_equals",
            "target": "Total: $32.39",
            "expected": "999.99",
            "description": "Simulated financial mismatch assertion (expected $999.99 vs actual $32.39)",
        })

    # --- Order Finalization ---
    steps.extend([
        {
            "type": "action",
            "action": "click",
            "selector": "#finish",
            "description": "Click Finish to submit transaction",
        },
        {
            "type": "assertion",
            "assertion_type": "url_contains",
            "target": "checkout-complete.html",
            "description": "Assert redirect to checkout completion receipt",
        },
        {
            "type": "assertion",
            "assertion_type": "page_contains_text",
            "target": "Thank you for your order!",
            "description": "Verify order receipt confirmation banner",
        },
    ])

    return steps


def run_test(
    simulate_failure: bool = False,
    headless: bool = True,
    report_path: str = "examples/sample_run_report.md",
) -> bool:
    """Run the e-commerce validation test."""
    cfg = MinionQAConfig(
        headless=headless,
        screenshot_dir=Path("./screenshots"),
        reports_dir=Path("./reports"),
    )
    agent = MinionQAAgent(config=cfg)

    test_steps = build_ecommerce_checkout_flow(simulate_failure=simulate_failure)
    test_title = (
        "E-Commerce Checkout & Revenue Flow Validation [Simulated Failure]"
        if simulate_failure
        else "E-Commerce Checkout & Revenue Flow Validation"
    )

    try:
        result = agent.run_flow(
            name=test_title,
            target_url="https://www.saucedemo.com",
            steps=test_steps,
            report_output_path=report_path,
        )
        return result["status"] == "PASSED"
    finally:
        agent.close()


def test_ecommerce_checkout():
    """Pytest entrypoint for e-commerce checkout flow."""
    assert run_test() is True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MinionQA e-commerce checkout test.")
    parser.add_argument(
        "--simulate-failure",
        action="store_true",
        help="Inject a failing price assertion to verify failure screenshot capture.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Chromium in headed mode for visual debugging.",
    )
    parser.add_argument(
        "--report",
        type=str,
        default="examples/sample_run_report.md",
        help="Output path for the generated audit report markdown.",
    )
    args = parser.parse_args()

    passed = run_test(
        simulate_failure=args.simulate_failure,
        headless=not args.headed,
        report_path=args.report,
    )
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
