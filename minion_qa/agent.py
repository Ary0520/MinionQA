"""
MinionQA Autonomous QA & Revenue-Flow Validation Agent.

Natively powered by OpenMinion 0.1 Python SDK primitives:
- openminion.Agent orchestration
- @tool decorated function execution
- Session lifecycle & policy gating
- Cortensor router/portal integration
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openminion import Agent, tool
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from minion_qa.config import MinionQAConfig, default_config
from minion_qa.tools.assertions import (
    clear_assertion_history,
    get_assertion_history,
    verify_assertion,
)
from minion_qa.tools.browser import (
    execute_action,
    get_browser_session,
    navigate_and_snapshot,
    reset_browser_session,
)
from minion_qa.tools.reporter import (
    compile_markdown_report,
    generate_qa_audit_report,
    print_rich_summary,
)

console = Console()

QA_SYSTEM_PROMPT = """You are MinionQA, an autonomous web QA and revenue-flow validation agent for Cortensor Hackathon #5.
Your mission is to rigorously test web application workflows, e-commerce checkouts, and dApp payment portals.

OPERATIONAL INSTRUCTIONS:
1. Navigate to target URL using `navigate_and_snapshot(url)`.
2. Inspect the interactive elements returned in the snapshot.
3. Perform required user actions (clicks, inputs, selections) using `execute_action(action_type, selector, value)`.
4. Validate page text, state, prices, and revenue calculations using `verify_assertion(assertion_type, target, expected_value)`.
   - Use `price_equals` to verify individual line items.
   - Use `price_total_matches` with target="subtotal,tax,discount" and expected_value="total" for financial validation.
