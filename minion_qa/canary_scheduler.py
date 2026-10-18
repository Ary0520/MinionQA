"""
MinionQA Canary Scheduler — Autonomous 24/7 Canary Watchdog & Regression Sentinel.

Continuously schedules and executes autonomous canary QA tests against registered
CanaryTargets and their discovered CanaryMap nodes:
- Automated periodic execution across business-critical nodes (swap, checkout, auth).
- Immediate regression detection: flags when a previously PASSING workflow turns FAILING.
- Dynamic test prompt dispatch through OpenMinion's AutonomousGoalPlanner.
- In-process async background ticker requiring zero Redis/Celery infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from minion_qa.canary_explorer import CanaryExplorer, get_canary_map_for_target
from minion_qa.config import MinionQAConfig, default_config
from minion_qa.database import SessionLocal
from minion_qa.models import (
    CanaryMapNode,
    CanaryRun,
    CanarySchedule,
    CanaryTarget,
    utc_now,
)
from minion_qa.worker import default_worker

logger = logging.getLogger("minion_qa.canary_scheduler")


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class ScheduleConfigPayload(BaseModel):
    interval_minutes: int = Field(default=60, ge=1, le=10080, description="Run interval in minutes")
    enabled: bool = Field(default=True, description="Enable or pause recurring canary watchdog")


class ScheduleDetailResponse(BaseModel):
    id: int
    target_id: int
    target_name: str
    base_url: str
    interval_minutes: int
    enabled: bool
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_verdict: Optional[str] = None
    regression_count: int = 0
    total_runs: int = 0


# ---------------------------------------------------------------------------
# Core Scheduler Logic & Database Functions
# ---------------------------------------------------------------------------

def schedule_target(
    target_id: int,
    interval_minutes: int = 60,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Register or update a recurring canary test schedule for a target."""
    with SessionLocal() as db:
        target = db.query(CanaryTarget).filter(CanaryTarget.id == target_id).first()
        if not target:
            raise ValueError(f"CanaryTarget with ID {target_id} does not exist")

        schedule = db.query(CanarySchedule).filter(CanarySchedule.target_id == target_id).first()
        now = utc_now()
        next_run = now + timedelta(minutes=interval_minutes) if enabled else None

        if schedule:
            schedule.interval_minutes = interval_minutes
            schedule.enabled = enabled
            schedule.next_run_at = next_run
            schedule.updated_at = now
        else:
            schedule = CanarySchedule(
                target_id=target_id,
                interval_minutes=interval_minutes,
                enabled=enabled,
                next_run_at=next_run,
                created_at=now,
                updated_at=now,
            )
            db.add(schedule)

        # Also update target schedule_interval_minutes mirror field
        target.schedule_interval_minutes = interval_minutes
        db.commit()
        db.refresh(schedule)

        return {
            "id": schedule.id,
            "target_id": target.id,
            "target_name": target.name,
            "base_url": target.base_url,
            "interval_minutes": schedule.interval_minutes,
            "enabled": schedule.enabled,
            "last_run_at": schedule.last_run_at.strftime("%Y-%m-%d %H:%M:%S UTC") if schedule.last_run_at else None,
            "next_run_at": schedule.next_run_at.strftime("%Y-%m-%d %H:%M:%S UTC") if schedule.next_run_at else None,
            "last_verdict": schedule.last_verdict,
            "regression_count": schedule.regression_count,
            "total_runs": schedule.total_runs,
        }


