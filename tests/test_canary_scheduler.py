"""
Unit and integration tests for MinionQA Canary Scheduler & 24/7 Watchdog.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from minion_qa.canary_explorer import CanaryMap, DiscoveredNode
from minion_qa.canary_scheduler import (
    CanaryWatchdog,
    get_target_schedule,
    list_schedules,
    run_canary_for_target,
    schedule_target,
)
from minion_qa.database import SessionLocal, init_db
from minion_qa.models import CanaryMapNode, CanarySchedule, CanaryTarget, utc_now
from minion_qa.server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def ensure_db():
    init_db()


def test_schedule_target_crud():
    """Verify creating, reading, and updating canary schedules."""
    with SessionLocal() as db:
        target = CanaryTarget(
            name="Watchdog Test Target",
            base_url="https://watchdog.test.local",
            created_at=utc_now(),
        )
        db.add(target)
        db.commit()
        db.refresh(target)
        tid = target.id

    # 1. Create schedule
    res = schedule_target(target_id=tid, interval_minutes=30, enabled=True)
    assert res["target_id"] == tid
    assert res["interval_minutes"] == 30
    assert res["enabled"] is True
    assert res["next_run_at"] is not None

    # 2. Read schedule
    read_res = get_target_schedule(target_id=tid)
    assert read_res is not None
    assert read_res["interval_minutes"] == 30

    # 3. Update schedule
    upd_res = schedule_target(target_id=tid, interval_minutes=15, enabled=False)
    assert upd_res["interval_minutes"] == 15
    assert upd_res["enabled"] is False
    assert upd_res["next_run_at"] is None

    # 4. List schedules
    all_scheds = list_schedules()
    assert any(s["target_id"] == tid for s in all_scheds)


def test_run_canary_for_target_regression_detection():
    """Verify regression detection when a previously passing target fails."""
    with SessionLocal() as db:
        target = CanaryTarget(
            name="Regression Sentinel Target",
            base_url="https://regression.example.com",
            created_at=utc_now(),
        )
        db.add(target)
        db.commit()
        db.refresh(target)
        tid = target.id

    schedule_target(target_id=tid, interval_minutes=60, enabled=True)

    # Set previous verdict to PASSED
    with SessionLocal() as db:
        s = db.query(CanarySchedule).filter(CanarySchedule.target_id == tid).first()
        s.last_verdict = "PASSED"
        db.commit()

    # Mock planner returning FAILED
    mock_exec_result = MagicMock()
    mock_exec_result.status = "FAILED"
    mock_exec_result.duration_s = 2.5
    mock_exec_result.report_path = "/tmp/fake_report.md"

    mock_map = CanaryMap(
        target_id=tid,
        base_url="https://regression.example.com",
        nodes=[
            DiscoveredNode(
                node_type="page",
                label="Swap Interface",
                url="https://regression.example.com/swap",
                business_category="swap",
                suggested_prompt="Test token swap calculation",
            )
        ],
    )

    with patch("minion_qa.canary_scheduler.get_canary_map_for_target", return_value=mock_map), \
         patch("minion_qa.autonomous_planner.AutonomousGoalPlanner.execute_goal", return_value=mock_exec_result):

        result = run_canary_for_target(tid)
        assert result["status"] == "FAILED"
        assert result["verdict"] == "REGRESSION"
        assert result["is_regression"] is True
        assert result["previous_verdict"] == "PASSED"

    # Verify database updated regression_count
    with SessionLocal() as db:
        s = db.query(CanarySchedule).filter(CanarySchedule.target_id == tid).first()
        assert s.last_verdict == "REGRESSION"
        assert s.regression_count >= 1
        assert s.total_runs >= 1


def test_watchdog_dispatch_due_schedules():
    """Verify watchdog ticker detects and dispatches due schedules."""
    with SessionLocal() as db:
        target = CanaryTarget(
            name="Ticker Test Target",
            base_url="https://ticker.example.com",
            created_at=utc_now(),
        )
        db.add(target)
        db.commit()
        db.refresh(target)
        tid = target.id

        # Schedule due right now
        sched = CanarySchedule(
            target_id=tid,
            interval_minutes=10,
            enabled=True,
            next_run_at=utc_now() - timedelta(minutes=5),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(sched)
        db.commit()

    watchdog = CanaryWatchdog(check_interval_seconds=10)

    async def _run_check():
        return await watchdog.check_and_dispatch_due_schedules()

    dispatched = asyncio.run(_run_check())
    assert len(dispatched) >= 1
    assert any(str(tid) in j for j in dispatched)


def test_server_schedule_endpoints():
    """Test REST API endpoints for canary scheduling."""
    # 1. Register target
    t_resp = client.post("/api/targets", json={
        "name": "Endpoint Target",
        "base_url": "https://api.example.com",
    })
    assert t_resp.status_code == 200
    target_id = t_resp.json()["id"]

    # 2. Configure schedule via POST /api/targets/{id}/schedule
    sched_resp = client.post(f"/api/targets/{target_id}/schedule?interval_minutes=45&enabled=true")
    assert sched_resp.status_code == 200
    sched_data = sched_resp.json()
    assert sched_data["target_id"] == target_id
    assert sched_data["interval_minutes"] == 45
    assert sched_data["enabled"] is True

    # 3. GET /api/targets/{id}/schedule
    get_resp = client.get(f"/api/targets/{target_id}/schedule")
    assert get_resp.status_code == 200
    assert get_resp.json()["interval_minutes"] == 45

    # 4. GET /api/schedules
    list_resp = client.get("/api/schedules")
    assert list_resp.status_code == 200
    assert any(s["target_id"] == target_id for s in list_resp.json())

    # 5. POST /api/targets/{id}/trigger (with mock)
    mock_run_res = {
        "job_id": "mock_canary_job",
        "target_id": target_id,
        "target_name": "Endpoint Target",
        "base_url": "https://api.example.com",
        "node_tested": "Landing Page",
        "prompt_tested": "Smoke test",
        "status": "PASSED",
        "verdict": "PASSED",
        "is_regression": False,
        "previous_verdict": None,
        "duration_seconds": 1.2,
        "report_path": None,
    }

    with patch("minion_qa.canary_scheduler.run_canary_for_target", return_value=mock_run_res):
        trig_resp = client.post(f"/api/targets/{target_id}/trigger")
        assert trig_resp.status_code == 200
        data = trig_resp.json()
        assert data["job_id"] == "mock_canary_job"
        assert data["verdict"] == "PASSED"
