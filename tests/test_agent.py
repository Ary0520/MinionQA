"""
Unit Tests for MinionQA Agent Runtime & Flow Orchestration.

Tests OpenMinion 0.1 Agent initialization, context manager lifecycle,
deterministic flow execution, and reporting.
"""

import os
from pathlib import Path
import pytest
from openminion import Agent

from minion_qa.agent import MinionQAAgent, QA_SYSTEM_PROMPT
from minion_qa.config import MinionQAConfig


def test_agent_initialization():
    cfg = MinionQAConfig(headless=True)
    agent = MinionQAAgent(config=cfg, session_id="test_sess_001")

    assert agent.session_id == "test_sess_001"
    assert len(agent.tools) == 4
    tool_names = [getattr(t, "tool_decl").name if hasattr(t, "tool_decl") else t.__name__ for t in agent.tools]
    assert "navigate_and_snapshot" in tool_names
    assert "execute_action" in tool_names
    assert "verify_assertion" in tool_names
    assert "generate_qa_audit_report" in tool_names

    # Check OpenMinion Agent instance
    assert isinstance(agent.agent, Agent)
    assert agent.agent.session_id == "test_sess_001"
    agent.close()


def test_agent_context_manager():
    with MinionQAAgent(session_id="cm_test_session") as agent:
        assert agent.session_id == "cm_test_session"


def test_agent_run_flow_passed(tmp_path):
    report_file = tmp_path / "test_flow_report.md"

    html_page = """
    data:text/html,
    <html>
      <head><title>Minion Test Page</title></head>
      <body>
        <h1 id="header">Autonomous QA Gateway</h1>
        <div class="cart-total">$49.99</div>
        <button id="checkout-btn">Checkout</button>
      </body>
    </html>
    """.strip().replace("\n", "")

    steps = [
        {"type": "action", "action": "wait", "value": "0.1", "description": "Wait for DOM"},
        {"type": "assertion", "assertion_type": "page_contains_text", "target": "Autonomous QA Gateway", "description": "Verify header"},
        {"type": "assertion", "assertion_type": "price_equals", "target": "$49.99", "expected": "$49.99", "description": "Verify price"},
    ]

    with MinionQAAgent(session_id="flow_pass_session") as agent:
        result = agent.run_flow(
            name="Smoke Flow Test",
            target_url=html_page,
            steps=steps,
            report_output_path=str(report_file),
        )

    assert result["status"] == "PASSED"
    assert result["assertions_count"] == 2
    assert result["assertions_passed"] == 2
    assert Path(result["report_path"]).exists()
    assert report_file.read_text(encoding="utf-8").startswith("# MinionQA Audit Report:")


def test_agent_run_flow_failed(tmp_path):
    report_file = tmp_path / "test_failed_report.md"

    html_page = """
    data:text/html,
    <html>
      <body>
        <p id="msg">Access Granted</p>
      </body>
    </html>
    """.strip().replace("\n", "")

    steps = [
        {"type": "assertion", "assertion_type": "page_contains_text", "target": "NonExistentText", "description": "Should fail"},
    ]

    with MinionQAAgent(session_id="flow_fail_session") as agent:
        result = agent.run_flow(
            name="Failing Flow Test",
            target_url=html_page,
            steps=steps,
            report_output_path=str(report_file),
        )

    assert result["status"] == "FAILED"
    assert result["assertions_count"] == 1
    assert result["assertions_passed"] == 0
    assert Path(result["report_path"]).exists()
    assert "❌ FAILED" in report_file.read_text(encoding="utf-8")
