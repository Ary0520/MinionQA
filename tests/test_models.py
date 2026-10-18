"""
Unit tests for MinionQA ORM Models (minion_qa.models).

Tests table creation, relationship mapping, cascading deletes,
and unique constraints across CanaryTarget, CanaryRun, RunScreenshot,
RunAssertion, NetworkSentinel, and LearnedLocator.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from minion_qa.database import Base
from minion_qa.models import (
    CanaryRun,
    CanaryRunStatus,
    CanaryTarget,
    LearnedLocator,
    NetworkSentinel,
    RunAssertion,
    RunScreenshot,
)


@pytest.fixture
def db_session():
    """In-memory SQLite session for isolated model testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_canary_target_creation(db_session):
    """Test creating a CanaryTarget and retrieving it."""
    target = CanaryTarget(
        name="DreamVault dApp",
        base_url="https://dreamvault.netlify.app",
        description="Web3 staking & vault portal",
        login_hint="MetaMask / Email OTP",
        schedule_interval_minutes=30,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    assert target.id is not None
    assert target.name == "DreamVault dApp"
    assert target.base_url == "https://dreamvault.netlify.app"
    assert target.schedule_interval_minutes == 30
    assert target.is_active is True
    assert target.created_at is not None


def test_canary_run_with_relationships(db_session):
    """Test creating a CanaryRun linked to a CanaryTarget with all child entities."""
    target = CanaryTarget(
        name="Test Target",
        base_url="https://example.com",
    )
    db_session.add(target)
    db_session.commit()

    run = CanaryRun(
        job_id="run_abc1234567",
        target_id=target.id,
        name="Canary Staking Flow Check",
        goal="Log in and verify stake balance exceeds 0",
        target_url="https://example.com/stake",
        max_turns=10,
        status=CanaryRunStatus.RUNNING,
        llm_provider="cortensor",
        llm_model="cortensor-router-v1",
        cortensor_session_id="session_cort_999",
    )
    db_session.add(run)
    db_session.commit()

    assert run.id is not None
    assert run.status == CanaryRunStatus.RUNNING
    assert run.target.id == target.id

    # Add RunScreenshot
    screenshot = RunScreenshot(
        run_id=run.id,
        file_path="screenshots/shot_001.png",
        filename="shot_001.png",
        url="https://example.com/stake",
        caption="Post-login dashboard",
        turn_index=2,
        is_failure=False,
    )
    db_session.add(screenshot)

    # Add RunAssertion
    assertion = RunAssertion(
        run_id=run.id,
        assertion_type="element_visible",
        target="#stake-balance",
        expected_value="visible",
        actual_value="visible",
        passed=True,
        detail="Stake balance rendered within 200ms",
    )
    db_session.add(assertion)

    # Add NetworkSentinel
    sentinel = NetworkSentinel(
        run_id=run.id,
        node_type="mainnet",
        session_id="session_cort_999",
        miner_id="cort_miner_0x71aF",
        model_id="deepseek-r1-distill",
        request_latency_ms=145,
        raw_response_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        validation_status="verified",
    )
    db_session.add(sentinel)
    db_session.commit()

    # Query back and verify back_populates
    db_session.refresh(run)
    assert len(run.screenshots) == 1
    assert run.screenshots[0].filename == "shot_001.png"

    assert len(run.assertions) == 1
    assert run.assertions[0].passed is True

    assert run.network_sentinel is not None
    assert run.network_sentinel.miner_id == "cort_miner_0x71aF"
    assert run.network_sentinel.validation_status == "verified"


def test_cascade_delete_target(db_session):
    """Deleting a CanaryTarget cascades delete to its runs and learned locators."""
    target = CanaryTarget(
        name="Ephemeral Target",
        base_url="https://ephemeral.io",
    )
    db_session.add(target)
    db_session.commit()

    run = CanaryRun(
        job_id="run_ephemeral_1",
        target_id=target.id,
        name="Ephemeral Run",
        goal="Test ephemeral",
        target_url="https://ephemeral.io",
    )
    locator = LearnedLocator(
        target_id=target.id,
        selector="#login-btn",
        hierarchy='[{"strategy": "id", "selector": "#login-btn"}]',
        element_text="Connect Wallet",
        tag="button",
    )
    db_session.add(run)
    db_session.add(locator)
    db_session.commit()

    assert db_session.query(CanaryRun).filter_by(id=run.id).count() == 1
    assert db_session.query(LearnedLocator).filter_by(id=locator.id).count() == 1

    # Delete target
    db_session.delete(target)
    db_session.commit()

    # Verify cascaded deletion
    assert db_session.query(CanaryRun).filter_by(id=run.id).count() == 0
    assert db_session.query(LearnedLocator).filter_by(id=locator.id).count() == 0


def test_cascade_delete_run(db_session):
    """Deleting a CanaryRun cascades delete to screenshots, assertions, and sentinel."""
    run = CanaryRun(
        job_id="run_cascade_2",
        name="Cascade Run",
        goal="Check cascades",
        target_url="https://example.com",
    )
    db_session.add(run)
    db_session.commit()

    screenshot = RunScreenshot(
        run_id=run.id,
        file_path="screenshots/cascade.png",
        filename="cascade.png",
    )
    assertion = RunAssertion(
        run_id=run.id,
        assertion_type="url_equals",
        target="url",
        passed=False,
    )
    sentinel = NetworkSentinel(
        run_id=run.id,
        node_type="trial",
        miner_id="miner_trial_1",
    )
    db_session.add_all([screenshot, assertion, sentinel])
    db_session.commit()

    # Verify exists
    assert db_session.query(RunScreenshot).filter_by(run_id=run.id).count() == 1
    assert db_session.query(RunAssertion).filter_by(run_id=run.id).count() == 1
    assert db_session.query(NetworkSentinel).filter_by(run_id=run.id).count() == 1

    # Delete run
    db_session.delete(run)
    db_session.commit()

    # Verify children deleted
    assert db_session.query(RunScreenshot).filter_by(run_id=run.id).count() == 0
    assert db_session.query(RunAssertion).filter_by(run_id=run.id).count() == 0
    assert db_session.query(NetworkSentinel).filter_by(run_id=run.id).count() == 0


def test_learned_locator_unique_constraint(db_session):
    """LearnedLocator enforces unique constraint on (target_id, selector)."""
    target = CanaryTarget(
        name="Locator Target",
        base_url="https://locator.io",
    )
    db_session.add(target)
    db_session.commit()

    loc1 = LearnedLocator(
        target_id=target.id,
        selector="#submit-button",
        times_seen=1,
    )
    db_session.add(loc1)
    db_session.commit()

    loc2 = LearnedLocator(
        target_id=target.id,
        selector="#submit-button",
        times_seen=2,
    )
    db_session.add(loc2)

    with pytest.raises(Exception):
        db_session.commit()
    db_session.rollback()
