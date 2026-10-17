"""
Playwright Browser Automation & DOM Snapshotting Tool for MinionQA.

Adapted from Leaka AI (backend/app/worker.py) with Windows subprocess fixes,
interactive element extraction, console error monitoring, and screenshot capture.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Critical Windows Proactor fix from Leaka AI
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from openminion import tool

from minion_qa.config import default_config
from minion_qa.tools.assertions import extract_visible_text


class BrowserSession:
    """Manages active Playwright browser instance, page context, and telemetry."""

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.playwright: Optional[Any] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.console_logs: List[Dict[str, Any]] = []
        self.failed_requests: List[Dict[str, Any]] = []
        self.captured_screenshots: List[Dict[str, str]] = []
        self.step_history: List[Dict[str, Any]] = []

    def ensure_started(self) -> Page:
        """Launch Chromium instance if not already running."""
        if self.page is not None and not self.page.is_closed():
            return self.page

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        self.context = self.browser.new_context(
            viewport={
                "width": default_config.viewport_width,
                "height": default_config.viewport_height,
            },
            user_agent="MinionQA-Autonomous-Agent/0.1.0 (Cortensor Hackathon #5)",
        )
        self.page = self.context.new_page()

        # Telemetry listeners
        self.page.on(
            "console",
            lambda msg: self.console_logs.append(
                {"type": msg.type, "text": msg.text, "time": time.time()}
            ),
        )
        self.page.on(
            "requestfailed",
            lambda req: self.failed_requests.append(
                {
                    "url": req.url,
                    "method": req.method,
                    "failure": req.failure,
                    "time": time.time(),
                }
            ),
        )

        return self.page

    def navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to a target URL, extract DOM snapshot, and capture screenshot."""
        page = self.ensure_started()
        start_time = time.time()
        
        try:
            page.goto(url, wait_until="load", timeout=default_config.timeout_ms)
            # Wait for networkidle so modern SPAs (Vite, Next.js, React) finish client hydration
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            # Short stabilization pause for dynamic DOM re-rendering
            page.wait_for_timeout(1000)
        except Exception as e:
            # Fallback if timeout: still read current page
            self.console_logs.append({"type": "error", "text": f"Navigation timeout or error: {e}"})

        duration = round(time.time() - start_time, 2)
        dom_html = page.content()
        visible_text = self.get_visible_text()
        interactive_elements = self._extract_interactive_elements(page)

        shot_name = f"snapshot_{int(time.time())}"
        shot_path = self.take_screenshot(shot_name)

        step_record = {
            "action": "navigate",
            "url": page.url,
            "target": url,
            "duration": duration,
            "status": "success",
            "screenshot": shot_path,
        }
        self.step_history.append(step_record)

        return {
            "current_url": page.url,
            "title": page.title(),
            "interactive_elements": interactive_elements,
            "visible_text_excerpt": visible_text[:1200],
            "console_errors_count": len([c for c in self.console_logs if c.get("type") == "error"]),
            "screenshot": shot_path,
        }

    def execute_action(
        self, action_type: str, selector: str, value: str = ""
    ) -> Dict[str, Any]:
        """Execute a user-like browser action (click, fill, type, select, wait)."""
        page = self.ensure_started()
        act = action_type.strip().lower()
        success = False
        error_msg = None

        try:
            if act in ("click", "press"):
                # Multi-selector strategy: try CSS/xpath or text
                if selector.startswith("//") or selector.startswith("id=") or selector.startswith("["):
                    page.click(selector, timeout=8000)
                else:
                    # Try text selector if pure string
                    try:
                        page.click(selector, timeout=4000)
                    except Exception:
                        page.click(f"text={selector}", timeout=5000)
                page.wait_for_timeout(500)
                success = True

            elif act in ("fill", "type", "input"):
                page.fill(selector, str(value), timeout=8000)
                success = True

            elif act in ("select", "dropdown"):
                page.select_option(selector, value, timeout=8000)
                success = True

            elif act in ("submit", "enter"):
                if selector:
                    page.press(selector, "Enter", timeout=8000)
                else:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                success = True

            elif act == "wait":
                wait_sec = float(value) if value else 2.0
                page.wait_for_timeout(int(wait_sec * 1000))
                success = True

            else:
                error_msg = f"Unsupported action type: {act}"

        except Exception as e:
            error_msg = str(e)
            # Automatic failure screenshot capture
            self.take_screenshot(f"failure_action_{act}_{int(time.time())}", is_failure=True)

        step_record = {
            "action": act,
            "selector": selector,
            "value": value,
            "success": success,
            "error": error_msg,
            "url": page.url,
            "time": time.time(),
        }
        self.step_history.append(step_record)

        return step_record

    def _extract_interactive_elements(self, page: Page) -> List[Dict[str, str]]:
        """Extract user-facing actionable elements (buttons, inputs, links, prices)."""
        js_extract = """
        () => {
            const elements = Array.from(document.querySelectorAll(
                'button, a[href], input, select, textarea, [role="button"], [data-testid], .price, .total, .subtotal'
            ));
            return elements.slice(0, 40).map((el, idx) => {
                const tag = el.tagName.toLowerCase();
                const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim();
                const id = el.id ? '#' + el.id : '';
                const testId = el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` : '';
                const name = el.getAttribute('name') ? `[name="${el.getAttribute('name')}"]` : '';
                const role = el.getAttribute('role') || tag;
                
                let selector = testId || id || name;
                if (!selector && text && tag !== 'input') {
                    selector = `${tag}:has-text("${text.slice(0, 30)}")`;
                }
                if (!selector) {
                    selector = `${tag}:nth-of-type(${idx + 1})`;
                }
                
                return {
                    index: idx,
                    tag: tag,
                    role: role,
                    text: text.slice(0, 80),
                    selector: selector
                };
            }).filter(item => item.text.length > 0 || item.tag === 'input');
        }
        """
        try:
            return page.evaluate(js_extract)
        except Exception:
            return []

    def take_screenshot(self, name: str, is_failure: bool = False) -> str:
        """Capture and save full viewport screenshot."""
        if not self.page:
            return ""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "FAIL_" if is_failure else ""
        filename = f"{prefix}{name}_{timestamp}.png"
        filepath = default_config.screenshot_dir / filename

        try:
            self.page.screenshot(path=str(filepath), full_page=False)
            rel_path = str(filepath.relative_to(Path.cwd()))
        except Exception:
            rel_path = str(filepath)

        self.captured_screenshots.append({
            "path": rel_path,
            "filename": filename,
            "is_failure": is_failure,
            "url": self.page.url if self.page else "",
        })
        return rel_path

    def get_current_url(self) -> str:
        return self.page.url if self.page else ""

    def get_dom_html(self) -> str:
        return self.page.content() if self.page else ""

    def get_visible_text(self) -> str:
        if self.page:
            try:
                text = self.page.inner_text("body", timeout=2000)
                if text and text.strip():
                    return text
            except Exception:
                pass
        return extract_visible_text(self.get_dom_html())

    def get_console_errors(self) -> List[Dict[str, Any]]:
        return [c for c in self.console_logs if c.get("type") == "error"]

    def get_compact_dom(self) -> Dict[str, Any]:
        """Extract current page compact DOM snapshot for LLM reasoning."""
        page = self.ensure_started()
        elems = self._extract_interactive_elements(page)
        elem_summary = "\n".join(
            [f"- [{e.get('index')}] <{e.get('tag')}> '{e.get('text')}' (selector: `{e.get('selector')}`)"
             for e in elems[:30]]
        )
        return {
            "url": page.url,
            "title": page.title(),
            "elements": elems,
            "elements_summary": elem_summary or "- No interactive elements detected",
            "visible_text": self.get_visible_text()[:1500],
        }

    def capture_failure_screenshot(self, name: str = "failure") -> str:
        """Capture and record failure screenshot with FAIL_ prefix."""
        clean_name = name.replace(".png", "")
        return self.take_screenshot(clean_name, is_failure=True)

    def close(self) -> None:
        """Clean up Playwright processes."""
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.page = None
            self.context = None
            self.browser = None
            self.playwright = None


