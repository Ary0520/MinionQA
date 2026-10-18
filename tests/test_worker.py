"""
Unit tests for MinionQA Background Task Worker (minion_qa.worker).

Tests async queue execution, threadpool delegation, job state transitions,
RCA root-cause classification, and automatic database persistence.
"""

import asyncio
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from minion_qa.database import Base, SessionLocal, init_db
from minion_qa.models import CanaryRun, CanaryRunStatus, RunAssertion, RunScreenshot
from minion_qa.worker import (
    CanaryExecutionWorker,
    JobRecord,
    classify_rca,
    default_worker,
)


def test_classify_rca():
    """Verify Root Cause Analysis categorization logic."""
    assert classify_rca(None, has_failed_assertion=False) == "NONE"
    assert classify_rca("Navigation timeout after 15000ms") == "TIMEOUT"
    assert classify_rca("[POLICY_DENIED] Action intercepted by policy gating") == "POLICY_BLOCKED"
    assert classify_rca("Page failed to load: net::ERR_CONNECTION_REFUSED") == "NETWORK_ERROR"
    assert classify_rca("Waiting for locator '#btn-cart' failed") == "FLAKY_SELECTOR"
    assert classify_rca(None, has_failed_assertion=True) == "PRODUCT_BUG"
    assert classify_rca("Unexpected layout shift", has_failed_assertion=False) == "ANOMALY"


def test_worker_sync_and_async_jobs():
    """Test worker executing synchronous and asynchronous functions."""
    async def _run():
        worker = CanaryExecutionWorker(max_concurrency=1)
        worker.start()

        try:
            # Sync callable
            def sync_task(x: int, y: int) -> dict:
                return {"status": "PASSED", "sum": x + y}

            # Async callable
            async def async_task(msg: str) -> dict:
                await asyncio.sleep(0.01)
                return {"status": "PASSED", "echo": msg}

            # Enqueue sync job
            job1 = worker.enqueue_job("job_sync_1", "Sync Math", sync_task, 4, 6)
            assert job1.status in ("pending", "running")

            # Enqueue async job
            job2 = worker.enqueue_job("job_async_2", "Async Echo", async_task, "hello")
            assert job2.status in ("pending", "running")

            # Wait for jobs to complete
            for _ in range(50):
                j1 = worker.get_job("job_sync_1")
                j2 = worker.get_job("job_async_2")
                if j1 and j1.status == "completed" and j2 and j2.status == "completed":
                    break
                await asyncio.sleep(0.05)

            j1 = worker.get_job("job_sync_1")
            assert j1.status == "completed"
            assert j1.result == {"status": "PASSED", "sum": 10}
            assert j1.duration_s >= 0.0

            j2 = worker.get_job("job_async_2")
            assert j2.status == "completed"
            assert j2.result == {"status": "PASSED", "echo": "hello"}

        finally:
            await worker.stop()

    asyncio.run(_run())


def test_worker_job_failure_and_rca():
    """Test worker handling unhandled exceptions and assigning RCA categories."""
    async def _run():
        worker = CanaryExecutionWorker(max_concurrency=1)
        worker.start()

        try:
            def failing_task():
                raise TimeoutError("Page load timed out after 10.0s")

            job = worker.enqueue_job("job_fail_1", "Failing Task", failing_task)

            for _ in range(30):
                j = worker.get_job("job_fail_1")
                if j and j.status == "failed":
                    break
                await asyncio.sleep(0.05)

            res_job = worker.get_job("job_fail_1")
            assert res_job.status == "failed"
            assert "timed out" in res_job.error.lower()
            assert res_job.rca_category == "TIMEOUT"

        finally:
            await worker.stop()

    asyncio.run(_run())


def test_worker_live_steps():
    """Test appending live step traces to an active job."""
    async def _run():
        worker = CanaryExecutionWorker(max_concurrency=1)
        worker.start()

        try:
            job = worker.enqueue_job("job_steps_1", "Steps Task", lambda: {"status": "PASSED"})
            worker.append_step("job_steps_1", {"step_number": 1, "thought": "Navigating to URL", "action": "navigate"})
            worker.append_step("job_steps_1", {"step_number": 2, "thought": "Clicking button", "action": "click"})

            j = worker.get_job("job_steps_1")
            assert len(j.live_steps) == 2
            assert j.current_step == "Clicking button"

        finally:
            await worker.stop()

    asyncio.run(_run())


def test_worker_database_sync():
    """Test that worker seamlessly persists execution results into CanaryRun DB table."""
    async def _run():
        init_db()
        job_id = f"test_run_db_{int(time.time() * 1000)}"

        # Create run row in DB
        with SessionLocal() as db:
            run = CanaryRun(
                job_id=job_id,
                name="DB Sync Test Run",
                goal="Test DB worker sync",
                target_url="https://example.com",
                status=CanaryRunStatus.PENDING,
            )
            db.add(run)
            db.commit()

        worker = CanaryExecutionWorker(max_concurrency=1)
        worker.start()

        try:
            def simulated_qa_flow():
                return {
                    "status": "PASSED",
                    "total_steps": 3,
                    "assertions_count": 1,
                    "assertions_passed": 1,
                    "assertions": [
                        {
                            "assertion_type": "page_contains_text",
                            "target": "Welcome",
                            "expected_value": "Welcome",
                            "actual_value": "Welcome",
                            "passed": True,
                            "detail": "Found text",
                        }
                    ],
                    "screenshots": [
                        {"path": "screenshots/db_shot.png", "filename": "db_shot.png", "is_failure": False}
                    ],
                    "report_path": "reports/db_report.md",
                }

            worker.enqueue_job(job_id, "DB Sync Task", simulated_qa_flow)

            for _ in range(40):
                j = worker.get_job(job_id)
                if j and j.status == "completed":
                    break
                await asyncio.sleep(0.05)

            # Verify DB updated
            with SessionLocal() as db:
                db_run = db.query(CanaryRun).filter(CanaryRun.job_id == job_id).first()
                assert db_run is not None
                assert db_run.status == CanaryRunStatus.COMPLETED
                assert db_run.is_successful is True
                assert db_run.verdict == "PASSED"
                assert db_run.rca_category == "NONE"
                assert db_run.assertions_count == 1
                assert db_run.assertions_passed == 1
                assert len(db_run.screenshots) == 1
                assert db_run.screenshots[0].filename == "db_shot.png"
                assert len(db_run.assertions) == 1
                assert db_run.assertions[0].passed is True

        finally:
            await worker.stop()

    asyncio.run(_run())