def get_target_schedule(target_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve schedule status and telemetry for a target."""
    with SessionLocal() as db:
        sched = db.query(CanarySchedule).filter(CanarySchedule.target_id == target_id).first()
        if not sched or not sched.target:
            return None

        return {
            "id": sched.id,
            "target_id": sched.target_id,
            "target_name": sched.target.name,
            "base_url": sched.target.base_url,
            "interval_minutes": sched.interval_minutes,
            "enabled": sched.enabled,
            "last_run_at": sched.last_run_at.strftime("%Y-%m-%d %H:%M:%S UTC") if sched.last_run_at else None,
            "next_run_at": sched.next_run_at.strftime("%Y-%m-%d %H:%M:%S UTC") if sched.next_run_at else None,
            "last_verdict": sched.last_verdict,
            "regression_count": sched.regression_count,
            "total_runs": sched.total_runs,
        }


def list_schedules() -> List[Dict[str, Any]]:
    """List all configured Canary Watchdog schedules."""
    with SessionLocal() as db:
        schedules = db.query(CanarySchedule).all()
        results = []
        for s in schedules:
            target_name = s.target.name if s.target else f"Target #{s.target_id}"
            base_url = s.target.base_url if s.target else ""
            results.append({
                "id": s.id,
                "target_id": s.target_id,
                "target_name": target_name,
                "base_url": base_url,
                "interval_minutes": s.interval_minutes,
                "enabled": s.enabled,
                "last_run_at": s.last_run_at.strftime("%Y-%m-%d %H:%M:%S UTC") if s.last_run_at else None,
                "next_run_at": s.next_run_at.strftime("%Y-%m-%d %H:%M:%S UTC") if s.next_run_at else None,
                "last_verdict": s.last_verdict,
                "regression_count": s.regression_count,
                "total_runs": s.total_runs,
            })
        return results


def run_canary_for_target(target_id: int) -> Dict[str, Any]:
    """
    Execute a scheduled canary watchdog run against a target:
    1. Selects or discovers target nodes from CanaryMap.
    2. Prioritizes business-critical uncovered nodes (checkout, swap, auth, or landing).
    3. Executes autonomous QA run.
    4. Evaluates regression: previously PASSED -> now FAILED.
    5. Updates schedule state, metrics, and coverage.
    """
    from minion_qa.autonomous_planner import AutonomousGoalPlanner

    with SessionLocal() as db:
        target = db.query(CanaryTarget).filter(CanaryTarget.id == target_id).first()
        if not target:
            raise ValueError(f"Target {target_id} not found")
        target_name = target.name
        base_url = target.base_url

        sched = db.query(CanarySchedule).filter(CanarySchedule.target_id == target_id).first()
        prev_verdict = sched.last_verdict if sched else None

    # Retrieve or generate CanaryMap
    cmap = get_canary_map_for_target(target_id)
    if not cmap or not cmap.nodes:
        logger.info(f"Target {target_id} has no CanaryMap; auto-exploring {base_url} first...")
        explorer = CanaryExplorer(headless=True)
        cmap = explorer.explore(base_url=base_url, target_id=target_id, max_pages=3)

    # Select target node to test
    selected_node = None
    # 1. Uncovered priority nodes first
    priority_categories = {"checkout", "swap", "wallet", "staking", "authentication"}
    for node in cmap.nodes:
        if not node.is_covered and node.business_category in priority_categories:
            selected_node = node
            break
    # 2. Any uncovered node
    if not selected_node:
        for node in cmap.nodes:
            if not node.is_covered:
                selected_node = node
                break
    # 3. Fallback to any priority node or first node
    if not selected_node and cmap.nodes:
        selected_node = cmap.nodes[0]

    test_prompt = (
        selected_node.suggested_prompt
        if selected_node and selected_node.suggested_prompt
        else f"Verify application {base_url} loads cleanly without console or network errors."
    )
    test_node_label = selected_node.label if selected_node else "Landing Page"

    job_id = f"canary_{uuid4().hex[:10]}"
    cfg = MinionQAConfig(headless=True)
    planner = AutonomousGoalPlanner(config=cfg, max_turns=6, session_id=job_id)

    exec_result = planner.execute_goal(goal=test_prompt, start_url=base_url)
    run_passed = (exec_result.status == "PASSED")
    current_verdict = "PASSED" if run_passed else "FAILED"

    # Regression Detection
    is_regression = (prev_verdict == "PASSED" and current_verdict == "FAILED")
    final_verdict = "REGRESSION" if is_regression else current_verdict

    # Update Database Schedule and Node Coverage
    now = utc_now()
    with SessionLocal() as db:
        sched = db.query(CanarySchedule).filter(CanarySchedule.target_id == target_id).first()
        if sched:
            sched.last_run_at = now
            sched.last_verdict = final_verdict
            sched.total_runs += 1
            if is_regression:
                sched.regression_count += 1
            if sched.enabled:
                sched.next_run_at = now + timedelta(minutes=sched.interval_minutes)

        # Mark node as covered
        if selected_node:
            db_node = (
                db.query(CanaryMapNode)
                .filter(CanaryMapNode.target_id == target_id, CanaryMapNode.label == selected_node.label)
                .first()
            )
            if db_node:
                db_node.is_covered = True
                db_node.last_tested_at = now

        db.commit()

    logger.info(
        f"Canary run completed for target {target_id} ({target_name}): "
        f"Verdict: {final_verdict} (Regression: {is_regression})"
    )

    return {
        "job_id": job_id,
        "target_id": target_id,
        "target_name": target_name,
        "base_url": base_url,
        "node_tested": test_node_label,
        "prompt_tested": test_prompt,
        "status": exec_result.status,
        "verdict": final_verdict,
        "is_regression": is_regression,
        "previous_verdict": prev_verdict,
        "duration_seconds": exec_result.duration_s,
        "report_path": exec_result.report_path,
    }


# ---------------------------------------------------------------------------
# Canary Watchdog In-Process Background Loop
# ---------------------------------------------------------------------------

class CanaryWatchdog:
    """
    Background asynchronous ticker for scheduled canary runs.
    Periodically queries active schedules and enqueues due watchdog jobs into the worker.
    """

    def __init__(self, check_interval_seconds: int = 30) -> None:
        self.check_interval_seconds = check_interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        """Start recurring watchdog ticker loop."""
        if self._is_running:
            return
        self._is_running = True
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._ticker_loop())
        logger.info("CanaryWatchdog ticker started (checking every %ds)", self.check_interval_seconds)

    async def stop(self) -> None:
        """Stop watchdog ticker."""
        self._is_running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("CanaryWatchdog ticker stopped")

    async def _ticker_loop(self) -> None:
        """Poll database periodically for due canary schedules."""
        while self._is_running:
            try:
                await self.check_and_dispatch_due_schedules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in CanaryWatchdog ticker: {e}", exc_info=True)

            await asyncio.sleep(self.check_interval_seconds)

    async def check_and_dispatch_due_schedules(self) -> List[str]:
        """Find enabled schedules whose next_run_at has arrived and enqueue them."""
        due_target_ids = []
        now = utc_now()

        with SessionLocal() as db:
            due_schedules = (
                db.query(CanarySchedule)
                .filter(
                    CanarySchedule.enabled == True,
                    CanarySchedule.next_run_at <= now,
                )
                .all()
            )
            for s in due_schedules:
                due_target_ids.append(s.target_id)
                # Advance next_run_at immediately to avoid duplicate dispatch
                s.next_run_at = now + timedelta(minutes=s.interval_minutes)
            db.commit()

        dispatched_jobs = []
        for tid in due_target_ids:
            job_id = f"watchdog_sched_{tid}_{int(now.timestamp())}"
            default_worker.enqueue_job(
                job_id=job_id,
                name=f"24/7 Canary Watchdog Run (Target #{tid})",
                func=run_canary_for_target,
                target_id=tid,
            )
            dispatched_jobs.append(job_id)
            logger.info(f"Dispatched automated canary watchdog job {job_id} for target {tid}")

        return dispatched_jobs


default_scheduler = CanaryWatchdog(check_interval_seconds=30)
