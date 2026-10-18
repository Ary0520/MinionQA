"""
Unit tests for MinionQA Cortensor Client and Network Sentinel (minion_qa.sentinel, minion_qa.cortensor_client).

Tests cryptographic SHA-256 proof generation, miner attestation recording,
decentralized compute verification, and attestation API retrieval.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from minion_qa.database import SessionLocal, init_db
from minion_qa.models import CanaryRun, CanaryRunStatus, NetworkSentinel
from minion_qa.cortensor_client import CortensorClient, generate_content_hash
from minion_qa.sentinel import (
    compute_proof_hash,
    get_attestation,
    record_attestation,
    verify_run_attestation,
)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_hash_generation():
    """Verify deterministic SHA-256 hashing."""
    data = {"task": "Verify stake balance", "verdict": "PASSED"}
    hash1 = generate_content_hash(data)
    hash2 = compute_proof_hash(data)
    assert hash1 == hash2
    assert len(hash1) == 64
    assert isinstance(hash1, str)


def test_record_and_get_attestation():
    """Verify persisting and querying NetworkSentinel for a run."""
    job_id = f"sentinel_run_{int(time.time() * 1000)}"

    with SessionLocal() as db:
        run = CanaryRun(
            job_id=job_id,
            name="Sentinel Attestation Test",
            goal="Test network proof",
            target_url="https://dreamvault.netlify.app",
            status=CanaryRunStatus.COMPLETED,
            verdict="PASSED",
        )
        db.add(run)
        db.commit()

    # Record attestation
    att = record_attestation(
        run_identifier=job_id,
        node_type="mainnet",
        session_id="session_cort_alpha_1",
        miner_id="cort_miner_0x7b12f",
        model_id="deepseek-r1-distill",
        request_latency_ms=185,
        raw_response_hash="a" * 64,
        validation_status="verified",
    )
    assert att is not None
    assert att["job_id"] == job_id
    assert att["miner_id"] == "cort_miner_0x7b12f"
    assert att["node_type"] == "mainnet"

    # Fetch attestation by job_id
    fetched = get_attestation(job_id)
    assert fetched is not None
    assert fetched["miner_id"] == "cort_miner_0x7b12f"
    assert fetched["raw_response_hash"] == "a" * 64

    # Verify attestation
    verification = verify_run_attestation(job_id)
    assert verification["verified"] is True
    assert verification["miner_id"] == "cort_miner_0x7b12f"
    assert verification["proof_of_execution"] == "Cortensor Decoupled Compute Verifier"


def test_cortensor_client_completion_mock():
    """Test CortensorClient completion parsing with mock."""
    async def _run():
        client = CortensorClient(base_url="https://api.cortensor.app/v1", api_key="test_key")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"thought": "Click stake", "action": "click"}'}}],
            "id": "cort_task_445",
        }
        mock_resp.headers = {"x-miner-id": "miner_cort_0xABC"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = await client.complete(
                messages=[{"role": "user", "content": "What is next step?"}],
                model="oss-20b",
            )

            assert res["status"] == "success"
            assert res["miner_id"] == "miner_cort_0xABC"
            assert res["node_type"] == "mainnet"
            assert len(res["raw_response_hash"]) == 64
            assert "Click stake" in res["content"]

    asyncio.run(_run())


def test_cortensor_client_validation_and_factcheck():
    """Test validation and fact-check calls."""
    async def _run():
        client = CortensorClient()

        mock_resp_val = MagicMock()
        mock_resp_val.status_code = 200
        mock_resp_val.json.return_value = {"status": "verified", "consensus": 0.98}

        mock_resp_fc = MagicMock()
        mock_resp_fc.status_code = 200
        mock_resp_fc.json.return_value = {"claim": "total equals $29.99", "consensus": "true"}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [mock_resp_val, mock_resp_fc]

            val = await client.validate(task="checkout", result="PASSED")
            assert val["validated"] is True
            assert val["validation_status"] == "verified"
            assert "raw_hash" in val

            fc = await client.factcheck(claim="total equals $29.99", context="subtotal $29.99")
            assert fc["factchecked"] is True

    asyncio.run(_run())
