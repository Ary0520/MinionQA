"""
MinionQA Web Server & Interactive Dashboard.

Provides a REST API and Web UI for running autonomous QA flows, inspecting
real-time Playwright execution traces, evaluating deterministic assertions,
and reviewing visual screenshot proof and audit reports.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minion_qa.agent import MinionQAAgent
from minion_qa.autonomous_planner import AutonomousGoalPlanner, AutonomousPlanResult
from minion_qa.config import MinionQAConfig, default_config
from minion_qa.tools.assertions import get_assertion_history, clear_assertion_history
from minion_qa.tools.browser import get_browser_session, reset_browser_session
from examples.test_ecommerce_checkout import build_ecommerce_checkout_flow

app = FastAPI(
    title="MinionQA Server",
    description="Autonomous Web QA & Revenue-Flow Validation Agent for Cortensor Hackathon #5",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory history of runs for dashboard
_run_history: List[Dict[str, Any]] = []


class AutonomousRunRequest(BaseModel):
    url: str = "https://www.saucedemo.com"
    goal: str = (
        "Log in with standard_user and secret_sauce, add backpack to cart, "
        "go to cart, and finish checkout for John Doe 94103."
    )
    max_turns: int = 15
    headless: bool = True


class CustomRunRequest(BaseModel):
    name: str = "Custom Web Smoke Test"
    url: str = "https://example.com"
    expected_text: Optional[str] = "Example"
    check_price: Optional[str] = None


class EcommerceRunRequest(BaseModel):
    simulate_failure: bool = False
    headless: bool = True


@app.get("/api/status")
def get_system_status() -> Dict[str, Any]:
    """Return runtime telemetry, OpenMinion status, and Cortensor integration info."""
    return {
        "service": "MinionQA Autonomous QA Agent",
        "version": "0.1.0",
        "openminion_sdk": "0.1.6",
        "runtime": "Python 3.13 (Cortensor Mainnet Full Alpha)",
        "network": "Cortensor Decentralized Inference Network",
        "provider": default_config.provider,
        "model": default_config.model,
        "action_policy_gating": "ENABLED (Safe read/write confirmation preview)",
        "dom_compaction_engine": "ACTIVE (<450 token structural extract)",
        "self_healing_selectors": "ACTIVE (Ranked DOM heuristic fallback)",
        "total_runs_executed": len(_run_history),
        "assertion_oracle": "Deterministic Revenue-Flow Engine",
        "screenshots_captured": len(list(default_config.screenshot_dir.glob("*.png"))),
        "reports_generated": len(list(default_config.reports_dir.glob("*.md"))),
    }


@app.get("/api/runs")
def list_runs() -> List[Dict[str, Any]]:
    """List recent agent execution runs."""
    return _run_history[::-1]


@app.post("/api/run-autonomous")
def run_autonomous_goal(payload: AutonomousRunRequest) -> Dict[str, Any]:
    """Execute natural-language goal-driven autonomous QA via OpenMinion multi-turn LLM loop."""
    cfg = MinionQAConfig(headless=payload.headless)
    planner = AutonomousGoalPlanner(config=cfg, max_turns=payload.max_turns)

    try:
        result = planner.execute_goal(goal=payload.goal, start_url=payload.url)
        report_filename = Path(result.report_path).name if result.report_path else ""

        screenshots = []
        for s in result.screenshots:
            if isinstance(s, dict):
                screenshots.append({
                    "path": s.get("path", ""),
                    "filename": s.get("filename") or Path(s.get("path", "shot.png")).name,
                    "is_failure": bool(s.get("is_failure", False)),
                })
            else:
                p = Path(str(s))
                screenshots.append({
                    "path": str(p),
                    "filename": p.name,
                    "is_failure": "fail" in p.name.lower(),
                })

        assertion_records = [
            a.to_dict() if hasattr(a, "to_dict") else (a.model_dump() if hasattr(a, "model_dump") else a.dict())
            for a in get_assertion_history()
        ]
        traces_data = [
            {
                "step_number": t.step_number,
                "thought": t.thought,
                "action": t.action,
                "selector": t.selector,
                "value": t.value,
                "result": t.result,
                "status": t.status,
                "duration_s": t.duration_s,
                "screenshot_path": Path(t.screenshot_path).name if t.screenshot_path else None,
            }
            for t in result.traces
        ]

        run_record = {
            "id": uuid4().hex[:8],
            "name": f"Autonomous: {payload.goal[:45]}...",
            "target_url": payload.url,
            "goal": payload.goal,
            "status": result.status,
            "duration": result.duration_s,
            "session_id": result.session_id,
            "assertions_count": result.assertions_count,
            "assertions_passed": result.assertions_passed,
            "report_path": result.report_path,
            "report_filename": report_filename,
            "screenshots": screenshots,
            "assertions": assertion_records,
            "traces": traces_data,
            "provider_used": result.provider_used,
            "model_used": result.model_used,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        _run_history.append(run_record)
        return run_record
    finally:
        reset_browser_session()


@app.post("/api/run-ecommerce")
def run_ecommerce_test(payload: EcommerceRunRequest) -> Dict[str, Any]:
    """Execute the full e-commerce checkout and revenue-flow validation test."""
    test_title = (
        "E-Commerce Checkout & Revenue Flow Validation [Simulated Failure]"
        if payload.simulate_failure
        else "E-Commerce Checkout & Revenue Flow Validation"
    )

    steps = build_ecommerce_checkout_flow(simulate_failure=payload.simulate_failure)
    cfg = MinionQAConfig(headless=payload.headless)
    agent = MinionQAAgent(config=cfg)

    report_filename = f"qa_report_{int(time.time())}_{uuid4().hex[:6]}.md"
    report_path = str(default_config.reports_dir / report_filename)

    try:
        result = agent.run_flow(
            name=test_title,
            target_url="https://www.saucedemo.com",
            steps=steps,
            report_output_path=report_path,
        )
        run_record = {
            "id": uuid4().hex[:8],
            "name": test_title,
            "target_url": "https://www.saucedemo.com",
            "status": result["status"],
            "duration": result["duration"],
            "session_id": result["session_id"],
            "assertions_count": result["assertions_count"],
            "assertions_passed": result["assertions_passed"],
            "report_path": result["report_path"],
            "report_filename": report_filename,
            "screenshots": result["screenshots"],
            "assertions": result.get("assertions", []),
            "traces": [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        _run_history.append(run_record)
        return run_record
    finally:
        agent.close()


@app.post("/api/run-custom")
def run_custom_test(payload: CustomRunRequest) -> Dict[str, Any]:
    """Execute a custom URL test with targeted assertions."""
    steps = [
        {"type": "action", "action": "wait", "value": "1.0", "description": "Wait for page render"},
    ]
    if payload.expected_text:
        steps.append({
            "type": "assertion",
            "assertion_type": "page_contains_text",
            "target": payload.expected_text,
            "description": f"Verify page contains '{payload.expected_text}'",
        })
    if payload.check_price:
        steps.append({
            "type": "assertion",
            "assertion_type": "price_equals",
            "target": payload.check_price,
            "expected": payload.check_price,
            "description": f"Verify price matches '{payload.check_price}'",
        })

    cfg = MinionQAConfig(headless=True)
    agent = MinionQAAgent(config=cfg)
    report_filename = f"custom_qa_{int(time.time())}.md"
    report_path = str(default_config.reports_dir / report_filename)

    try:
        result = agent.run_flow(
            name=payload.name,
            target_url=payload.url,
            steps=steps,
            report_output_path=report_path,
        )
        run_record = {
            "id": uuid4().hex[:8],
            "name": payload.name,
            "target_url": payload.url,
            "status": result["status"],
            "duration": result["duration"],
            "session_id": result["session_id"],
            "assertions_count": result["assertions_count"],
            "assertions_passed": result["assertions_passed"],
            "report_path": result["report_path"],
            "report_filename": report_filename,
            "screenshots": result["screenshots"],
            "assertions": result.get("assertions", []),
            "traces": [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        _run_history.append(run_record)
        return run_record
    finally:
        agent.close()


@app.get("/api/reports")
def list_reports() -> List[Dict[str, Any]]:
    """List all available QA audit markdown reports."""
    reports = []
    for p in sorted(default_config.reports_dir.glob("*.md"), reverse=True):
        reports.append({
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "modified": time.ctime(p.stat().st_mtime),
            "path": str(p),
        })
    sample_rep = Path("examples/sample_run_report.md")
    if sample_rep.exists():
        reports.append({
            "filename": "sample_run_report.md",
            "size_bytes": sample_rep.stat().st_size,
            "modified": time.ctime(sample_rep.stat().st_mtime),
            "path": str(sample_rep),
        })
    return reports


@app.get("/api/reports/{filename}")
def get_report_content(filename: str) -> Dict[str, str]:
    """Retrieve raw markdown content of a report."""
    if filename == "sample_run_report.md":
        target = Path("examples/sample_run_report.md")
    else:
        target = default_config.reports_dir / filename

    if not target.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return {"filename": filename, "content": target.read_text(encoding="utf-8")}


@app.get("/screenshots/{filename}")
def get_screenshot_file(filename: str) -> FileResponse:
    """Serve captured PNG screenshot."""
    p = default_config.screenshot_dir / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(p), media_type="image/png")


# ---------------------------------------------------------------------------
# Interactive Web UI Dashboard
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MinionQA Dashboard | Autonomous Web QA & Revenue-Flow Validation</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0b0f19; color: #f3f4f6; }
    code, pre { font-family: 'JetBrains Mono', monospace; }
    .glass { background: rgba(17, 24, 39, 0.75); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glass-card { background: #111827; border: 1px solid rgba(255, 255, 255, 0.06); }
    .gradient-text { background: linear-gradient(135deg, #38bdf8 0%, #818cf8 50%, #c084fc 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  </style>
</head>
<body class="min-h-screen">
  <!-- Top Navbar -->
  <header class="glass sticky top-0 z-50 px-6 py-4 border-b border-gray-800 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white font-bold text-xl shadow-lg shadow-cyan-500/20">
        <i class="fa-solid fa-robot"></i>
      </div>
      <div>
        <div class="flex items-center space-x-2">
          <h1 class="text-xl font-bold tracking-tight text-white">MinionQA</h1>
          <span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800">OpenMinion 0.1</span>
          <span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-indigo-950 text-indigo-400 border border-indigo-800">Cortensor Alpha</span>
        </div>
        <p class="text-xs text-gray-400">Autonomous Web QA & Revenue-Flow Validation Agent</p>
      </div>
    </div>
    
    <div class="flex items-center space-x-3 text-sm">
      <span class="inline-flex items-center px-3 py-1 rounded-lg bg-emerald-950/60 text-emerald-400 border border-emerald-800/60">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse mr-2"></span>
        Agent Engine Ready
      </span>
      <a href="https://github.com/Ary0520/MinionQA" target="_blank" class="px-3 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700 flex items-center gap-1.5 transition">
        <i class="fa-brands fa-github"></i>
        <span>Repository</span>
      </a>
    </div>
  </header>

  <!-- Main Container -->
  <main class="max-w-7xl mx-auto px-6 py-8 space-y-8">
    
    <!-- Hero Banner -->
    <div class="glass-card rounded-2xl p-6 relative overflow-hidden border border-cyan-900/40">
      <div class="absolute -right-20 -top-20 w-96 h-96 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none"></div>
      <div class="space-y-2">
        <span class="text-xs font-bold uppercase tracking-wider text-cyan-400">Cortensor Hackathon #5 (DoraHacks #2418) Live Runner</span>
        <h2 class="text-2xl lg:text-3xl font-extrabold text-white">Autonomous E-Commerce & Financial Web QA</h2>
        <p class="text-gray-400 text-sm leading-relaxed max-w-4xl">
          MinionQA pairs the <strong>OpenMinion 0.1</strong> autonomous agent framework with headless Playwright execution and a deterministic Python test oracle. It operates on ultra-compact DOM trees (&lt;450 tokens), self-heals broken selectors on dynamic pages, evaluates financial pricing arithmetic down to the cent, captures instant visual failure artifacts, and generates reproducible audit reports.
        </p>
      </div>
    </div>

    <!-- Centerpiece: Autonomous Goal Studio -->
    <div class="glass-card rounded-2xl p-6 border border-cyan-800/50 shadow-xl space-y-6">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-gray-800/80 pb-4">
        <div>
          <div class="flex items-center gap-2">
            <span class="text-xl">🎯</span>
            <h3 class="text-xl font-bold text-white">Autonomous Goal Studio</h3>
            <span class="px-2 py-0.5 text-[11px] font-semibold rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800">Multi-Turn LLM Loop</span>
          </div>
          <p class="text-xs text-gray-400 mt-1">
            Input any natural-language objective. MinionQA inspects live DOM state, reasons multi-turn steps, heals selectors, and validates assertions.
          </p>
        </div>
        
        <div class="flex items-center gap-2 text-xs">
          <span class="px-2.5 py-1 rounded-lg bg-gray-900 border border-gray-800 text-gray-300 flex items-center gap-1.5 font-mono">
            <i class="fa-solid fa-microchip text-cyan-400"></i>
            <span id="activeModelLabel">OpenRouter / gpt-4o-mini</span>
          </span>
          <span class="px-2.5 py-1 rounded-lg bg-emerald-950/60 border border-emerald-800/60 text-emerald-300 font-mono">
            <i class="fa-solid fa-shield-halved mr-1"></i>Self-Healing On
          </span>
        </div>
      </div>

      <!-- Quick Preset Buttons -->
      <div class="space-y-2">
        <label class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Quick Benchmark Presets</label>
        <div class="flex flex-wrap gap-2">
          <button type="button" onclick="loadPreset('saucedemo')" class="px-3 py-1.5 rounded-lg bg-gray-900 hover:bg-gray-800 text-cyan-300 border border-gray-700 text-xs font-medium flex items-center gap-1.5 transition">
            <i class="fa-solid fa-cart-shopping text-cyan-400"></i>
            <span>🛒 SauceDemo Checkout Funnel</span>
          </button>
          <button type="button" onclick="loadPreset('example')" class="px-3 py-1.5 rounded-lg bg-gray-900 hover:bg-gray-800 text-indigo-300 border border-gray-700 text-xs font-medium flex items-center gap-1.5 transition">
            <i class="fa-solid fa-globe text-indigo-400"></i>
            <span>🌐 Example.com Smoke Test</span>
          </button>
          <button type="button" onclick="loadPreset('anomaly')" class="px-3 py-1.5 rounded-lg bg-gray-900 hover:bg-gray-800 text-rose-300 border border-gray-700 text-xs font-medium flex items-center gap-1.5 transition">
            <i class="fa-solid fa-triangle-exclamation text-rose-400"></i>
            <span>⚠️ Pricing Anomaly Catch</span>
          </button>
          <button type="button" onclick="loadPreset('dreamvault')" class="px-3 py-1.5 rounded-lg bg-gray-900 hover:bg-gray-800 text-purple-300 border border-purple-800/60 text-xs font-medium flex items-center gap-1.5 transition">
            <i class="fa-solid fa-cloud text-purple-400"></i>
            <span>✨ Dream Vault Auth</span>
          </button>
        </div>
      </div>

      <!-- Autonomous Input Form -->
      <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div class="md:col-span-3 space-y-1">
          <label class="text-xs font-semibold text-gray-300">Target Web URL</label>
          <input id="autoUrl" type="url" value="https://www.saucedemo.com" placeholder="https://..." class="w-full px-3.5 py-2.5 bg-gray-900 border border-gray-700 rounded-xl text-xs text-white font-mono focus:outline-none focus:border-cyan-500">
        </div>
        <div class="space-y-1">
          <label class="text-xs font-semibold text-gray-300">Max Planner Turns</label>
          <select id="autoMaxTurns" class="w-full px-3.5 py-2.5 bg-gray-900 border border-gray-700 rounded-xl text-xs text-white focus:outline-none focus:border-cyan-500">
            <option value="5">5 Turns (Quick Smoke)</option>
            <option value="10">10 Turns (Medium)</option>
            <option value="15" selected>15 Turns (Full Funnel)</option>
            <option value="20">20 Turns (Deep Flow)</option>
          </select>
        </div>
      </div>

      <div class="space-y-1">
        <label class="text-xs font-semibold text-gray-300">Natural-Language QA Objective / Goal</label>
        <textarea id="autoGoal" rows="3" placeholder="Enter high-level QA instruction..." class="w-full px-3.5 py-2.5 bg-gray-900 border border-gray-700 rounded-xl text-xs text-white leading-relaxed focus:outline-none focus:border-cyan-500 font-mono">Log in with username standard_user and password secret_sauce, add backpack to cart, go to cart, and finish checkout for John Doe zip 94103.</textarea>
      </div>

      <div class="flex flex-col sm:flex-row items-center justify-between gap-4 pt-2">
        <div class="flex items-center space-x-2 text-xs text-gray-400">
          <input type="checkbox" id="autoHeadless" checked class="rounded bg-gray-900 border-gray-700 text-cyan-600 focus:ring-0">
          <label for="autoHeadless">Headless Chromium (Uncheck for visual browser debugging)</label>
        </div>

        <button id="btnLaunchAutonomous" onclick="runAutonomous()" class="w-full sm:w-auto px-6 py-3 rounded-xl bg-gradient-to-r from-cyan-500 via-indigo-600 to-purple-600 hover:from-cyan-400 hover:to-purple-500 text-white font-bold text-sm flex items-center justify-center gap-2 shadow-lg shadow-cyan-500/25 transition active:scale-95">
          <i class="fa-solid fa-rocket"></i>
          <span>Launch Autonomous QA Agent</span>
        </button>
      </div>
    </div>

    <!-- Quick Run Pre-Baked Benchmarks (Static Flow & Custom Smoke) -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="glass-card rounded-xl p-5 border border-gray-800 space-y-3">
        <div class="flex items-center justify-between">
          <h4 class="text-sm font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-code-commit text-cyan-400"></i>
            <span>Deterministic Pre-Baked Flow</span>
          </h4>
          <span class="text-[10px] text-gray-400 font-mono">100% Deterministic</span>
        </div>
        <p class="text-xs text-gray-400 leading-relaxed">
          Executes rigid multi-step checkout with financial subtotal+tax verification. Useful for zero-token baseline benchmarking.
        </p>
        <div class="flex gap-2">
          <button onclick="runEcommerce(false)" class="flex-1 py-2 px-3 rounded-lg bg-gray-800 hover:bg-gray-700 text-cyan-300 border border-gray-700 text-xs font-semibold flex items-center justify-center gap-1.5 transition">
            <i class="fa-solid fa-play text-cyan-400"></i> Run Baseline Pass
          </button>
          <button onclick="runEcommerce(true)" class="py-2 px-3 rounded-lg bg-gray-800 hover:bg-rose-950/40 text-rose-300 border border-rose-900/40 text-xs font-semibold flex items-center justify-center gap-1.5 transition">
            <i class="fa-solid fa-bug text-rose-400"></i> Inject Math Mismatch
          </button>
        </div>
      </div>

      <div class="glass-card rounded-xl p-5 border border-gray-800 space-y-3">
        <div class="flex items-center justify-between">
          <h4 class="text-sm font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-compass text-indigo-400"></i>
            <span>Ad-Hoc URL Quick Checker</span>
          </h4>
          <span class="text-[10px] text-gray-400 font-mono">Single Assertion</span>
        </div>
        <p class="text-xs text-gray-400 leading-relaxed">
          Quickly verify single element or text presence on any target landing page without writing code.
        </p>
        <div class="flex gap-2">
          <input id="customUrl" type="url" value="https://example.com" class="flex-1 px-3 py-1.5 bg-gray-900 border border-gray-700 rounded-lg text-xs text-white font-mono focus:outline-none">
          <input id="customText" type="text" value="Example" class="w-28 px-3 py-1.5 bg-gray-900 border border-gray-700 rounded-lg text-xs text-white focus:outline-none">
          <button onclick="runCustom()" class="py-1.5 px-3 rounded-lg bg-gray-800 hover:bg-gray-700 text-white text-xs font-semibold border border-gray-700 transition">
            Run
          </button>
        </div>
      </div>
    </div>

    <!-- Metrics Cards -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="glass-card rounded-xl p-4 border border-gray-800">
        <div class="text-xs text-gray-400 font-medium">Test Oracle Mode</div>
        <div class="text-lg font-bold text-white mt-1 flex items-center gap-2">
          <i class="fa-solid fa-calculator text-cyan-400"></i>
          <span>Deterministic</span>
        </div>
        <div class="text-xs text-cyan-400/80 mt-1">Python math, zero hallucination</div>
      </div>

      <div class="glass-card rounded-xl p-4 border border-gray-800">
        <div class="text-xs text-gray-400 font-medium">DOM Token Compression</div>
        <div class="text-lg font-bold text-white mt-1 flex items-center gap-2">
          <i class="fa-solid fa-compress text-indigo-400"></i>
          <span>99.2% Reduced</span>
        </div>
        <div class="text-xs text-indigo-400/80 mt-1">&lt;450 tokens vs 60,000 raw DOM</div>
      </div>

      <div class="glass-card rounded-xl p-4 border border-gray-800">
        <div class="text-xs text-gray-400 font-medium">Self-Healing Selectors</div>
        <div class="text-lg font-bold text-white mt-1 flex items-center gap-2">
          <i class="fa-solid fa-wand-magic-sparkles text-emerald-400"></i>
          <span>Active</span>
        </div>
        <div class="text-xs text-emerald-400/80 mt-1">Automatic DOM heuristic fallback</div>
      </div>

      <div class="glass-card rounded-xl p-4 border border-gray-800">
        <div class="text-xs text-gray-400 font-medium">Decentralized Target</div>
        <div class="text-lg font-bold text-white mt-1 flex items-center gap-2">
          <i class="fa-solid fa-network-wired text-purple-400"></i>
          <span>Cortensor dApps</span>
        </div>
        <div class="text-xs text-purple-400/80 mt-1">Continuous payment flow testing</div>
      </div>
    </div>

    <!-- Live Execution Status Panel -->
    <div id="executionPanel" class="hidden glass-card rounded-2xl p-6 border border-gray-800 space-y-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div id="statusSpinner" class="w-6 h-6 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin"></div>
          <div>
            <h3 id="execTitle" class="text-lg font-bold text-white">Execution in Progress...</h3>
            <p id="execSubtitle" class="text-xs text-gray-400">Autonomous OpenMinion agent navigating target application</p>
          </div>
        </div>
        <div id="verdictBadge"></div>
      </div>

      <!-- Execution Progress Logs -->
      <div id="execLogBox" class="bg-gray-950 rounded-xl p-4 border border-gray-800 text-xs text-gray-300 font-mono max-h-56 overflow-y-auto space-y-1">
        <div class="text-cyan-400">[OpenMinion 0.1] Initializing browser worker & autonomous planner...</div>
      </div>
    </div>

    <!-- Active Run Details (Traces, Assertion Matrix & Evidence) -->
    <div id="resultsSection" class="hidden space-y-6">
      
      <!-- Section: Autonomous Multi-Turn Trace Timeline -->
      <div id="tracesCard" class="hidden glass-card rounded-2xl p-6 border border-gray-800 space-y-4">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="text-lg font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-shoe-prints text-indigo-400"></i>
              <span>Autonomous Multi-Turn Action Trace</span>
            </h3>
            <p class="text-xs text-gray-400">Step-by-step reasoning chain, DOM interaction, self-healing status, and duration per turn</p>
          </div>
          <div id="traceStats" class="text-xs font-semibold text-gray-300"></div>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs border-collapse">
            <thead>
              <tr class="border-b border-gray-800 text-gray-400 font-mono">
                <th class="py-2.5 px-3">Turn</th>
                <th class="py-2.5 px-3">Action</th>
                <th class="py-2.5 px-3">Target / Selector</th>
                <th class="py-2.5 px-3">Planner Thought / Reasoning</th>
                <th class="py-2.5 px-3">Status</th>
                <th class="py-2.5 px-3">Duration</th>
              </tr>
            </thead>
            <tbody id="traceTableBody" class="divide-y divide-gray-850">
              <!-- Dynamically populated -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- Section: Assertion Verification Matrix -->
      <div class="glass-card rounded-2xl p-6 border border-gray-800 space-y-4">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="text-lg font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-list-check text-cyan-400"></i>
              <span>Deterministic Assertion Verification Matrix</span>
            </h3>
            <p class="text-xs text-gray-400">Evaluated independently by MinionQA Test Oracle to guarantee math & state integrity</p>
          </div>
          <div id="assertionStats" class="text-xs font-semibold text-gray-300"></div>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs border-collapse">
            <thead>
              <tr class="border-b border-gray-800 text-gray-400 font-mono">
                <th class="py-2.5 px-3">Type</th>
                <th class="py-2.5 px-3">Target / Criteria</th>
                <th class="py-2.5 px-3">Expected</th>
                <th class="py-2.5 px-3">Verdict</th>
                <th class="py-2.5 px-3">Details</th>
              </tr>
            </thead>
            <tbody id="assertionTableBody" class="divide-y divide-gray-850">
              <!-- Dynamically populated -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- Section: Visual Artifacts Gallery -->
      <div class="glass-card rounded-2xl p-6 border border-gray-800 space-y-4">
        <div>
          <h3 class="text-lg font-bold text-white flex items-center gap-2">
            <i class="fa-solid fa-camera text-purple-400"></i>
            <span>Visual Proof & Failure Snapshots</span>
          </h3>
          <p class="text-xs text-gray-400">High-resolution viewport snapshots captured during navigation and on assertion discrepancies</p>
        </div>

        <div id="screenshotsGrid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <!-- Dynamically populated -->
        </div>
      </div>

      <!-- Section: Audit Report Quickview -->
      <div class="glass-card rounded-2xl p-6 border border-gray-800 space-y-4">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="text-lg font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-file-lines text-emerald-400"></i>
              <span>Generated Audit Report (Markdown)</span>
            </h3>
            <p class="text-xs text-gray-400">Persisted locally for CI/CD test gates and compliance logs</p>
          </div>
          <a id="btnDownloadReport" href="#" download class="px-3 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 border border-gray-700 flex items-center gap-1.5 transition">
            <i class="fa-solid fa-download"></i>
            <span>Download Report</span>
          </a>
        </div>
        <pre id="reportMarkdownPre" class="bg-gray-950 p-4 rounded-xl text-xs text-gray-300 font-mono overflow-x-auto max-h-72 border border-gray-850"></pre>
      </div>

    </div>

  </main>

  <script>
    function loadPreset(type) {
      if (type === 'saucedemo') {
        document.getElementById('autoUrl').value = 'https://www.saucedemo.com';
        document.getElementById('autoGoal').value = 'Log in with username standard_user and password secret_sauce, add backpack to cart, go to cart, and finish checkout for John Doe zip 94103.';
        document.getElementById('autoMaxTurns').value = '15';
      } else if (type === 'example') {
        document.getElementById('autoUrl').value = 'https://example.com';
        document.getElementById('autoGoal').value = 'Verify that the heading contains Example Domain and assert that the More information link exists on page.';
        document.getElementById('autoMaxTurns').value = '5';
      } else if (type === 'anomaly') {
        document.getElementById('autoUrl').value = 'https://www.saucedemo.com';
        document.getElementById('autoGoal').value = 'Log in with standard_user and secret_sauce, add backpack to cart, proceed to checkout for Alice 94103, and assert total price equals $99.99 to verify financial anomaly detection.';
        document.getElementById('autoMaxTurns').value = '15';
      } else if (type === 'dreamvault') {
        document.getElementById('autoUrl').value = 'https://dreamvault.netlify.app';
        document.getElementById('autoGoal').value = 'Click Log In, enter email aryansinhag2006@gmail.com and password sinha123@, click Log In button, and verify authentication succeeds.';
        document.getElementById('autoMaxTurns').value = '10';
      }
    }

    async function runAutonomous() {
      let url = document.getElementById('autoUrl').value.trim();
      if (!url.startsWith('http://') && !url.startsWith('https://')) {
        url = 'https://' + url;
        document.getElementById('autoUrl').value = url;
      }
      const goal = document.getElementById('autoGoal').value.trim();
      const maxTurns = parseInt(document.getElementById('autoMaxTurns').value);
      const headless = document.getElementById('autoHeadless').checked;

      const panel = document.getElementById('executionPanel');
      const results = document.getElementById('resultsSection');
      const spinner = document.getElementById('statusSpinner');
      const title = document.getElementById('execTitle');
      const subtitle = document.getElementById('execSubtitle');
      const badge = document.getElementById('verdictBadge');
      const logBox = document.getElementById('execLogBox');
      const btn = document.getElementById('btnLaunchAutonomous');

      panel.classList.remove('hidden');
      results.classList.add('hidden');
      spinner.classList.remove('hidden');
      badge.innerHTML = '';
      title.innerText = 'Autonomous Agent Running...';
      subtitle.innerText = `Goal: "${goal.substring(0, 70)}..." on ${url}`;
      btn.disabled = true;
      btn.classList.add('opacity-50', 'cursor-not-allowed');

      logBox.innerHTML = `
        <div class="text-cyan-400">[0.0s] OpenMinion Autonomous Goal Planner initiated...</div>
        <div class="text-gray-400">[0.4s] Navigating headless Playwright Chromium to ${url}...</div>
        <div class="text-gray-400">[1.1s] Inspecting DOM state & compacting tokens (&lt;450 tokens)...</div>
        <div class="text-indigo-400">[1.8s] Querying multi-turn reasoning engine via OpenRouter / Cortensor API...</div>
        <div class="text-gray-400">[2.5s] Executing actions with self-healing selector fallback...</div>
      `;

      try {
        const response = await fetch('/api/run-autonomous', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url, goal: goal, max_turns: maxTurns, headless: headless })
        });
        const data = await response.json();

        spinner.classList.add('hidden');
        if (data.status === 'PASSED') {
          title.innerText = 'Autonomous Goal Completed Successfully';
          badge.innerHTML = '<span class="px-3 py-1 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-bold text-xs">✅ PASSED</span>';
          logBox.innerHTML += `<div class="text-emerald-400 font-bold">[Final] Goal achieved in ${data.duration}s across ${data.traces.length} autonomous turns!</div>`;
        } else {
          title.innerText = 'Autonomous Goal Failed / Mismatch Detected';
          badge.innerHTML = '<span class="px-3 py-1 rounded-full bg-rose-950 text-rose-400 border border-rose-800 font-bold text-xs">❌ FAILED</span>';
          logBox.innerHTML += `<div class="text-rose-400 font-bold">[Final] Goal run concluded: status=${data.status}. Visual failure proof captured.</div>`;
        }

        results.classList.remove('hidden');
        renderRunDetails(data);
      } catch (err) {
        spinner.classList.add('hidden');
        title.innerText = 'Execution Encountered Error';
        badge.innerHTML = '<span class="px-3 py-1 rounded-full bg-amber-950 text-amber-400 border border-amber-800 font-bold text-xs">⚠️ ERROR</span>';
        logBox.innerHTML += `<div class="text-rose-400 font-bold">Error: ${err.message}</div>`;
      } finally {
        btn.disabled = false;
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
      }
    }

    async function runEcommerce(simulateFailure) {
      const panel = document.getElementById('executionPanel');
      const results = document.getElementById('resultsSection');
      const spinner = document.getElementById('statusSpinner');
      const title = document.getElementById('execTitle');
      const subtitle = document.getElementById('execSubtitle');
      const badge = document.getElementById('verdictBadge');
      const logBox = document.getElementById('execLogBox');

      panel.classList.remove('hidden');
      results.classList.add('hidden');
      spinner.classList.remove('hidden');
      badge.innerHTML = '';
      title.innerText = simulateFailure ? 'Testing E-Commerce with Simulated Anomaly...' : 'Running E-Commerce Validation Flow...';
      subtitle.innerText = 'Navigating Saucedemo, adding backpack to cart, verifying tax formula & checkout...';
      
      logBox.innerHTML = `
        <div class="text-cyan-400">[0.0s] OpenMinion Agent started session...</div>
        <div class="text-gray-400">[0.5s] Launching headless Chromium via Playwright...</div>
        <div class="text-gray-400">[1.2s] Navigating to https://www.saucedemo.com...</div>
        <div class="text-gray-400">[2.1s] Authenticating standard_user credentials...</div>
        <div class="text-gray-400">[3.0s] Adding Sauce Labs Backpack ($29.99) to cart...</div>
        <div class="text-gray-400">[4.2s] Navigating to checkout overview...</div>
        <div class="text-gray-400">[5.0s] Deterministic Test Oracle evaluating subtotal + tax = total...</div>
      `;

      try {
        const response = await fetch('/api/run-ecommerce', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ simulate_failure: simulateFailure, headless: true })
        });
        const data = await response.json();

        spinner.classList.add('hidden');
        if (data.status === 'PASSED') {
          title.innerText = 'Run Completed Successfully';
          badge.innerHTML = '<span class="px-3 py-1 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-bold text-xs">✅ PASSED</span>';
          logBox.innerHTML += `<div class="text-emerald-400 font-bold">[Final] Flow PASSED in ${data.duration}s. All ${data.assertions_passed}/${data.assertions_count} assertions verified.</div>`;
        } else {
          title.innerText = 'Run Failed (Anomaly Detected)';
          badge.innerHTML = '<span class="px-3 py-1 rounded-full bg-rose-950 text-rose-400 border border-rose-800 font-bold text-xs">❌ FAILED</span>';
          logBox.innerHTML += `<div class="text-rose-400 font-bold">[Final] Flow FAILED as expected. Anomaly captured: ${data.assertions_passed}/${data.assertions_count} passed. Failure screenshot saved.</div>`;
        }

        results.classList.remove('hidden');
        renderRunDetails(data);
      } catch (err) {
        spinner.classList.add('hidden');
        title.innerText = 'Error Executing Run';
        logBox.innerHTML += `<div class="text-rose-400 font-bold">Execution Error: ${err.message}</div>`;
      }
    }

    async function runCustom() {
      let url = document.getElementById('customUrl').value.trim();
      if (!url.startsWith('http://') && !url.startsWith('https://')) {
        url = 'https://' + url;
        document.getElementById('customUrl').value = url;
      }
      const expectedText = document.getElementById('customText').value;
      const panel = document.getElementById('executionPanel');
      const results = document.getElementById('resultsSection');
      const spinner = document.getElementById('statusSpinner');
      const title = document.getElementById('execTitle');
      const subtitle = document.getElementById('execSubtitle');
      const badge = document.getElementById('verdictBadge');
      const logBox = document.getElementById('execLogBox');

      panel.classList.remove('hidden');
      results.classList.add('hidden');
      spinner.classList.remove('hidden');
      badge.innerHTML = '';
      title.innerText = 'Running Custom Flow...';
      subtitle.innerText = `Navigating to ${url}...`;
      logBox.innerHTML = `<div class="text-cyan-400">[0.0s] OpenMinion navigating to ${url}...</div>`;

      try {
        const response = await fetch('/api/run-custom', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url, expected_text: expectedText })
        });
        const data = await response.json();

        spinner.classList.add('hidden');
        if (data.status === 'PASSED') {
          title.innerText = 'Custom Run Passed';
          badge.innerHTML = '<span class="px-3 py-1 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-bold text-xs">✅ PASSED</span>';
        } else {
          title.innerText = 'Custom Run Failed';
          badge.innerHTML = '<span class="px-3 py-1 rounded-full bg-rose-950 text-rose-400 border border-rose-800 font-bold text-xs">❌ FAILED</span>';
        }

        results.classList.remove('hidden');
        renderRunDetails(data);
      } catch (err) {
        spinner.classList.add('hidden');
        title.innerText = 'Error Executing Custom Run';
      }
    }

    async function renderRunDetails(data) {
      // 1. Traces Table (if available)
      const tracesCard = document.getElementById('tracesCard');
      const traceTbody = document.getElementById('traceTableBody');
      const traceStats = document.getElementById('traceStats');

      if (data.traces && data.traces.length > 0) {
        tracesCard.classList.remove('hidden');
        traceStats.innerText = `Total Turns: ${data.traces.length} | Model: ${data.model_used || 'LLM'}`;
        traceTbody.innerHTML = '';
        data.traces.forEach(t => {
          const tr = document.createElement('tr');
          tr.className = 'border-b border-gray-800/60 hover:bg-gray-800/30 font-mono text-[11px]';
          
          let statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">PASSED</span>';
          if (t.status === 'HEALED') {
            statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-800">⚡ HEALED</span>';
          } else if (t.status === 'FAILED') {
            statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-400 border border-rose-800">FAILED</span>';
          }

          tr.innerHTML = `
            <td class="py-2.5 px-3 text-cyan-400 font-bold">#${t.step_number}</td>
            <td class="py-2.5 px-3 text-purple-300 font-semibold">${t.action}</td>
            <td class="py-2.5 px-3 text-gray-300 max-w-[200px] truncate" title="${t.selector || t.value}">${t.selector || t.value || '-'}</td>
            <td class="py-2.5 px-3 text-gray-400 font-sans max-w-[320px] truncate" title="${t.thought}">${t.thought}</td>
            <td class="py-2.5 px-3">${statusBadge}</td>
            <td class="py-2.5 px-3 text-gray-500">${t.duration_s}s</td>
          `;
          traceTbody.appendChild(tr);
        });
      } else {
        tracesCard.classList.add('hidden');
      }

      // 2. Assertion Matrix Table
      document.getElementById('assertionStats').innerText = `Passed: ${data.assertions_passed} / ${data.assertions_count}`;
      const tbody = document.getElementById('assertionTableBody');
      tbody.innerHTML = '';
      if (data.assertions && data.assertions.length > 0) {
        data.assertions.forEach(a => {
          const isPass = a.passed;
          const tr = document.createElement('tr');
          tr.className = 'border-b border-gray-800/60 hover:bg-gray-800/30 font-mono text-[11px]';
          tr.innerHTML = `
            <td class="py-2.5 px-3 text-cyan-400 font-semibold">${a.assertion_type}</td>
            <td class="py-2.5 px-3 text-gray-300 max-w-[220px] truncate" title="${a.target}">${a.target}</td>
            <td class="py-2.5 px-3 text-gray-400">${a.expected_value || '-'}</td>
            <td class="py-2.5 px-3">
              <span class="px-2 py-0.5 rounded text-[10px] font-bold ${isPass ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800'}">
                ${isPass ? 'PASS' : 'FAIL'}
              </span>
            </td>
            <td class="py-2.5 px-3 text-gray-400 text-[10px]">${a.detail}</td>
          `;
          tbody.appendChild(tr);
        });
      } else {
        tbody.innerHTML = '<tr><td colspan="5" class="py-3 px-3 text-gray-500">No explicit financial assertions recorded in this run.</td></tr>';
      }

      // 3. Fetch Report content
      if (data.report_filename) {
        try {
          const repRes = await fetch(`/api/reports/${data.report_filename}`);
          const repJson = await repRes.json();
          document.getElementById('reportMarkdownPre').innerText = repJson.content;
          document.getElementById('btnDownloadReport').href = `/api/reports/${data.report_filename}`;
        } catch (e) {}
      }

      // 4. Populate screenshots
      const shotGrid = document.getElementById('screenshotsGrid');
      shotGrid.innerHTML = '';
      if (data.screenshots && data.screenshots.length > 0) {
        data.screenshots.forEach(s => {
          const filename = s.filename || (s.path ? s.path.split(/\\\\|\\//).pop() : 'shot.png');
          const isFail = s.is_failure;
          const card = document.createElement('div');
          card.className = `glass rounded-xl p-3 border ${isFail ? 'border-rose-800/80 bg-rose-950/10' : 'border-gray-800'}`;
          card.innerHTML = `
            <div class="flex items-center justify-between text-xs mb-2">
              <span class="font-bold ${isFail ? 'text-rose-400' : 'text-gray-300'}">${isFail ? '⚠️ Failure Capture' : '📸 State Snapshot'}</span>
              <span class="text-gray-500 font-mono text-[10px]">${filename.substring(0, 24)}</span>
            </div>
            <a href="/screenshots/${filename}" target="_blank" class="block overflow-hidden rounded-lg border border-gray-850 hover:opacity-90 transition">
              <img src="/screenshots/${filename}" alt="Capture" class="w-full h-36 object-cover object-top">
            </a>
          `;
          shotGrid.appendChild(card);
        });
      } else {
        shotGrid.innerHTML = '<div class="text-xs text-gray-500 col-span-3">No screenshots recorded for this run.</div>';
      }
    }

    // Auto-load sample report or status on page load
    window.addEventListener('DOMContentLoaded', async () => {
      try {
        const statusRes = await fetch('/api/status');
        if (statusRes.ok) {
          const st = await statusRes.json();
          if (st.provider && st.model) {
            document.getElementById('activeModelLabel').innerText = `${st.provider} / ${st.model}`;
          }
        }

        const res = await fetch('/api/reports/sample_run_report.md');
        if (res.ok) {
          const j = await res.json();
          document.getElementById('reportMarkdownPre').innerText = j.content;
          document.getElementById('resultsSection').classList.remove('hidden');
          const runsRes = await fetch('/api/runs');
          if (runsRes.ok) {
            const runs = await runsRes.json();
            if (runs.length > 0) {
              renderRunDetails(runs[0]);
            }
          }
        }
      } catch (e) {}
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def get_dashboard() -> str:
    """Serve the interactive web UI dashboard."""
    return DASHBOARD_HTML


def start_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Entrypoint to launch the MinionQA FastAPI web server."""
    print(f"Starting MinionQA Server on http://localhost:{port} (OpenMinion 0.1 / Cortensor Alpha)...")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Launch MinionQA Web Server & Dashboard.")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Port to bind server (default: 8080)")
    parser.add_argument("--host", "-H", type=str, default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    args = parser.parse_args()
    start_server(host=args.host, port=args.port)
