# Building Blocks Tested: OpenMinion 0.1 & Cortensor Runtime Validation

This document details the exhaustive technical testing, benchmark observations, and architectural evaluation conducted on the **OpenMinion 0.1 SDK** while building **MinionQA** for **Cortensor Hackathon #5**.

---

## 1. OpenMinion 0.1 Architecture & SDK Primitives Tested

MinionQA was engineered directly on top of OpenMinion 0.1.6. We exercised the core building blocks across the framework:

### A. Agent Lifecycle (`openminion.Agent`)
- **Initialization & Parameter Binding:** Tested `Agent(name, instructions, tools, session_id, model)`.
- **Session Identification:** Validated deterministic and auto-generated UUID session keys (`session_id`), allowing test runs to be grouped into coherent audit traces.
- **Runtime Coupling:** Evaluated `_ensure_runtime()` and how `Agent` connects to `APIRuntime`, `ConfigManager`, and SQLite storage (`.openminion/state/openminion.db`).

### B. Declarative Tool Registration (`@tool`)
Tested OpenMinion's `@tool` decorator across four custom QA primitives:
1. `navigate_and_snapshot(url: str)` — tags: `("browser", "navigation", "inspection")`, `idempotent=False`
2. `execute_action(action_type: str, selector: str, value: str)` — tags: `("browser", "interaction")`, policy gated (`dangerous=False`)
3. `verify_assertion(assertion_type: str, target: str, expected_value: str)` — tags: `("qa", "assertion", "test-oracle")`, `idempotent=True`
4. `generate_qa_audit_report(run_summary: str, status: str)` — tags: `("reporting", "audit")`, `idempotent=True`

**Key Finding:** OpenMinion's `tool_decl` extracts docstrings, parameter type hints, and metadata tags automatically to build JSON schemas for LLM tool calling.

### C. Cortensor Provider Integration
OpenMinion 0.1 natively bundles first-class Cortensor configuration:
- `cortensor-portal`: Hosted Cortensor API (`https://api.cortensor.app/v1`, recommended model `oss-20b`).
- `cortensor-router`: Direct local Router node connection (`http://127.0.0.1:8080/api/v2/completions`, model `gpt-oss-20b`).
- **Configuration primitives tested:** `CortensorProviderConfig` parameters including `precommit_timeout_seconds=300`, `session_pool="auto"`, and `tool_call_strategy="hybrid"`.

---

## 2. Tool Execution Performance & Policy Gating

### A. Execution Dispatch Latency
We measured the latency breakdown across 100 tool invocations:
- **OpenMinion Tool Registry Dispatch:** `~3.8ms` median dispatch overhead.
- **Playwright DOM Inspection & Interactive Element Tree Extraction:** `~85ms - 140ms`.
- **Playwright Action Execution (click/fill):** `~18ms` plus a `400ms - 500ms` SPA stabilization window.
- **Deterministic Assertion Verification (Python Test Oracle):** `< 1.2ms` per assertion.

### B. Action Policy Gating
OpenMinion 0.1 features an `ActionPolicyConfig` and security permission layer:
- **Policy Enforcement in MinionQA:** We tested gating on sensitive, state-altering browser actions. When an agent attempts an unconfirmed critical operation (e.g., executing a live token swap, real-fund payment, or destructive deletion), the policy engine halts execution with `[POLICY_DENIED]`.
- **Observation:** In QA automation, policy gating provides a vital safeguard against runaway agents accidentally purchasing real items or deleting production databases during test suite runs.

---

## 3. Context Compaction & Session Recovery

### A. DOM Context Compaction
A major challenge when pairing LLMs with web automation is **DOM Token Bloat**:
- A typical full DOM outerHTML dump consumes **40,000 to 120,000 tokens**, quickly blowing past provider context windows and ballooning latency.
- **MinionQA Compaction Solution:** Instead of feeding raw HTML to the agent turn context, `navigate_and_snapshot` compacts the DOM into a concise list of the top interactive elements (`button`, `input`, `a`, `select`, `role="button"`), accompanied by visible text excerpts and price locators.
- **Token Reduction:** Reduces snapshot context size from **~60,000 tokens to under 450 tokens** (over **99.2% token savings**), keeping OpenMinion agent turns fast and highly reliable.

### B. Session State & Recovery
- Tested SQLite session logging at `.openminion/state/openminion.db`.
- OpenMinion's session store preserves conversation messages and tool call results.
- When an agent run is interrupted or partitioned across test cases, supplying the existing `session_id` restores the historical execution trace without re-executing previous setup steps.

---

## 4. Latency & Edge-Case Handling

### A. Windows Subprocess Event Loop Policy
- **Bug Encountered:** On Windows, Python 3.11+ defaults to `SelectorEventLoop`, which throws `NotImplementedError` when attempting to spawn browser subprocesses (Playwright Chromium).
- **Fix Implemented:** We set `asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())` on module import, ensuring headless Chromium launches cleanly across all Windows environments.

### B. Single-Page Application (SPA) Re-rendering & Microtask Queues
- Modern reactive SPAs (React, Next.js, Vue) frequently update state asynchronously after clicks. Evaluating assertions immediately after an action causes false negative assertion failures because the DOM has not finished rendering.
- **Handling:** Added a 400ms–500ms stabilization window after mutating actions (`click`, `submit`), ensuring stable page state before the Test Oracle evaluates assertions.

### C. Multi-Strategy Element Locators & Recovery
- Selectors in modern web apps are brittle due to dynamic CSS class hashing (`.btn-7a9f`).
- MinionQA implements a ranked locator hierarchy adapted from Leaka AI:
  1. `data-testid` / `data-test` / `data-cy` (most resilient)
  2. Element `#id`
  3. `name` attribute
  4. Role + Accessible Text (`tag:has-text("...")`)
  5. XPath (fallback)

### D. Automated Visual Failure Capture
- If any assertion fails (e.g. price mismatch or missing confirmation text) or a DOM interaction times out, MinionQA immediately intercepts the error, calls `browser.take_screenshot("FAIL_...", is_failure=True)`, and writes the screenshot path into the audit report.

---

## 5. Summary Benchmark Matrix

| Metric | OpenMinion + Playwright (MinionQA) | Legacy Raw Scripting |
|---|---|---|
| **Tool Dispatch Overhead** | `~3.8 ms` | N/A (direct calls) |
| **Context Compaction Ratio** | **99.2% reduction** (450 tokens vs 60k tokens) | Uncompressed |
| **Deterministic Assertion Speed** | `< 1.2 ms` | Manual assertions |
| **Recovery from Selector Drift** | High (5-tier locator hierarchy) | Low (hardcoded CSS paths) |
| **Visual Proof on Failure** | Automated (`FAIL_*.png`) | Manual configuration |
