"""
MinionQA ORM Models.

Persistent data models for the Decentralized Canary QA Sentinel:
- CanaryTarget: Registered dApps and web applications under monitoring.
- CanaryRun: Immutable execution records with live steps and audit traces.
- RunScreenshot: Captured visual evidence and failure frames.
- RunAssertion: Evaluated test oracle assertions per run.
- NetworkSentinel: Verifiable decentralized inference metadata (Cortensor node attestation).
- LearnedLocator: Self-healing selector memory and stability hierarchies.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from minion_qa.database import Base


def utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class CanaryRunStatus(str, enum.Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"  # Sent to Cortensor decentralized node
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CanaryTarget(Base):
    """A registered Web3 dApp or web application being monitored by MinionQA."""

    __tablename__ = "canary_targets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(2048), nullable=False)
    description = Column(Text, nullable=True)
    login_hint = Column(Text, nullable=True)
    schedule_interval_minutes = Column(Integer, nullable=True)  # e.g. 60 min for canary watchdog
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    runs = relationship("CanaryRun", back_populates="target", cascade="all, delete-orphan")
    learned_locators = relationship("LearnedLocator", back_populates="target", cascade="all, delete-orphan")


class CanaryRun(Base):
    """
    A single execution run against a target or ad-hoc URL.
    Captures end-to-end execution lifecycle, live streaming steps,
    assertions, and Cortensor decentralized node telemetry.
    """

    __tablename__ = "canary_runs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String(64), unique=True, index=True, nullable=False)
    target_id = Column(Integer, ForeignKey("canary_targets.id"), nullable=True, index=True)

    # Goal & URL
    name = Column(String(255), nullable=False)
    goal = Column(Text, nullable=False)
    target_url = Column(String(2048), nullable=False)
    max_turns = Column(Integer, default=15, nullable=False)

    # Status & Life Cycle
    status = Column(SAEnum(CanaryRunStatus), default=CanaryRunStatus.PENDING, index=True, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # In-Flight & Final Telemetry
    live_steps = Column(Text, nullable=True)       # Incremental JSON array of step traces
    steps_log = Column(Text, nullable=True)        # Final complete JSON array of steps
    dom_snapshot = Column(Text, nullable=True)     # Compact DOM snapshot from final state
    visited_urls = Column(Text, nullable=True)     # JSON array of visited URLs

    # Test Results
    is_successful = Column(Boolean, nullable=True)
    verdict = Column(String(16), nullable=True)    # PASSED | FAILED | ANOMALY
    result_summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    rca_category = Column(String(64), nullable=True)  # PRODUCT_BUG | TIMEOUT | FLAKY_SELECTOR | ANOMALY
    anomaly_detected = Column(Boolean, default=False, nullable=False)
    total_turns = Column(Integer, default=0, nullable=False)

    # Assertion Metrics
    assertions_count = Column(Integer, default=0, nullable=False)
    assertions_passed = Column(Integer, default=0, nullable=False)
    assertion_results = Column(Text, nullable=True)  # JSON array of assertion results

    # Reporting & Artifacts
    report_path = Column(String(2048), nullable=True)
    report_filename = Column(String(255), nullable=True)

    # Inference Provider Telemetry
    llm_provider = Column(String(32), nullable=True)
    llm_model = Column(String(128), nullable=True)
    cortensor_session_id = Column(String(64), nullable=True)
    cortensor_task_id = Column(String(128), nullable=True)

    # Relationships
    target = relationship("CanaryTarget", back_populates="runs")
    screenshots = relationship("RunScreenshot", back_populates="run", cascade="all, delete-orphan")
    assertions = relationship("RunAssertion", back_populates="run", cascade="all, delete-orphan")
    network_sentinel = relationship("NetworkSentinel", back_populates="run", uselist=False, cascade="all, delete-orphan")


class RunScreenshot(Base):
    """Visual proof artifact captured during a test run."""

    __tablename__ = "run_screenshots"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("canary_runs.id"), nullable=False, index=True)
    file_path = Column(String(2048), nullable=False)
    filename = Column(String(255), nullable=False)
    url = Column(String(2048), nullable=True)
    caption = Column(String(500), nullable=True)
    turn_index = Column(Integer, nullable=True)
    is_failure = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    run = relationship("CanaryRun", back_populates="screenshots")


class RunAssertion(Base):
    """Deterministic Test Oracle assertion evaluated during a run."""

    __tablename__ = "run_assertions"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("canary_runs.id"), nullable=False, index=True)
    assertion_type = Column(String(64), nullable=False)
    target = Column(Text, nullable=False)
    expected_value = Column(Text, nullable=True)
    actual_value = Column(Text, nullable=True)
    passed = Column(Boolean, nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    run = relationship("CanaryRun", back_populates="assertions")


class NetworkSentinel(Base):
    """
    Decentralized verification record: logs which Cortensor network miner/node served
    this run and cryptographic response hashes to prove decentralized compute was used.
    """

    __tablename__ = "network_sentinels"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("canary_runs.id"), unique=True, nullable=False, index=True)
    node_type = Column(String(32), default="trial", nullable=False)  # trial | mainnet | router
    session_id = Column(String(64), nullable=True)
    miner_id = Column(String(128), nullable=True)
    model_id = Column(String(128), nullable=True)
    request_latency_ms = Column(Integer, nullable=True)
    raw_response_hash = Column(String(64), nullable=True)  # SHA-256 hash of network response
    validation_status = Column(String(32), default="unverified", nullable=False)  # verified | unverified | flagged
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    run = relationship("CanaryRun", back_populates="network_sentinel")


class LearnedLocator(Base):
    """
    Durable Self-Healing Locator Memory (inspired by Leaka AI's memory.py).
    Stores selectors and stability hierarchies that demonstrably worked for an application.
    """

    __tablename__ = "learned_locators"

    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, ForeignKey("canary_targets.id"), nullable=False, index=True)
    selector = Column(Text, nullable=False)
    hierarchy = Column(Text, nullable=True)       # JSON string of ranked fallback strategies
    element_text = Column(String(500), nullable=True)
    tag = Column(String(64), nullable=True)
    page_url_pattern = Column(String(2048), nullable=True)
    times_seen = Column(Integer, default=1, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("target_id", "selector", name="uq_target_locator"),
    )

    target = relationship("CanaryTarget", back_populates="learned_locators")
