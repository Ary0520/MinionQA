"""
QA Audit Reporter & Rich Terminal Visualization for MinionQA.

Compiles steps, screenshots, deterministic assertions, and OpenMinion session
metadata into a structured markdown audit report and interactive Rich console UI.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from openminion import tool

from minion_qa.config import default_config
from minion_qa.tools.assertions import get_assertion_history
from minion_qa.tools.browser import get_browser_session

console = Console()


def compile_markdown_report(
    test_name: str,
    run_summary: str,
    status: str,
    session_id: Optional[str] = None,
    target_url: Optional[str] = None,
) -> str:
    """Generate comprehensive markdown test audit report."""
    browser = get_browser_session()
    assertions = get_assertion_history()
    screenshots = browser.captured_screenshots
    steps = browser.step_history
    console_errors = browser.get_console_errors()

    all_assertions_passed = all(a.passed for a in assertions) if assertions else True
    final_verdict = "PASSED" if (status.upper() == "PASSED" and all_assertions_passed) else "FAILED"
    verdict_badge = "✅ PASSED" if final_verdict == "PASSED" else "❌ FAILED"

    lines = [
        f"# MinionQA Audit Report: {test_name}",
        "",
        f"**Test Verdict:** {verdict_badge}  ",
        f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**OpenMinion Session ID:** `{session_id or 'auto-session'}`  ",
        f"**Target URL:** `{target_url or browser.get_current_url()}`  ",
        f"**Runtime Engine:** OpenMinion 0.1 (Cortensor Mainnet Full Alpha)  ",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        f"{run_summary.strip()}",
        "",
        "---",
        "",
        "## 2. Deterministic Assertion Matrix (Test Oracle)",
    ]

    if assertions:
        lines.extend([
            "| Assertion Type | Target / Criteria | Expected | Actual | Verdict | Details |",
            "|---|---|---|---|---|---|",
        ])
        for a in assertions:
            v_icon = "✅ PASS" if a.passed else "❌ FAIL"
            lines.append(
                f"| `{a.assertion_type}` | `{a.target[:35]}` | `{a.expected_value or '-'}` | "
                f"`{a.actual_value or '-'}` | **{v_icon}** | {a.detail} |"
            )
    else:
        lines.append("*No assertions recorded for this run.*")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Browser Execution Trace",
    ])

    if steps:
        lines.extend([
            "| # | Action | Selector / Target | Value | Status | Duration |",
            "|---|---|---|---|---|---|",
        ])
        for idx, s in enumerate(steps, 1):
            s_status = "✅ OK" if s.get("success", s.get("status") == "success") else "❌ ERR"
            lines.append(
                f"| {idx} | `{s.get('action')}` | `{str(s.get('selector') or s.get('target') or '')[:30]}` | "
                f"`{str(s.get('value', ''))[:20]}` | {s_status} | {s.get('duration', '-')}s |"
            )
    else:
        lines.append("*No execution steps recorded.*")

    lines.extend([
        "",
        "---",
        "",
        "## 4. Visual Evidence & Artifacts",
    ])

    if screenshots:
        lines.extend([
            "| Type | Filename | Path | Target URL |",
            "|---|---|---|---|",
        ])
        for shot in screenshots:
            kind = "⚠️ Failure Point" if shot.get("is_failure") else "📸 State Snapshot"
            lines.append(
                f"| {kind} | `{shot.get('filename')}` | `{shot.get('path')}` | `{shot.get('url')[:40]}` |"
            )
    else:
        lines.append("*No screenshots captured.*")

    if console_errors:
        lines.extend([
            "",
            "---",
            "",
            "## 5. Console & Network Diagnostics",
            "```text",
        ])
        for err in console_errors:
            lines.append(f"[{err.get('type')}] {err.get('text')}")
        lines.append("```")

    lines.extend([
        "",
        "---",
        "",
        "## 6. Cortensor Ecosystem Relevance",
        "- **dApp Health Validation:** Continuous headless verification for decentralized payment flows, token checkouts, and Web3 frontends.",
        "- **State Integrity:** Deterministic assertion checks prevent silent UI breakage or incorrect pricing logic.",
        "- **Proof of Execution:** OpenMinion session hashes and saved visual artifacts provide verifiable audit logs.",
    ])

    return "\n".join(lines)


def print_rich_summary(test_name: str, status: str, report_path: Optional[str] = None) -> None:
    """Render beautiful terminal UI using Rich."""
    browser = get_browser_session()
    assertions = get_assertion_history()
    screenshots = browser.captured_screenshots

    all_passed = all(a.passed for a in assertions) if assertions else True
    is_success = (status.upper() == "PASSED" and all_passed)
    badge_style = "bold green" if is_success else "bold red"
    verdict_text = "PASSED" if is_success else "FAILED"

    # Panel header
    title_text = Text(f"MinionQA Test Execution: {test_name}", style="bold cyan")
    body_text = Text()
    body_text.append("Final Verdict: ", style="bold")
    body_text.append(f" {verdict_text} \n", style=badge_style)
    body_text.append(f"Target URL: {browser.get_current_url()}\n", style="dim")
    if report_path:
        body_text.append(f"Audit Report: {report_path}\n", style="italic underline yellow")

    console.print(Panel(body_text, title=title_text, expand=False, border_style="cyan"))

    # Assertions Table
    if assertions:
        table = Table(title="Assertion Verification Matrix", show_header=True, header_style="bold magenta")
        table.add_column("Type", style="cyan", width=22)
        table.add_column("Expected", width=18)
        table.add_column("Verdict", width=10, justify="center")
        table.add_column("Details", style="dim")

        for a in assertions:
            v_style = "bold green" if a.passed else "bold red"
            v_sym = "[PASS]" if a.passed else "[FAIL]"
            table.add_row(
                a.assertion_type,
                a.expected_value or a.target[:16],
                Text(v_sym, style=v_style),
                a.detail,
            )
        console.print(table)

    # Screenshots Table
    if screenshots:
        s_table = Table(title="Captured Visual Proof", show_header=True, header_style="bold blue")
        s_table.add_column("Type", width=16)
        s_table.add_column("Path", style="dim")
        for s in screenshots:
            stype = "[bold red]Failure Point[/bold red]" if s.get("is_failure") else "[green]Snapshot[/green]"
            s_table.add_row(stype, s.get("path"))
        console.print(s_table)


# ---------------------------------------------------------------------------
# OpenMinion @tool definition
# ---------------------------------------------------------------------------
@tool(
    name="generate_qa_audit_report",
    description=(
        "Compiles QA test execution steps, captured screenshots, assertion results, "
        "and final test verdict into an audit report and saves it to disk."
    ),
    tags=("reporting", "audit"),
    idempotent=True,
)
def generate_qa_audit_report(run_summary: str, status: str) -> str:
    """OpenMinion tool: Generates QA test report."""
    report_content = compile_markdown_report(
        test_name="Autonomous QA Validation Run",
        run_summary=run_summary,
        status=status,
        session_id=default_config.session_id,
    )

    filename = f"qa_audit_report_{int(time.time())}.md"
    out_path = default_config.reports_dir / filename
    out_path.write_text(report_content, encoding="utf-8")

    # Render summary to Rich console
    print_rich_summary(test_name="Autonomous QA Validation Run", status=status, report_path=str(out_path))

    return (
        f"[REPORT_GENERATED] Audit report successfully written to '{out_path}'. "
        f"Verdict: {status.upper()}."
    )
