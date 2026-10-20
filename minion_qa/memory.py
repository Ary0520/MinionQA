"""
MinionQA Durable Self-Healing Locator Memory Service.

Inspired by Leaka AI (backend/app/memory.py) and adapted for Cortensor Canary QA.
Provides:
- Durable persistence of self-healed selectors and fallback hierarchies into learned_locators table
- Never-raising write guarantee (durability over failure)
- Instant memory lookup before LLM turns, preventing redundant failures
- Target domain auto-association for Web3 dApps and e-commerce targets
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from minion_qa.database import SessionLocal
from minion_qa.models import CanaryTarget, LearnedLocator, utc_now

logger = logging.getLogger("minion_qa.memory")


def _extract_domain(url: str) -> str:
    """Extract clean domain/hostname from a URL."""
    try:
        parsed = urlparse(url)
        return parsed.netloc or parsed.path.split("/")[0]
    except Exception:
        return url


def find_or_create_target_for_url(db: Session, url: str) -> CanaryTarget:
    """
    Find existing CanaryTarget matching the URL domain, or auto-register one.
    Guarantees a valid target_id for durable learned locator storage.
    """
    domain = _extract_domain(url)
    
    # 1. Search for matching base_url containing domain
    target = (
        db.query(CanaryTarget)
        .filter(CanaryTarget.base_url.like(f"%{domain}%"))
        .first()
    )
    if target:
        return target

    # 2. Auto-create CanaryTarget for unmonitored target
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    base_url = f"{scheme}://{domain}" if domain else url

    target = CanaryTarget(
        name=f"Auto: {domain or 'Web Target'}",
        base_url=base_url,
        description="Auto-discovered canary target from autonomous QA run",
        is_active=True,
        created_at=utc_now(),
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    logger.info("Auto-registered new CanaryTarget for domain %s (id=%d)", domain, target.id)
    return target


def remember_locator(
    original_selector: str,
    healed_selector: str,
    target_url: str,
    element_text: Optional[str] = None,
    tag: Optional[str] = None,
    target_id: Optional[int] = None,
    strategies: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Persist a verified healed selector into durable memory.
    Never raises an exception — guarantees calling run always proceeds.
    """
    if not healed_selector:
        return {"status": "skipped", "reason": "empty healed_selector"}

    try:
        with SessionLocal() as db:
            # Resolve target_id if not explicitly provided
            if target_id is None:
                canary_target = find_or_create_target_for_url(db, target_url)
                tid = canary_target.id
            else:
                tid = target_id

            hierarchy_data = {
                "original_selector": original_selector,
                "healed_selector": healed_selector,
                "strategies": strategies or [
                    {"type": "healed", "selector": healed_selector},
                    {"type": "original", "selector": original_selector},
                ],
                "recorded_at": utc_now().isoformat(),
            }
            hierarchy_json = json.dumps(hierarchy_data)

            # Check if this healed selector already exists for this target
            existing = (
                db.query(LearnedLocator)
                .filter(
                    LearnedLocator.target_id == tid,
                    LearnedLocator.selector == healed_selector,
                )
                .first()
            )

            if existing:
                existing.times_seen += 1
                existing.last_seen_at = utc_now()
                existing.hierarchy = hierarchy_json
                if element_text:
                    existing.element_text = element_text[:500]
                if tag:
                    existing.tag = tag[:64]
                db.commit()
                return {"status": "updated", "id": existing.id, "times_seen": existing.times_seen}

            # Create new LearnedLocator entry
            locator = LearnedLocator(
                target_id=tid,
                selector=healed_selector,
                hierarchy=hierarchy_json,
                element_text=element_text[:500] if element_text else None,
                tag=tag[:64] if tag else None,
                page_url_pattern=target_url[:2048] if target_url else None,
                times_seen=1,
                last_seen_at=utc_now(),
            )
            db.add(locator)
            db.commit()
            db.refresh(locator)
            logger.info(
                "Persisted learned locator '%s' (healed from '%s') for target %d",
                healed_selector,
                original_selector,
                tid,
            )
            return {"status": "created", "id": locator.id}

    except Exception as exc:
        logger.warning("Could not persist learned locator: %s", exc)
        return {"status": "failed", "error": str(exc)}


def lookup_locator(
    selector: str,
    target_url: str,
    target_id: Optional[int] = None,
) -> Optional[str]:
    """
    Check durable memory for known healed selector matching the requested selector.
    Returns healed selector if found, else None.
    """
    if not selector:
        return None

    try:
        with SessionLocal() as db:
            if target_id is None:
                domain = _extract_domain(target_url)
                target = (
                    db.query(CanaryTarget)
                    .filter(CanaryTarget.base_url.like(f"%{domain}%"))
                    .first()
                )
                if not target:
                    return None
                tid = target.id
            else:
                tid = target_id

            # 1. Exact match on known selector
            exact = (
                db.query(LearnedLocator)
                .filter(
                    LearnedLocator.target_id == tid,
                    LearnedLocator.selector == selector,
                )
                .first()
            )
            if exact:
                return exact.selector

            # 2. Check if selector was stored as original_selector in hierarchy
            candidates = (
                db.query(LearnedLocator)
                .filter(LearnedLocator.target_id == tid)
                .order_by(LearnedLocator.times_seen.desc())
                .all()
            )
            for cand in candidates:
                if cand.hierarchy:
                    try:
                        hdata = json.loads(cand.hierarchy)
                        if hdata.get("original_selector") == selector:
                            return cand.selector
                    except Exception:
                        continue

            return None
    except Exception as exc:
        logger.debug("Memory lookup error: %s", exc)
        return None


def list_learned_locators(target_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieve list of learned locators with hierarchy and confidence metrics."""
    try:
        with SessionLocal() as db:
            query = db.query(LearnedLocator)
            if target_id is not None:
                query = query.filter(LearnedLocator.target_id == target_id)
            items = query.order_by(LearnedLocator.times_seen.desc()).all()

            results = []
            for it in items:
                hierarchy_parsed = {}
                if it.hierarchy:
                    try:
                        hierarchy_parsed = json.loads(it.hierarchy)
                    except Exception:
                        pass

                results.append({
                    "id": it.id,
                    "target_id": it.target_id,
                    "selector": it.selector,
                    "original_selector": hierarchy_parsed.get("original_selector"),
                    "element_text": it.element_text,
                    "tag": it.tag,
                    "page_url_pattern": it.page_url_pattern,
                    "times_seen": it.times_seen,
                    "last_seen_at": it.last_seen_at.strftime("%Y-%m-%d %H:%M:%S UTC") if it.last_seen_at else "",
                })
            return results
    except Exception as exc:
        logger.error("Failed to list learned locators: %s", exc)
        return []


def build_memory_hint(target_id: Optional[int] = None) -> str:
    """
    Construct a markdown prompt hint detailing previously learned, durable DOM selectors
    for this target dApp, so OpenMinion / LLM planners can select the correct selectors immediately.
    """
    locators = list_learned_locators(target_id=target_id)
    if not locators:
        return ""

    lines = ["\n[Durable Self-Healing Locator Memory]:"]
    for loc in locators[:10]:
        orig = loc.get("original_selector") or "unknown"
        healed = loc.get("selector")
        txt = loc.get("element_text") or ""
        seen = loc.get("times_seen", 1)
        lines.append(f"- Learned: '{orig}' -> '{healed}' (text: '{txt}', confirmed {seen}x)")
    return "\n".join(lines)

