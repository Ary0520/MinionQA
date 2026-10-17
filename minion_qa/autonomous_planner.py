"""
MinionQA Autonomous Goal Planning & Execution Engine.

Ports enterprise-grade autonomous QA patterns from Leaka AI into OpenMinion:
- Natural-language goal understanding & step-by-step reasoning
- Compact DOM snapshot inspection (<450 tokens)
- Ranked locator hierarchy & self-healing selector fallback
- Deterministic financial/revenue validation oracle
- Multi-provider inference support (OpenRouter / Cortensor / OpenAI)
- Real-time step telemetry and visual failure proof capture
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

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
from minion_qa.tools.reporter import compile_markdown_report, print_rich_summary

logger = logging.getLogger("minion_qa.autonomous_planner")
console = Console()

SYSTEM_PLANNER_PROMPT = """You are MinionQA's Autonomous Web QA Planner.
Your role is to autonomously test web applications and dApps to verify functional workflows, checkout funnels, and revenue calculations.

Given:
1. USER GOAL: High-level test objective.
2. CURRENT URL & PAGE TITLE.
3. INTERACTIVE DOM ELEMENTS: List of interactable elements currently visible on the page.
4. ACTION HISTORY: Steps already completed.

You must decide the single NEXT action to take. Respond with a STRICT JSON OBJECT (no markdown wrap, no backticks, no extra text):
{
  "thought": "Brief explanation of what you see and why this action progresses toward the goal",
  "action": "click | fill | select | navigate | wait | assert_text | assert_price | assert_total | finish | fail",
  "selector": "The CSS selector or text identifier for the target element (or empty if not applicable)",
  "value": "Value to input or select (or URL for navigate, or wait seconds)",
  "assertion_type": "page_contains_text | price_equals | price_total_matches | url_contains (only for assert actions)",
  "expected": "Expected value to verify (only for assert actions)"
}

ACTION RULES:
- "click": Click a button, link, or tab. Use selector like '[data-test="add-to-cart"]' or '#checkout' or text.
- "fill": Enter text into an input or textarea. Specify "selector" and "value".
- "navigate": Go to a new URL. Specify "value" as the URL.
- "wait": Wait for page/network settle. Specify "value" in seconds (e.g. "1.5").
- "assert_text": Verify that text appears on the page. Specify "expected" with the text to check.
- "assert_price": Verify an item price. Specify "selector" and "expected" (e.g. "$29.99").
- "assert_total": Verify financial math. Specify "selector" with "subtotal,tax,discount" and "expected" with total.
- "finish": The goal has been completely and successfully achieved. Specify "thought" with a summary.
- "fail": An unrecoverable bug or missing feature was encountered. Specify "thought" with the reason.