# Global active browser session
_current_browser_session: Optional[BrowserSession] = None


def get_browser_session(headless: bool = True) -> BrowserSession:
    """Retrieve or initialize the active browser session."""
    global _current_browser_session
    if _current_browser_session is None:
        _current_browser_session = BrowserSession(headless=headless)
    return _current_browser_session


def reset_browser_session() -> None:
    """Close and reset current browser session."""
    global _current_browser_session
    if _current_browser_session is not None:
        _current_browser_session.close()
        _current_browser_session = None


# ---------------------------------------------------------------------------
# OpenMinion @tool definitions
# ---------------------------------------------------------------------------
@tool(
    name="navigate_and_snapshot",
    description=(
        "Navigates Playwright headless browser to a specified URL, captures a screenshot, "
        "and extracts interactive DOM elements, visible text, and console errors."
    ),
    tags=("browser", "navigation", "inspection"),
    idempotent=False,
)
def navigate_and_snapshot(url: str) -> str:
    """OpenMinion tool: Navigates and snapshots web page state."""
    session = get_browser_session()
    snapshot = session.navigate(url)
    
    elems = snapshot.get("interactive_elements", [])
    elem_summary = "\n".join(
        [f"- [{e.get('index')}] <{e.get('tag')}> '{e.get('text')}' (selector: `{e.get('selector')}`)"
         for e in elems[:15]]
    )
    
    return (
        f"Navigated to: {snapshot.get('current_url')}\n"
        f"Page Title: {snapshot.get('title')}\n"
        f"Screenshot saved: {snapshot.get('screenshot')}\n"
        f"Interactive Elements Detected ({len(elems)} total):\n{elem_summary}\n"
        f"Console Errors: {snapshot.get('console_errors_count')}\n"
    )


@tool(
    name="execute_action",
    description=(
        "Executes a user interaction on the page: action_type can be 'click', 'fill', 'type', "
        "'select', 'submit', or 'wait'. Selector specifies target element, and value specifies text input."
    ),
    tags=("browser", "interaction"),
    dangerous=False, # Gated by OpenMinion action policy
)
def execute_action(action_type: str, selector: str, value: str = "") -> str:
    """OpenMinion tool: Executes a user DOM interaction."""
    session = get_browser_session()
    
    # OpenMinion Policy Check simulation / safeguard
    if default_config.enable_policy_gating:
        # Prevent unintentional destructive checkout/payment actions unless confirmed
        sensitive_keywords = ["pay-real-card", "delete-account", "transfer-real-funds"]
        if any(kw in selector.lower() or kw in value.lower() for kw in sensitive_keywords):
            return (
                f"[POLICY_DENIED] Action '{action_type}' on '{selector}' requires explicit confirmation: "
                "Intercepted by OpenMinion Action Policy Gating."
            )

    res = session.execute_action(action_type, selector, value)
    if res.get("success"):
        return f"[SUCCESS] Action '{action_type}' on selector '{selector}' succeeded."
    else:
        return f"[FAILED] Action '{action_type}' on selector '{selector}' failed: {res.get('error')}"
