# MISSION SPECIFICATION: MinionQA (Cortensor Hackathon #5)

## 1. Project Context & Objectives
You are building **MinionQA**, a specialized autonomous web QA and revenue-flow validation agent for **Cortensor Hackathon #5 (DoraHacks #2418)**.
- **Hackathon Theme:** Cortensor Mainnet Full Alpha & OpenMinion 0.1 Python SDK builder round.
- **Submission Deadline:** December 18, 2026.
- **Primary Technical Mandate:** The agent runtime MUST be natively powered by the `openminion` (0.1+) Python SDK using its `Agent`, `@tool` decorators, and session primitives.
- **Our Advantage:** The hackathon explicitly lists "QA helper" and "monitoring agent" under official project ideas. We are adapting the core browser QA engine from an existing project (`Leaka AI`) into this clean, hyper-focused hackathon repository.

---

## 2. Source Codebase Reference (Leaka AI)
Do NOT copy or clone the entire repository. Instead, inspect and extract only the relevant business logic from:
`C:\Users\aryan\Desktop\WEB3 PROJECTS\Leaka AI`

### Specific Files to Read & Extract From:
1. `backend/app/assertions.py` — The core assertion engine (validating UI text, price totals, elements, and HTTP responses).
2. `backend/app/worker.py` — The Playwright/browser-use execution routines (headless Chromium launching, DOM interaction, screenshot extraction).
3. `backend/app/schemas.py` — The data schemas for test runs, steps, and failure summaries.

---

## 3. Target Architecture for MinionQA

### A. Core Runtime (`openminion`)
Build a Python 3.11+ agent using `openminion`:
- Define custom `@tool` functions:
  1. `navigate_and_snapshot(url: str)`: Loads page in Playwright, extracts interactive DOM elements, logs console errors.
  2. `execute_action(action_type: str, selector: str, value: str)`: Clicks, fills inputs, or submits forms.
  3. `verify_assertion(assertion_type: str, target: str, expected_value: str)`: Verifies prices, coupons, discounts, or element presence.
  4. `generate_qa_audit_report(run_summary: str, status: str)`: Compiles steps taken, screenshots captured, and final test verdict.
- Run the agent inside OpenMinion's session context:
  ```python
  from openminion import Agent, tool
  # Agent orchestration loop with Cortensor / local model provider