5. If any step or assertion fails, capture a visual screenshot and report the failure.
6. Once complete, compile and finalize the run with `generate_qa_audit_report(run_summary, status)`.
"""


class MinionQAAgent:
    """
    MinionQA Autonomous Agent Runtime.
    Encapsulates OpenMinion Agent instance, registered tools, and session management.
    """

    def __init__(
        self,
        config: Optional[MinionQAConfig] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.config = config or default_config
        self.session_id = session_id or self.config.session_id or f"session_{uuid4().hex[:12]}"
        self.config.session_id = self.session_id

        # Register tools natively with OpenMinion 0.1 Agent
        self.tools = [
            navigate_and_snapshot,
            execute_action,
            verify_assertion,
            generate_qa_audit_report,
        ]

        self.agent = Agent(
            name="minion-qa",
            instructions=QA_SYSTEM_PROMPT,
            tools=self.tools,
            session_id=self.session_id,
        )

    def __enter__(self) -> "MinionQAAgent":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def run_flow(
        self,
        name: str,
        target_url: str,
        steps: List[Dict[str, Any]],
        report_output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute an autonomous QA flow deterministically using OpenMinion's tools.
        Ensures zero-friction local and CI execution without requiring remote API keys.
        """
        clear_assertion_history()
        start_time = time.time()
        browser = get_browser_session(headless=self.config.headless)

        console.print(
            Panel(
                Text(
                    f"MinionQA Starting Flow: {name}\n"
                    f"Target URL: {target_url}\n"
                    f"Session ID: {self.session_id}\n"
                    f"Runtime: OpenMinion 0.1 (Cortensor Alpha)",
                    style="bold cyan",
                ),
                border_style="cyan",
            )
        )

        flow_passed = True
        summary_notes: List[str] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:

            # Step 1: Initial Navigation
            task = progress.add_task(f"[yellow]Navigating to {target_url}...", total=None)
            nav_result = navigate_and_snapshot(target_url)
            progress.remove_task(task)
            summary_notes.append(f"Navigated to {target_url}")

            # Step 2: Iterate through steps
            for idx, step in enumerate(steps, 1):
                stype = step.get("type", "action")
                desc = step.get("description", f"Step {idx}")
                
                task = progress.add_task(f"[cyan]Step {idx}: {desc}...", total=None)

                if stype == "navigate":
                    url = step.get("url", target_url)
                    nav_result = navigate_and_snapshot(url)
                    summary_notes.append(f"Step {idx}: Navigated to {url}")

                elif stype == "action":
                    action = step.get("action", "click")
                    selector = step.get("selector", "")
                    value = step.get("value", "")
                    res = execute_action(action, selector, value)
                    if "[FAILED]" in res:
                        flow_passed = False
                        summary_notes.append(f"Step {idx} FAILED: {res}")
                    else:
                        summary_notes.append(f"Step {idx} OK: {action} on {selector}")

                elif stype == "assertion":
                    a_type = step.get("assertion_type", "page_contains_text")
                    target = step.get("target", "")
                    expected = step.get("expected", "")
                    res = verify_assertion(a_type, target, expected)
                    if "[FAILED]" in res:
                        flow_passed = False
                        summary_notes.append(f"Step {idx} Assertion FAILED: {res}")
                    else:
                        summary_notes.append(f"Step {idx} Assertion PASSED: {target}")

                progress.remove_task(task)

        # Check all assertion verdicts
        assertions = get_assertion_history()
        all_passed = all(a.passed for a in assertions) if assertions else True
        final_status = "PASSED" if (flow_passed and all_passed) else "FAILED"

        duration = round(time.time() - start_time, 2)
        exec_summary = (
            f"Executed flow '{name}' across {len(steps)} steps in {duration}s. "
            f"Total assertions: {len(assertions)}, Passed: {sum(1 for a in assertions if a.passed)}. "
            f"Final verdict: {final_status}."
        )

        # Compile report
        report_md = compile_markdown_report(
            test_name=name,
            run_summary=exec_summary,
            status=final_status,
            session_id=self.session_id,
            target_url=target_url,
        )

        if report_output_path:
            out_file = report_output_path
        else:
            filename = f"qa_report_{int(time.time())}.md"
            out_file = str(self.config.reports_dir / filename)

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report_md)

        # Terminal Rich output
        print_rich_summary(test_name=name, status=final_status, report_path=out_file)

        return {
            "status": final_status,
            "duration": duration,
            "session_id": self.session_id,
            "assertions_count": len(assertions),
            "assertions_passed": sum(1 for a in assertions if a.passed),
            "report_path": out_file,
            "screenshots": browser.captured_screenshots,
        }

    def close(self) -> None:
        """Clean up active browser and agent session."""
        try:
            if hasattr(self, "agent") and self.agent is not None:
                self.agent.close()
        except Exception:
            pass
        reset_browser_session()


def main() -> None:
    """CLI entrypoint for minion-qa."""
    parser = argparse.ArgumentParser(
        description="MinionQA: Autonomous Web QA & Revenue-Flow Validation Agent (OpenMinion 0.1)"
    )
    parser.add_argument("--url", "-u", type=str, default="https://example.com", help="Target URL to test")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode (default: headless)")
    parser.add_argument("--session-id", "-s", type=str, default=None, help="OpenMinion session ID")
    parser.add_argument("--report", "-r", type=str, default=None, help="Output path for audit report")
    args = parser.parse_args()

    cfg = MinionQAConfig(headless=not args.headed)
    agent = MinionQAAgent(config=cfg, session_id=args.session_id)

    try:
        sample_steps = [
            {"type": "action", "action": "wait", "value": "1.0", "description": "Wait for page render"},
            {"type": "assertion", "assertion_type": "page_contains_text", "target": "Example", "description": "Verify page title text"},
            {"type": "assertion", "assertion_type": "url_contains", "target": "example.com", "description": "Verify domain URL"},
        ]
        result = agent.run_flow(
            name="Smoke Test Verification",
            target_url=args.url,
            steps=sample_steps,
            report_output_path=args.report,
        )
        sys.exit(0 if result["status"] == "PASSED" else 1)
    finally:
        agent.close()


if __name__ == "__main__":
    main()
