# What We Learned: Building MinionQA with OpenMinion 0.1 & Cortensor

This retrospective captures our key technical findings, developer experience insights, and recommendations gathered while developing **MinionQA** for **Cortensor Hackathon #5**.

---

## 1. Developer Experience & OpenMinion 0.1 Highlights

### What Worked Exceptionally Well
1. **First-Class Cortensor Provider Architecture:**
   OpenMinion 0.1 treats Cortensor as a premier model provider alongside OpenAI and Anthropic. Having direct support for both `cortensor-portal` and direct `cortensor-router` (`CortensorProviderConfig`) makes configuring decentralized AI runtimes straightforward.
2. **Declarative Tool Decorators (`@tool`):**
   The `@tool` decorator in OpenMinion automatically extracts docstrings, parameter signatures, and metadata tags (`idempotent`, `dangerous`, `tags`). Registering tools with an `Agent(tools=[...])` is clean, idiomatic Python.
3. **Session State Primitives:**
   OpenMinion’s native session tracking (`session_id`) and SQLite state backend (`openminion.db`) allow long-running QA test scenarios to maintain context across turns and recover state gracefully.
4. **Action Policy Gating:**
   The built-in action policy gating provides a critical safety layer for autonomous agents interacting with stateful systems (e.g. intercepting accidental live purchases or destructive mutations).

---

## 2. Technical Hurdles & Practical Solutions

### A. Configuration Bootstrapping
- **Observation:** `openminion.Agent` requires a valid configuration file (`.openminion/agents.json`). Initializing an agent without running `openminion setup` first raises `ConfigManagerError`.
- **Solution in MinionQA:** We automated the provisioning of `.openminion/agents.json` with pre-configured Cortensor Router and Portal presets, ensuring zero-friction setup for developers cloning the repo.

### B. Python 3.11+ Subprocess Spawning on Windows
- **Observation:** Default `SelectorEventLoop` on Windows raises `NotImplementedError` when Playwright attempts to launch Chromium subprocesses.
- **Solution:** Enforcing `asyncio.WindowsProactorEventLoopPolicy()` at module initialization resolves the issue across all Windows environments.

### C. The DOM Token Explosion Dilemma
- **Observation:** Feeding full DOM HTML to an LLM agent consumes 50k–100k tokens per snapshot, resulting in high latency and frequent context window exhaustion.
- **Solution:** MinionQA extracts an interactive element tree (compacted to `< 450` tokens) combined with visible text extraction and deterministic Python assertions. This delivers sub-second assertion speed and reliable agent reasoning.

---

## 3. Cortensor Ecosystem Relevance (10% Pillar)

MinionQA serves as a foundational quality assurance and financial integrity agent for the Cortensor decentralized ecosystem:

### 1. Continuous Validation of Decentralized Portals (dApps)
- Decentralized applications frequently experience breaking updates due to RPC node lag, smart contract upgrades, or frontend bundle changes.
- MinionQA can be scheduled as an autonomous daemon to continuously test dApp frontends, ensuring buttons, wallet connection modals, and transaction review dialogs remain functional.

### 2. Revenue-Flow & Mathematical Validation
- Web3 applications handle critical financial flows: token swaps, liquidity pool deposits, staking yields, and bridge transfers.
- MinionQA’s **Deterministic Test Oracle** validates exact calculations (e.g. `price_total_matches`, fee deductions, and slippage tolerances) independently of the LLM, guaranteeing mathematical correctness.

### 3. Canary Testing & Regression Guard for Cortensor Builders
- Any project participating in Cortensor Hackathons or deploying to Cortensor Mainnet can integrate MinionQA into their CI/CD pipeline or run it as a continuous monitoring agent to catch regressions before end users encounter them.

---

## 4. Recommendations for OpenMinion SDK 0.2

Based on our implementation experience, we recommend the following enhancements for future OpenMinion releases:

1. **Zero-Config In-Memory Agent:**
   Provide a lightweight, in-memory default configuration mode so developers can run `Agent()` in unit tests or quick scripts without requiring an `.openminion/` folder on disk.
2. **Built-in DOM / Web Tool Family:**
   Given that web browsing and QA are primary autonomous agent use cases, bundling a standardized, token-compacted `browser` tool family in OpenMinion would save builders significant boilerplate.
3. **Enhanced Cortensor Telemetry:**
   Surface Cortensor Router node latency, token throughput, and consensus verification proofs directly inside `AgentRunResult.stats`.
4. **Native Retry Policy on Tool Level:**
   Allow `@tool(retry_count=3, retry_delay_seconds=1.0)` directly on the decorator for transient network failures.
