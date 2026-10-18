"""
MinionQA Background Task Worker & Execution Queue.

Decouples long-running autonomous QA runs and Playwright browser executions
from FastAPI request threads using an in-process asyncio.Queue.
Provides:
- Non-blocking job enqueueing and lifecycle management
- In-flight live step streaming and telemetry
- Transparent database sync to CanaryRun ORM models
- Automatic Root Cause Analysis (RCA) classification
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from minion_qa.database import SessionLocal
from minion_qa.models import CanaryRun, CanaryRunStatus, RunAssertion, RunScreenshot

logger = logging.getLogger("minion_qa.worker")


def utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def classify_rca(
    error_message: Optional[str],
    has_failed_assertion: bool = False,
    is_anomaly: bool = False,
) -> str:
    """
    Classify root cause category based on error context (inspired by Leaka AI).
    Categories: PRODUCT_BUG | TIMEOUT | FLAKY_SELECTOR | POLICY_BLOCKED | NETWORK_ERROR | ANOMALY | NONE
    """
    if not error_message and not has_failed_assertion and not is_anomaly:
        return "NONE"

    msg = (error_message or "").lower()

    if "timeout" in msg or "timed out" in msg:
        return "TIMEOUT"
    if "policy_denied" in msg or "policy" in msg:
        return "POLICY_BLOCKED"
    if "net::" in msg or "404" in msg or "connection refused" in msg or "cannot navigate" in msg:
        return "NETWORK_ERROR"
    if "selector" in msg or "waiting for locator" in msg or "element not found" in msg:
        return "FLAKY_SELECTOR"
    if has_failed_assertion or is_anomaly:
        return "PRODUCT_BUG"

    return "ANOMALY"


@dataclass
class JobRecord:
    """In-memory representation of an active or completed background job."""

    job_id: str
    name: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_s: float = 0.0
    live_steps: List[Dict[str, Any]] = field(default_factory=list)
    current_step: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    rca_category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert job state to serializable dictionary."""
        return asdict(self)


