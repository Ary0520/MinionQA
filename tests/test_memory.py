"""
Unit tests for MinionQA Durable Self-Healing Locator Memory (minion_qa.memory).

Tests locator persistence, domain auto-target resolution, duplicate handling,
hierarchy mapping, and instant memory lookup.
"""

import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from minion_qa.database import Base, SessionLocal, init_db
from minion_qa.models import CanaryTarget, LearnedLocator
from minion_qa.memory import (
    _extract_domain,
    find_or_create_target_for_url,
    list_learned_locators,
    lookup_locator,
    remember_locator,
)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_extract_domain():
    """Verify clean domain extraction from various URL formats."""
    assert _extract_domain("https://dreamvault.netlify.app/dashboard") == "dreamvault.netlify.app"
    assert _extract_domain("http://localhost:8080/api/status") == "localhost:8080"
    assert _extract_domain("https://www.saucedemo.com") == "www.saucedemo.com"
    assert _extract_domain("example.com/checkout") == "example.com"


def test_find_or_create_target_for_url():
    """Verify target auto-registration from URL domain."""
    with SessionLocal() as db:
        unique_url = f"https://dapp-{int(time.time() * 1000)}.cortensor.network/vault"
        target1 = find_or_create_target_for_url(db, unique_url)
        assert target1.id is not None
        assert "cortensor.network" in target1.base_url

        # Calling again for same domain should return existing target
        target2 = find_or_create_target_for_url(db, unique_url + "/stake")
        assert target2.id == target1.id


def test_remember_and_lookup_locator():
    """Verify storing a healed locator and looking it up by original and healed selector."""
    uid = int(time.time() * 1000)
    test_url = f"https://unique-{uid}.netlify.app/stake"
    broken_sel = f"#broken-stake-{uid}"
    healed_sel = f'[data-testid="stake-submit-{uid}"]'

    # 1. Initially lookup should return None
    assert lookup_locator(broken_sel, test_url) is None

    # 2. Persist healed locator
    res = remember_locator(
        original_selector=broken_sel,
        healed_selector=healed_sel,
        target_url=test_url,
        element_text="Deposit STAKE",
        tag="button",
    )
    assert res["status"] in ("created", "updated")

    # 3. Lookup using original broken selector should resolve to healed selector!
    healed = lookup_locator(broken_sel, test_url)
    assert healed == healed_sel

    # 4. Lookup using healed selector directly should match
    exact = lookup_locator(healed_sel, test_url)
    assert exact == healed_sel

    # 5. Storing again should increment times_seen
    res2 = remember_locator(
        original_selector=broken_sel,
        healed_selector=healed_sel,
        target_url=test_url,
    )
    assert res2["status"] == "updated"
    assert res2["times_seen"] >= 2


def test_list_learned_locators():
    """Verify listing locators and hierarchy metadata."""
    uid = int(time.time() * 1000)
    test_url = f"https://wallet-{uid}.netlify.app/wallet"
    broken_sel = f"#connect-wallet-{uid}"
    healed_sel = f'button:has-text("Connect Wallet {uid}")'

    remember_locator(
        original_selector=broken_sel,
        healed_selector=healed_sel,
        target_url=test_url,
        element_text=f"Connect Wallet {uid}",
        tag="button",
    )

    locators = list_learned_locators()
    assert len(locators) >= 1
    found = next((l for l in locators if l["selector"] == healed_sel), None)
    assert found is not None
    assert found["original_selector"] == broken_sel
    assert found["times_seen"] >= 1
