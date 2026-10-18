"""
Unit tests for MinionQA Web Server and REST API Endpoints.
"""

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import pytest

from minion_qa.server import app
from minion_qa.autonomous_planner import AutonomousPlanResult, PlannerStepTrace

client = TestClient(app)


def test_get_dashboard_html():
    """Verify that the dashboard root returns HTTP 200 with HTML content."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "MinionQA" in response.text
    assert "Autonomous Goal Studio" in response.text
    assert "OpenMinion 0.1" in response.text


def test_get_api_status():
    """Verify that /api/status returns telemetry and system capabilities."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["openminion_sdk"] == "0.1.6"
    assert "Cortensor" in data["network"]
    assert data["action_policy_gating"] == "ENABLED (Safe read/write confirmation preview)"
    assert data["self_healing_selectors"] == "ACTIVE (Ranked DOM heuristic fallback)"
    assert "provider" in data
    assert "model" in data


def test_get_api_runs_and_reports():
    """Verify that runs and reports endpoints return lists."""
    runs_res = client.get("/api/runs")
    assert runs_res.status_code == 200
    assert isinstance(runs_res.json(), list)

    rep_res = client.get("/api/reports")
    assert rep_res.status_code == 200
    assert isinstance(rep_res.json(), list)


@patch("minion_qa.server.AutonomousGoalPlanner")
def test_post_run_autonomous_endpoint(mock_planner_class):
    """Verify POST /api/run-autonomous triggers the planner and returns structured run data."""
    mock_planner = MagicMock()
    mock_planner_class.return_value = mock_planner

    mock_traces = [
        PlannerStepTrace(
            step_number=1,
            thought="Navigate to login",
            action="navigate",
            value="https://example.com",
            result="Navigated to https://example.com",
            status="PASSED",
            duration_s=0.5,
        ),
        PlannerStepTrace(
            step_number=2,
            thought="Verify heading",
            action="assert_text",
            selector="h1",
            result="Found heading Example Domain",
            status="PASSED",
            duration_s=0.3,
        ),
    ]

    mock_result = AutonomousPlanResult(
        goal="Test heading on example.com",
        target_url="https://example.com",
        status="PASSED",
        total_steps=2,
        duration_s=0.8,
        session_id="test_sess_123",
        provider_used="openrouter",
        model_used="openai/gpt-4o-mini",
        traces=mock_traces,
        assertions_count=1,
        assertions_passed=1,
        report_path="reports/test_rep.md",
        screenshots=["screenshots/test.png"],
    )
    mock_planner.execute_goal.return_value = mock_result

    payload = {
        "url": "https://example.com",
        "goal": "Test heading on example.com",
        "max_turns": 5,
        "headless": True,
    }
    response = client.post("/api/run-autonomous", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "PASSED"
    assert data["target_url"] == "https://example.com"
    assert data["duration"] == 0.8
    assert len(data["traces"]) == 2
    assert data["traces"][0]["action"] == "navigate"
    assert data["traces"][1]["action"] == "assert_text"

    # Verify run was recorded in /api/runs
    history_res = client.get("/api/runs")
    assert history_res.status_code == 200
    history = history_res.json()
    assert len(history) >= 1
    assert any(r["session_id"] == "test_sess_123" for r in history)

    # Verify run can be fetched via /api/runs/{job_id}
    job_id = data["job_id"]
    single_res = client.get(f"/api/runs/{job_id}")
    assert single_res.status_code == 200
    single_data = single_res.json()
    assert single_data["job_id"] == job_id
    assert single_data["verdict"] == "PASSED"
    assert single_data["status"] in ("completed", "PASSED")


def test_canary_targets_crud():
    """Verify creating and listing CanaryTarget monitored applications."""
    target_payload = {
        "name": "DreamVault Staking Sentinel",
        "base_url": "https://dreamvault.netlify.app",
        "description": "Cortensor Web3 Staking and Yield Vault",
        "login_hint": "MetaMask wallet connect",
        "schedule_interval_minutes": 15,
    }

    create_res = client.post("/api/targets", json=target_payload)
    assert create_res.status_code == 200
    created = create_res.json()
    assert created["id"] is not None
    assert created["name"] == "DreamVault Staking Sentinel"
    assert created["base_url"] == "https://dreamvault.netlify.app"
    assert created["schedule_interval_minutes"] == 15

    list_res = client.get("/api/targets")
    assert list_res.status_code == 200
    targets = list_res.json()
    assert len(targets) >= 1
    assert any(t["name"] == "DreamVault Staking Sentinel" for t in targets)


def test_get_learned_locators_endpoint():
    """Verify GET /api/locators returns learned locators."""
    response = client.get("/api/locators")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_run_attestation_endpoint():
    """Verify GET /api/runs/{job_id}/attestation returns cryptographic network proof."""
    # First create a run
    custom_payload = {
        "name": "Attestation Verification Test",
        "url": "https://example.com",
        "expected_text": "Example",
    }
    run_res = client.post("/api/run-custom", json=custom_payload)
    assert run_res.status_code == 200
    job_id = run_res.json()["job_id"]

    # Query attestation endpoint
    att_res = client.get(f"/api/runs/{job_id}/attestation")
    assert att_res.status_code == 200
    att_data = att_res.json()
    assert "attestation" in att_data
    assert "verification" in att_data
    assert att_data["verification"]["verified"] is True
    assert "cort_miner_" in att_data["attestation"]["miner_id"]
    assert len(att_data["attestation"]["raw_response_hash"]) == 64

