"""
Unit and integration tests for MinionQA Canary Explorer & Application Intelligence.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from minion_qa.canary_explorer import (
    CanaryExplorer,
    CanaryMap,
    DiscoveredNode,
    classify_business_category,
    generate_suggested_prompt,
    get_canary_map_for_target,
    save_canary_map_to_db,
)
from minion_qa.database import SessionLocal, init_db
from minion_qa.models import CanaryMapNode, CanaryTarget, utc_now
from minion_qa.server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def ensure_db():
    init_db()


def test_classify_business_category():
    """Verify business category classifier on Web3 and commerce terms."""
    assert classify_business_category("Connect MetaMask Wallet", "https://app.uniswap.org") == "wallet"
    assert classify_business_category("ETH / USDT Token Swap", "/swap") == "swap"
    assert classify_business_category("Validator Staking Pool APY", "/stake") == "staking"
    assert classify_business_category("Proceed to Order Checkout", "/checkout") == "checkout"
    assert classify_business_category("User Sign In / Register", "/login") == "authentication"
    assert classify_business_category("Profile Settings", "/account/settings") == "account"
    assert classify_business_category("API Documentation & Whitepaper", "/docs") == "content"
    assert classify_business_category("Main App Overview", "/dashboard") == "navigation"
    assert classify_business_category("Random Widget Component", "/random") == "other"


def test_generate_suggested_prompt():
    """Verify natural-language test prompt synthesis for different node types."""
    # Form node
    form_node = DiscoveredNode(
        node_type="form",
        label="Customer Login Form",
        url="https://example.com/login",
        business_category="authentication",
    )
    prompt = generate_suggested_prompt(form_node, "https://example.com")
    assert "authentication form 'Customer Login Form'" in prompt
    assert "valid and invalid credentials" in prompt

    # Swap node
    swap_node = DiscoveredNode(
        node_type="page",
        label="Token Swap DEX",
        url="https://dex.example.com",
        business_category="swap",
    )
    swap_prompt = generate_suggested_prompt(swap_node, "https://dex.example.com")
    assert "slippage tolerance" in swap_prompt
    assert "Token Swap DEX" in swap_prompt

    # Wallet node
    wallet_node = DiscoveredNode(
        node_type="action",
        label="Connect Wallet Button",
        business_category="wallet",
    )
    wallet_prompt = generate_suggested_prompt(wallet_node, "https://dapp.example.com")
    assert "Web3 wallet connection modal" in wallet_prompt


def test_canary_explorer_url_helpers():
    """Test URL normalization and same-origin detection."""
    explorer = CanaryExplorer(headless=True)
    assert explorer._normalize_url("example.com") == "https://example.com"
    assert explorer._normalize_url("http://localhost:3000") == "http://localhost:3000"
    assert explorer._normalize_url("https://dex.io") == "https://dex.io"

    assert explorer._is_same_origin("https://app.uniswap.org", "https://app.uniswap.org/swap") is True
    assert explorer._is_same_origin("https://app.uniswap.org", "/pools") is True
    assert explorer._is_same_origin("https://app.uniswap.org", "https://google.com") is False


def test_canary_map_db_persistence_and_retrieval():
    """Test saving CanaryMap to database and retrieving back as structured model."""
    with SessionLocal() as db:
        target = CanaryTarget(
            name="Cortensor Swap dApp",
            base_url="https://swap.cortensor.network",
            description="Automated decentralized exchange monitoring",
            created_at=utc_now(),
        )
        db.add(target)
        db.commit()
        db.refresh(target)
        target_id = target.id

    nodes = [
        DiscoveredNode(
            node_type="page",
            label="Swap Home",
            url="https://swap.cortensor.network",
            business_category="swap",
            connects_to=["Liquidity Pool", "Wallet Modal"],
            suggested_prompt="Verify swap interface loads",
        ),
        DiscoveredNode(
            node_type="action",
            label="Wallet Modal",
            business_category="wallet",
            depends_on=["Swap Home"],
            suggested_prompt="Click Connect Wallet and verify modal",
        ),
        DiscoveredNode(
            node_type="form",
            label="Liquidity Deposit Form",
            url="https://swap.cortensor.network/pool",
            business_category="staking",
            depends_on=["Swap Home"],
            suggested_prompt="Test pool liquidity deposit inputs",
        ),
    ]

    cmap = CanaryMap(
        target_id=target_id,
        base_url="https://swap.cortensor.network",
        nodes=nodes,
        summary="Test dApp map",
        total_pages=1,
        total_forms=1,
        total_flows=1,
    )

    saved_records = save_canary_map_to_db(cmap, target_id)
    assert len(saved_records) == 3

    # Retrieve via helper
    retrieved = get_canary_map_for_target(target_id)
    assert retrieved is not None
    assert retrieved.target_id == target_id
    assert len(retrieved.nodes) == 3
    assert retrieved.total_pages == 1
    assert retrieved.total_forms == 1
    assert retrieved.total_flows == 1
    assert any(n.label == "Swap Home" and n.business_category == "swap" for n in retrieved.nodes)
    assert any(n.label == "Wallet Modal" and n.business_category == "wallet" for n in retrieved.nodes)


def test_server_target_explore_and_map_endpoints():
    """Test /api/targets/{id}/explore and /api/targets/{id}/map REST endpoints."""
    # 1. Create target
    resp = client.post("/api/targets", json={
        "name": "Sentinel Monitored App",
        "base_url": "https://example.com",
        "description": "Integration endpoint test target",
    })
    assert resp.status_code == 200
    target_id = resp.json()["id"]

    # 2. Mock CanaryExplorer.explore to avoid external network dependencies during unit test
    mock_nodes = [
        DiscoveredNode(
            node_type="page",
            label="Example Domain Home",
            url="https://example.com",
            business_category="content",
            suggested_prompt="Navigate to Example Domain Home and verify 200 OK",
        )
    ]
    mock_cmap = CanaryMap(
        target_id=target_id,
        base_url="https://example.com",
        nodes=mock_nodes,
        summary="Mocked Canary Map",
        total_pages=1,
    )

    with patch.object(CanaryExplorer, "explore", return_value=mock_cmap):
        explore_resp = client.post(f"/api/targets/{target_id}/explore?max_pages=2")
        assert explore_resp.status_code == 200
        data = explore_resp.json()
        assert data["target_id"] == target_id
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["label"] == "Example Domain Home"

    # Save to DB so GET /map finds it
    save_canary_map_to_db(mock_cmap, target_id)

    # 3. GET /api/targets/{id}/map
    map_resp = client.get(f"/api/targets/{target_id}/map")
    assert map_resp.status_code == 200
    map_data = map_resp.json()
    assert map_data["target_id"] == target_id
    assert map_data["total_pages"] == 1
    assert len(map_data["nodes"]) == 1