CRITICAL EXPLORATION RULES:
- Do NOT invent or guess random external URLs (e.g. do NOT guess https://example.com/login or /actual-login).
- Always inspect the INTERACTIVE DOM ELEMENTS currently visible on the page! If you see a 'Log In' link or button (e.g. `a:has-text("Log In")`), click it!
- Do not repeatedly navigate to the same URL. If an action fails or the target cannot be reached, call "fail" with the explanation.
- Always use the selectors given in the INTERACTIVE DOM ELEMENTS list whenever possible.
"""


@dataclass
class PlannerStepTrace:
    """Trace record for an individual autonomous action step."""

    step_number: int
    thought: str
    action: str
    selector: str = ""
    value: str = ""
    result: str = ""
    status: str = "PASSED"  # PASSED | FAILED | HEALED
    duration_s: float = 0.0
    screenshot_path: Optional[str] = None


@dataclass
class AutonomousPlanResult:
    """Consolidated outcome of an autonomous QA run."""

    goal: str
    target_url: str
    status: str  # PASSED | FAILED | ERROR
    total_steps: int
    duration_s: float
    session_id: str
    provider_used: str
    model_used: str
    traces: List[PlannerStepTrace] = field(default_factory=list)
    assertions_count: int = 0
    assertions_passed: int = 0
    report_path: Optional[str] = None
    screenshots: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


class AutonomousGoalPlanner:
    """
    Autonomous multi-turn QA Planner.
    Inspects live DOM states, queries the configured LLM engine, self-heals selectors,
    and executes deterministic financial validations.
    """

    def __init__(
        self,
        config: Optional[MinionQAConfig] = None,
        session_id: Optional[str] = None,
        max_turns: int = 15,
    ) -> None:
        self.config = config or default_config
        self.session_id = session_id or self.config.session_id or f"auto_{uuid4().hex[:10]}"
        self.config.session_id = self.session_id
        self.max_turns = max_turns

    def call_llm(self, messages: List[Dict[str, str]]) -> str:
        """
        Dispatch prompt to the configured LLM provider.
        Supports OpenRouter, Cortensor Portal, and OpenAI.
        """
        provider = (self.config.provider or "openrouter").lower()

        if provider == "cortensor" and self.config.cortensor_api_key:
            endpoint = f"{self.config.cortensor_portal_url.rstrip('/')}/chat/completions"
            api_key = self.config.cortensor_api_key
            model = self.config.model or "oss-20b"
        elif provider == "openai" and self.config.openai_api_key:
            endpoint = "https://api.openai.com/v1/chat/completions"
            api_key = self.config.openai_api_key
            model = self.config.model or "gpt-4o-mini"
        else:
            # Default: OpenRouter
            endpoint = "https://openrouter.ai/api/v1/chat/completions"
            api_key = self.config.openrouter_api_key or os.getenv("OPENROUTER_API_KEY", "")
            model = self.config.model or "openai/gpt-4o-mini"

        if not api_key:
            raise ValueError(
                f"No API key configured for provider '{provider}'. "
                f"Please set OPENROUTER_API_KEY or CORTENSOR_API_KEY in your .env file."
            )

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 512,
        }

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Ary0520/MinionQA",
                "X-Title": "MinionQA Autonomous Agent",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                res_body = response.read().decode("utf-8")
                data = json.loads(res_body)
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_content = e.read().decode("utf-8") if e.fp else str(e)
            logger.error(f"LLM API request failed with code {e.code}: {err_content}")
            raise RuntimeError(f"LLM Provider ({provider}) HTTP {e.code}: {err_content}") from e
        except Exception as e:
            logger.error(f"LLM API network error: {e}")
            raise RuntimeError(f"LLM network error on {provider}: {e}") from e

    def _parse_llm_decision(self, raw_reply: str) -> Dict[str, Any]:
        """Defensively extract structured JSON action decision from LLM text."""
        cleaned = raw_reply.strip()
        # Remove markdown codeblock fences if returned
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        # Extract balanced JSON substring if surrounded by prose
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning(f"Could not parse JSON decision: {cleaned}")
            return {
                "thought": f"Failed to parse LLM JSON: {exc}",
                "action": "fail",
                "selector": "",
                "value": "",
            }

    def _self_heal_selector(
        self,
        selector: str,
        dom_snapshot: str,
        action: str,
    ) -> Optional[str]:
        """
        Leaka AI-inspired Ranked Locator Self-Healing:
        If a selector failed, scans DOM snapshot for candidates matching
        purpose-built test IDs, IDs, names, or role/text.
        """
        if not selector:
            return None

        # Extract alphanumeric search tokens (e.g. "btn-cart-backpack" -> ["btn", "cart", "backpack"])
        clean_target = selector.replace("[", " ").replace("]", " ").replace('"', " ").replace("'", " ")
        terms = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", clean_target) if t.lower() not in {"btn", "button", "input"}]
        if not terms:
            terms = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", clean_target)]
        if not terms:
            return None

        primary_term = terms[-1]

        # Check DOM snapshot for candidates containing the term
        for line in dom_snapshot.splitlines():
            line_str = line.strip()
            if primary_term in line_str.lower():
                # Try to extract data-test or id or tag
                test_match = re.search(r'data-test="([^"]+)"', line_str)
                if test_match:
                    return f'[data-test="{test_match.group(1)}"]'

                testid_match = re.search(r'data-testid="([^"]+)"', line_str)
                if testid_match:
                    return f'[data-testid="{testid_match.group(1)}"]'

                id_match = re.search(r'id="([^"]+)"', line_str)
                if id_match:
                    return f'#{id_match.group(1)}'

                name_match = re.search(r'name="([^"]+)"', line_str)
                if name_match:
                    return f'[name="{name_match.group(1)}"]'

        return None

    def execute_goal(
        self,
        goal: str,
        start_url: str,
        on_step_callback: Optional[Callable[[PlannerStepTrace], None]] = None,
    ) -> AutonomousPlanResult:
        """
        Main autonomous loop:
        Repeatedly inspects page state, asks LLM for next action, executes,
        heals locators if broken, and verifies assertions.
        """
        clear_assertion_history()
        start_time = time.time()

        # Isolate session: reset browser to ensure clean page and no cross-site state pollution
        reset_browser_session()
        browser = get_browser_session(headless=self.config.headless)

        # Normalize start_url
        clean_url = start_url.strip()
        if not clean_url.startswith(("http://", "https://", "about:", "data:", "file://")):
            if clean_url.startswith("localhost") or clean_url.startswith("127.0.0.1"):
                clean_url = f"http://{clean_url}"
            else:
                clean_url = f"https://{clean_url}"

        console.print(
            Panel(
                Text(
                    f"MinionQA Autonomous Goal Execution\n"
                    f"Goal: {goal}\n"
                    f"Start URL: {clean_url}\n"
                    f"Engine: {self.config.provider.upper()} ({self.config.model})\n"
                    f"Session ID: {self.session_id}",
                    style="bold green",
                ),
                border_style="green",
            )
        )

        traces: List[PlannerStepTrace] = []
        history_prompts: List[Dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PLANNER_PROMPT}
        ]

        # Step 0: Initial Navigation
        nav_res = browser.navigate(clean_url)
        if nav_res.get("error") and isinstance(nav_res.get("error"), str):
            fail_shot = browser.capture_failure_screenshot("fail_initial_nav.png")
            err_detail = f"Could not navigate to target URL '{clean_url}': {nav_res.get('error')}"
            fail_trace = PlannerStepTrace(
                step_number=1,
                thought=f"Attempted initial navigation to {clean_url}",
                action="navigate",
                selector=clean_url,
                value=clean_url,
                result=f"[FAILED] {err_detail}",
                status="FAILED",
                duration_s=round(time.time() - start_time, 2),
                screenshot_path=fail_shot,
            )
            traces.append(fail_trace)
            if on_step_callback:
                on_step_callback(fail_trace)

            report_md = compile_markdown_report(
                test_name=f"Autonomous Goal: {goal[:40]}...",
                run_summary=f"Autonomous run failed on initial navigation to {clean_url}: {err_detail}",
                status="FAILED",
                session_id=self.session_id,
                target_url=clean_url,
            )
            out_filename = f"autonomous_qa_{int(time.time())}.md"
            out_path = str(self.config.reports_dir / out_filename)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(report_md)

            return AutonomousPlanResult(
                goal=goal,
                target_url=clean_url,
                status="FAILED",
                total_steps=1,
                duration_s=round(time.time() - start_time, 2),
                session_id=self.session_id,
                provider_used=self.config.provider,
                model_used=self.config.model,
                traces=traces,
                assertions_count=0,
                assertions_passed=0,
                report_path=out_path,
                screenshots=browser.captured_screenshots,
                error_message=err_detail,
            )

        initial_snapshot = browser.get_compact_dom()
        current_dom = initial_snapshot.get("elements_summary", "")
        current_url = initial_snapshot.get("url", clean_url)
        current_title = initial_snapshot.get("title", "")

        is_finished = False
        final_status = "PASSED"
        error_msg = None

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:

            for turn in range(1, self.max_turns + 1):
                turn_start = time.time()
                task = progress.add_task(f"[cyan]Turn {turn}/{self.max_turns}: Reasoning...", total=None)

                # Construct turn observation
                action_history_summary = "\n".join(
                    [
                        f"Step {t.step_number}: {t.action}(selector='{t.selector}', value='{t.value}') -> {t.status}: {t.result}"
                        for t in traces[-4:]  # Last 4 steps for context
                    ]
                ) or "None (starting on initial page)"

                turn_prompt = (
                    f"USER GOAL: {goal}\n\n"
                    f"CURRENT URL: {current_url}\n"
                    f"PAGE TITLE: {current_title}\n\n"
                    f"PREVIOUS ACTIONS:\n{action_history_summary}\n\n"
                    f"INTERACTIVE DOM ELEMENTS (Compacted):\n{current_dom}\n\n"
                    f"What is the single NEXT action to take? Respond ONLY with JSON."
                )

                history_prompts.append({"role": "user", "content": turn_prompt})

                try:
                    llm_reply = self.call_llm(history_prompts)
                    history_prompts.append({"role": "assistant", "content": llm_reply})
                    decision = self._parse_llm_decision(llm_reply)
                except Exception as e:
                    progress.remove_task(task)
                    final_status = "ERROR"
                    error_msg = f"LLM turn failed: {e}"
                    logger.error(error_msg)
                    break

                thought = decision.get("thought", "Executing next step")
                action = decision.get("action", "wait").lower()
                selector = decision.get("selector", "")
                value = decision.get("value", "")
                assertion_type = decision.get("assertion_type", "page_contains_text")
                expected = decision.get("expected", "")

                progress.update(task, description=f"[yellow]Turn {turn}: {action} ({selector or value})...")

                step_trace = PlannerStepTrace(
                    step_number=turn,
                    thought=thought,
                    action=action,
                    selector=selector,
                    value=value,
                )

                # --- 1. Terminal Actions ---
                if action == "finish":
                    step_trace.result = f"Goal achieved: {thought}"
                    step_trace.status = "PASSED"
                    step_trace.duration_s = round(time.time() - turn_start, 2)
                    traces.append(step_trace)
                    if on_step_callback:
                        on_step_callback(step_trace)
                    progress.remove_task(task)
                    is_finished = True
                    break

                if action == "fail":
                    step_trace.result = f"Goal failed: {thought}"
                    step_trace.status = "FAILED"
                    final_status = "FAILED"
                    error_msg = thought
                    # Capture failure screenshot
                    fail_shot = browser.capture_failure_screenshot(f"fail_turn_{turn}.png")
                    step_trace.screenshot_path = fail_shot
                    step_trace.duration_s = round(time.time() - turn_start, 2)
                    traces.append(step_trace)
                    if on_step_callback:
                        on_step_callback(step_trace)
                    progress.remove_task(task)
                    break

                # --- 2. Action Execution with Self-Healing ---
                step_status = "PASSED"
                step_result = ""

                if action == "navigate":
                    target = value or selector or start_url
                    res = navigate_and_snapshot(target)
                    step_result = f"Navigated to {target}"

                elif action in ("click", "fill", "select", "wait"):
                    res = execute_action(action, selector, value)
                    if "[FAILED]" in res:
                        # Attempt Leaka AI-style Self-Healing
                        healed_selector = self._self_heal_selector(selector, current_dom, action)
                        if healed_selector and healed_selector != selector:
                            logger.info(f"Self-healing selector from '{selector}' to '{healed_selector}'")
                            res_healed = execute_action(action, healed_selector, value)
                            if "[FAILED]" not in res_healed:
                                step_status = "HEALED"
                                step_result = f"Auto-healed selector: {res_healed}"
                                selector = healed_selector
                            else:
                                step_status = "FAILED"
                                step_result = f"Action failed after self-heal attempt: {res_healed}"
                        else:
                            step_status = "FAILED"
                            step_result = res
                    else:
                        step_result = res

                elif action.startswith("assert"):
                    a_type = assertion_type
                    if action == "assert_text":
                        a_type = "page_contains_text"
                        target_val = expected or selector or value
                    elif action == "assert_price":
                        a_type = "price_equals"
                        target_val = selector or value
                    elif action == "assert_total":
                        a_type = "price_total_matches"
                        target_val = selector or value
                    else:
                        target_val = selector or value

                    res = verify_assertion(a_type, target_val, expected)
                    if "[FAILED]" in res:
                        step_status = "FAILED"
                        step_result = res
                    else:
                        step_result = res

                else:
                    step_status = "FAILED"
                    step_result = f"Unknown action: {action}"

                step_trace.selector = selector
                step_trace.result = step_result
                step_trace.status = step_status
                step_trace.duration_s = round(time.time() - turn_start, 2)

                # Capture visual proof if failed
                if step_status == "FAILED":
                    fail_shot = browser.capture_failure_screenshot(f"fail_turn_{turn}.png")
                    step_trace.screenshot_path = fail_shot

                traces.append(step_trace)
                if on_step_callback:
                    on_step_callback(step_trace)

                progress.remove_task(task)

                # Refresh DOM state for next turn
                snapshot = browser.get_compact_dom()
                current_dom = snapshot.get("elements_summary", "")
                current_url = snapshot.get("url", current_url)
                current_title = snapshot.get("title", current_title)

        # Check assertion results
        assertions = get_assertion_history()
        all_assertions_passed = all(a.passed for a in assertions) if assertions else True

        if not is_finished and final_status == "PASSED":
            if turn >= self.max_turns:
                final_status = "FAILED"
                error_msg = f"Turn limit reached ({self.max_turns} turns) without finish."
            elif not all_assertions_passed:
                final_status = "FAILED"
                error_msg = "One or more test assertions failed."

        total_duration = round(time.time() - start_time, 2)

        # Compile Audit Report
        summary_text = (
            f"Autonomous Goal: '{goal}'. "
            f"Executed {len(traces)} turns in {total_duration}s using {self.config.provider} ({self.config.model}). "
            f"Total assertions: {len(assertions)}, Passed: {sum(1 for a in assertions if a.passed)}. "
            f"Final Status: {final_status}."
        )

        report_md = compile_markdown_report(
            test_name=f"Autonomous Goal: {goal[:40]}...",
            run_summary=summary_text,
            status=final_status,
            session_id=self.session_id,
            target_url=start_url,
        )

        # Add Autonomous Planner Traces section to markdown report
        trace_table = "\n\n### Autonomous Action Trace\n\n| Turn | Action | Target / Selector | Result | Status |\n| :--- | :--- | :--- | :--- | :--- |\n"
        for t in traces:
            trace_table += f"| {t.step_number} | `{t.action}` | `{t.selector or t.value}` | {t.result[:80]} | **{t.status}** |\n"

        full_report_md = report_md + trace_table
        out_filename = f"autonomous_qa_{int(time.time())}.md"
        out_path = str(self.config.reports_dir / out_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full_report_md)

        # Print Rich Summary to Console
        print_rich_summary(test_name=f"Autonomous: {goal[:40]}", status=final_status, report_path=out_path)

        return AutonomousPlanResult(
            goal=goal,
            target_url=start_url,
            status=final_status,
            total_steps=len(traces),
            duration_s=total_duration,
            session_id=self.session_id,
            provider_used=self.config.provider,
            model_used=self.config.model,
            traces=traces,
            assertions_count=len(assertions),
            assertions_passed=sum(1 for a in assertions if a.passed),
            report_path=out_path,
            screenshots=browser.captured_screenshots,
            error_message=error_msg,
        )


def main() -> None:
    """CLI runner for Autonomous Goal Planner."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="MinionQA Autonomous Goal Planner (Leaka AI-grade OpenMinion Planner)"
    )
    parser.add_argument("--url", "-u", type=str, default="https://example.com", help="Target URL to test")
    parser.add_argument("--goal", "-g", type=str, required=True, help="Natural-language goal to execute")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode (default: headless)")
    parser.add_argument("--max-turns", "-m", type=int, default=15, help="Maximum planner turns")
    args = parser.parse_args()

    cfg = MinionQAConfig(headless=not args.headed)
    planner = AutonomousGoalPlanner(config=cfg, max_turns=args.max_turns)

    try:
        result = planner.execute_goal(goal=args.goal, start_url=args.url)
        sys.exit(0 if result.status == "PASSED" else 1)
    finally:
        reset_browser_session()


if __name__ == "__main__":
    main()