class CanaryExecutionWorker:
    """
    In-process asynchronous job queue and worker engine.
    Runs background QA jobs sequentially or concurrently without external Redis.
    """

    def __init__(self, max_concurrency: int = 1) -> None:
        self.max_concurrency = max_concurrency
        self._queue: asyncio.Queue = asyncio.Queue()
        self._jobs: Dict[str, JobRecord] = {}
        self._worker_tasks: List[asyncio.Task] = []
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        """Start background queue consumer workers."""
        if self._is_running:
            return

        self._is_running = True
        loop = asyncio.get_event_loop()
        for i in range(self.max_concurrency):
            t = loop.create_task(self._worker_loop(worker_idx=i))
            self._worker_tasks.append(t)
        logger.info(
            "CanaryExecutionWorker started with concurrency %d", self.max_concurrency
        )

    async def stop(self) -> None:
        """Gracefully stop queue workers."""
        self._is_running = False
        for t in self._worker_tasks:
            t.cancel()
        self._worker_tasks.clear()
        logger.info("CanaryExecutionWorker stopped")

    def enqueue_job(
        self,
        job_id: str,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> JobRecord:
        """
        Register and enqueue a job. Thread-safe.
        `func` can be a normal synchronous function (run in threadpool) or an async coroutine.
        """
        record = JobRecord(job_id=job_id, name=name, status="pending")
        self._jobs[job_id] = record

        # Ensure worker is running if an active loop is running
        try:
            loop = asyncio.get_running_loop()
            if not self._is_running:
                self.start()
            self._queue.put_nowait((job_id, func, args, kwargs))
        except RuntimeError:
            # If no running loop yet, worker will pick it up when started
            self._queue.put_nowait((job_id, func, args, kwargs))

        return record

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Fetch current state of a job."""
        return self._jobs.get(job_id)

    def list_jobs(self) -> List[JobRecord]:
        """List all tracked jobs sorted by creation time descending."""
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def append_step(self, job_id: str, step_data: Dict[str, Any]) -> None:
        """Add an incremental step trace to a job and optionally sync to DB."""
        job = self._jobs.get(job_id)
        if not job:
            return

        job.live_steps.append(step_data)
        desc = step_data.get("thought") or step_data.get("action") or "Running step"
        job.current_step = str(desc)[:120]

        # Sync live steps to database if record exists
        try:
            with SessionLocal() as db:
                db_run = db.query(CanaryRun).filter(CanaryRun.job_id == job_id).first()
                if db_run:
                    db_run.live_steps = json.dumps(job.live_steps)
                    db.commit()
        except Exception as exc:
            logger.debug("Could not sync live step to DB for %s: %s", job_id, exc)

    async def _worker_loop(self, worker_idx: int) -> None:
        """Consumer loop reading jobs from queue."""
        while self._is_running:
            try:
                job_id, func, args, kwargs = await self._queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error reading from task queue: %s", e)
                continue

            job = self._jobs.get(job_id)
            if not job:
                self._queue.task_done()
                continue

            # Update job to running
            job.status = "running"
            job.started_at = time.time()
            self._sync_db_status(job_id, CanaryRunStatus.RUNNING, started_at=utc_now())

            try:
                # Execute sync callable in threadpool to keep event loop responsive
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(func, *args, **kwargs)

                # Process successful execution
                duration = round(time.time() - (job.started_at or job.created_at), 2)
                job.duration_s = duration
                job.completed_at = time.time()
                job.result = result if isinstance(result, dict) else {"result": str(result)}

                # Determine if result contains test failure
                status_str = "completed"
                is_success = True
                verdict = "PASSED"
                err_msg = None

                if isinstance(result, dict):
                    res_status = str(result.get("status", "")).upper()
                    if res_status in ("FAILED", "ERROR"):
                        status_str = "failed"
                        is_success = False
                        verdict = "FAILED"
                        err_msg = result.get("error_message") or result.get("result_summary")
                elif hasattr(result, "status"):
                    res_status = str(getattr(result, "status", "")).upper()
                    if res_status in ("FAILED", "ERROR"):
                        status_str = "failed"
                        is_success = False
                        verdict = "FAILED"
                        err_msg = getattr(result, "error_message", None)

                job.status = status_str
                job.error = err_msg
                job.rca_category = classify_rca(
                    err_msg, has_failed_assertion=(not is_success)
                )

                self._sync_db_completed(
                    job_id=job_id,
                    status=CanaryRunStatus.COMPLETED if is_success else CanaryRunStatus.FAILED,
                    duration_s=duration,
                    verdict=verdict,
                    result_data=job.result,
                    rca_category=job.rca_category,
                    error_message=err_msg,
                )

            except Exception as exc:
                duration = round(time.time() - (job.started_at or job.created_at), 2)
                job.duration_s = duration
                job.completed_at = time.time()
                job.status = "failed"
                job.error = str(exc)
                job.rca_category = classify_rca(str(exc), has_failed_assertion=True)

                logger.error("Job %s failed with exception: %s", job_id, exc, exc_info=True)
                self._sync_db_completed(
                    job_id=job_id,
                    status=CanaryRunStatus.FAILED,
                    duration_s=duration,
                    verdict="FAILED",
                    result_data={"error": str(exc)},
                    rca_category=job.rca_category,
                    error_message=str(exc),
                )

            finally:
                self._queue.task_done()

    def _sync_db_status(
        self,
        job_id: str,
        status: CanaryRunStatus,
        started_at: Optional[datetime] = None,
    ) -> None:
        """Update run status in database."""
        try:
            with SessionLocal() as db:
                db_run = db.query(CanaryRun).filter(CanaryRun.job_id == job_id).first()
                if db_run:
                    db_run.status = status
                    if started_at:
                        db_run.started_at = started_at
                    db.commit()
        except Exception as exc:
            logger.debug("DB status sync failed for %s: %s", job_id, exc)

    def _sync_db_completed(
        self,
        job_id: str,
        status: CanaryRunStatus,
        duration_s: float,
        verdict: str,
        result_data: Dict[str, Any],
        rca_category: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Sync final completion results and artifacts to database."""
        try:
            with SessionLocal() as db:
                db_run = db.query(CanaryRun).filter(CanaryRun.job_id == job_id).first()
                if not db_run:
                    return

                db_run.status = status
                db_run.completed_at = utc_now()
                db_run.duration_seconds = duration_s
                db_run.verdict = verdict
                db_run.is_successful = (verdict == "PASSED")
                db_run.rca_category = rca_category
                db_run.error_message = error_message

                if "report_path" in result_data:
                    db_run.report_path = result_data["report_path"]
                    db_run.report_filename = Path(result_data["report_path"]).name
                if "total_steps" in result_data:
                    db_run.total_turns = result_data["total_steps"]
                if "assertions_count" in result_data:
                    db_run.assertions_count = result_data["assertions_count"]
                if "assertions_passed" in result_data:
                    db_run.assertions_passed = result_data["assertions_passed"]
                if "assertions" in result_data:
                    db_run.assertion_results = json.dumps(result_data["assertions"])
                if "traces" in result_data:
                    db_run.steps_log = json.dumps(result_data["traces"])

                # Persist screenshots as RunScreenshot rows if present
                screenshots = result_data.get("screenshots", [])
                for s in screenshots:
                    s_path = s.get("path") if isinstance(s, dict) else str(s)
                    s_name = s.get("filename") if isinstance(s, dict) else Path(s_path).name
                    s_fail = bool(s.get("is_failure", False)) if isinstance(s, dict) else ("fail" in s_name.lower())
                    
                    shot_record = RunScreenshot(
                        run_id=db_run.id,
                        file_path=s_path,
                        filename=s_name,
                        url=db_run.target_url,
                        is_failure=s_fail,
                    )
                    db.add(shot_record)

                # Persist assertions as RunAssertion rows if present
                assertions = result_data.get("assertions", [])
                for a in assertions:
                    if isinstance(a, dict):
                        assert_record = RunAssertion(
                            run_id=db_run.id,
                            assertion_type=a.get("assertion_type", "custom"),
                            target=a.get("target", ""),
                            expected_value=a.get("expected_value"),
                            actual_value=a.get("actual_value"),
                            passed=bool(a.get("passed", False)),
                            detail=a.get("detail"),
                        )
                        db.add(assert_record)

                db.commit()
                logger.info("Successfully persisted completion for job %s to DB", job_id)
        except Exception as exc:
            logger.error("Failed to sync completed run %s to DB: %s", job_id, exc)


# Global default worker instance
default_worker = CanaryExecutionWorker(max_concurrency=1)
