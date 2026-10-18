"""
MinionQA Canary Explorer — Autonomous Application Intelligence & Map Discovery.

Autonomously crawls a registered CanaryTarget dApp/web application using Playwright
and heuristic/LLM classification to construct a structured CanaryMap:
- Pages, interactive forms, button flows, and Web3 action nodes.
- Business category tagging (auth, checkout, swap, wallet, staking, navigation, etc.).
- Natural language canary test prompt generation for 24/7 watchdog execution.
- Persistence to `canary_map_nodes` for test coverage metrics and regression tracking.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from minion_qa.database import SessionLocal
from minion_qa.models import CanaryMapNode, CanaryTarget, utc_now
from minion_qa.tools.browser import BrowserSession, get_browser_session

logger = logging.getLogger("minion_qa.canary_explorer")


# ---------------------------------------------------------------------------
# Structured Pydantic Schemas
# ---------------------------------------------------------------------------

class DiscoveredNode(BaseModel):
    """One discovered entity (page, form, flow, or action) in a dApp's structure."""

    node_type: str = Field(default="page", description="One of: page, form, flow, action")
    label: str = Field(description="Human-readable label (e.g. 'Swap Interface', 'Checkout Form')")
    url: Optional[str] = Field(default=None, description="URL where this node lives")
    description: Optional[str] = Field(default=None, description="Functionality overview")
    business_category: Optional[str] = Field(
        default="other",
        description="One of: authentication, billing, checkout, swap, wallet, staking, account, navigation, content, other",
    )
    connects_to: List[str] = Field(default_factory=list, description="Labels of connected/downstream nodes")
    depends_on: List[str] = Field(default_factory=list, description="Labels of precondition nodes (e.g. 'Login')")
    flow_steps: List[str] = Field(default_factory=list, description="Ordered step labels for flow nodes")
    suggested_prompt: Optional[str] = Field(
        default=None,
        description="Auto-generated natural-language QA test prompt for canary sentinel execution",
    )
    is_covered: bool = Field(default=False, description="Whether an executed test run has covered this node")


class CanaryMap(BaseModel):
    """Consolidated application map and test topology for a CanaryTarget."""

    target_id: Optional[int] = None
    base_url: str
    nodes: List[DiscoveredNode] = Field(default_factory=list)
    summary: Optional[str] = None
    explored_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    total_pages: int = 0
    total_forms: int = 0
    total_flows: int = 0
    coverage_percentage: float = 0.0


# ---------------------------------------------------------------------------
# Business Category Classifier & Test Prompt Synthesizer
# ---------------------------------------------------------------------------

def classify_business_category(label: str, url: str = "", element_type: str = "page") -> str:
    """Classify an application node into its primary business function."""
    text = f"{label} {url} {element_type}".lower()

    if re.search(r"\b(wallet|connect wallet|web3|metamask|phantom|keplr|rainbow|coinbase)\b", text):
        return "wallet"
    if re.search(r"\b(stake|staking|yield|farm|vault|apr|apy|unstake|reward|validator)\b", text):
        return "staking"
    if re.search(r"\b(swap|trade|exchange|liquidity|slippage|pool|dex)\b", text):
        return "swap"
    if re.search(r"\b(checkout|cart|buy|purchase|order|payment|billing|invoice|stripe|subtotal)\b", text):
        return "checkout"
    if re.search(r"\b(login|signin|sign in|log in|register|signup|sign up|auth|password|logout)\b", text):
        return "authentication"
    if re.search(r"\b(account|profile|settings|preferences|user|profile)\b", text):
        return "account"
    if re.search(r"\b(docs|documentation|about|faq|terms|privacy|blog|guide|whitepaper)\b", text):
        return "content"
    if re.search(r"\b(home|dashboard|nav|menu|overview|explore|portal)\b", text):
        return "navigation"

    return "other"


def generate_suggested_prompt(node: DiscoveredNode, base_url: str) -> str:
    """Draft a natural-language QA test prompt that exercises this node."""
    target_loc = node.url or base_url

    if node.node_type == "form":
        if node.business_category == "authentication":
            return (
                f"Test authentication form '{node.label}' on {target_loc} with valid and invalid "
                f"credentials, verifying appropriate validation error feedback."
            )
        return (
            f"Verify that form '{node.label}' on {target_loc} displays required input fields, "
            f"accepts valid inputs, and handles submission validation."
        )

    cat = node.business_category
    if cat == "swap":
        return (
            f"Navigate to '{node.label}' on {target_loc}, input token swap amounts, and verify "
            f"slippage tolerance, exchange rate, and fee estimates render properly."
        )
    if cat == "staking":
        return (
            f"Navigate to '{node.label}' on {target_loc}, verify APY/pool metrics display, "
            f"and test staking amount entry interaction."
        )
    if cat == "wallet":
        return (
            f"Click '{node.label}' on {target_loc} and verify Web3 wallet connection modal "
            f"triggers properly with provider selections."
        )
    if cat == "checkout":
        return (
            f"Exercise full checkout flow on '{node.label}' ({target_loc}), validating item subtotal, "
            f"tax calculation, and order submission confirmation."
        )
    if cat == "authentication":
        return (
            f"Navigate to '{node.label}' on {target_loc} and verify sign-in/registration elements render."
        )

    return f"Navigate to '{node.label}' ({target_loc}) and verify page renders with no HTTP or console errors."


# ---------------------------------------------------------------------------
# Canary Explorer Engine
# ---------------------------------------------------------------------------

class CanaryExplorer:
    """
    Autonomous web crawler & topology extractor for CanaryTargets.
    Discovers pages, forms, and flows, classifying them into a verifiable CanaryMap.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def _normalize_url(self, raw_url: str) -> str:
        """Ensure standard protocol prefix."""
        u = raw_url.strip()
        if not u.startswith(("http://", "https://", "about:", "data:", "file://")):
            if u.startswith("localhost") or u.startswith("127.0.0.1"):
                return f"http://{u}"
            return f"https://{u}"
        return u

    def _is_same_origin(self, base_url: str, candidate_url: str) -> bool:
        """Check whether candidate link is on the same host or relative to base."""
        base_parsed = urlparse(base_url)
        cand_parsed = urlparse(candidate_url)
        if not cand_parsed.netloc:
            return True
        return base_parsed.netloc.lower() == cand_parsed.netloc.lower()

    def explore(
        self,
        base_url: str,
        target_id: Optional[int] = None,
        max_pages: int = 5,
        browser_session: Optional[BrowserSession] = None,
    ) -> CanaryMap:
        """
        Explore a web application / dApp starting from base_url.
        Extracts pages, forms, and flows, and compiles them into a CanaryMap.
        """
        normalized_base = self._normalize_url(base_url)
        session = browser_session or get_browser_session(headless=self.headless)
        discovered_nodes: List[DiscoveredNode] = []
        visited_urls: Set[str] = set()
        seen_labels: Set[str] = set()

        try:
            page = session.ensure_started()
        except Exception as e:
            logger.error(f"Failed to launch browser session: {e}")
            root_node = DiscoveredNode(
                node_type="page",
                label="Landing Page (Offline/Unreachable)",
                url=normalized_base,
                description=f"Initial crawl failed: {e}",
                business_category="navigation",
            )
            root_node.suggested_prompt = generate_suggested_prompt(root_node, normalized_base)
            canary_map = CanaryMap(
                target_id=target_id,
                base_url=normalized_base,
                nodes=[root_node],
                summary=f"Exploration failed to connect to {normalized_base}: {e}",
                total_pages=1,
            )
            if target_id:
                save_canary_map_to_db(canary_map, target_id)
            return canary_map

        # 1. Inspect Landing / Base Page
        nav_result = session.navigate(normalized_base)
        visited_urls.add(normalized_base)
        current_title = nav_result.get("title", "Landing Page")
        root_label = current_title.strip() if current_title and current_title != "Navigation Error" else "Landing Page"

        root_node = DiscoveredNode(
            node_type="page",
            label=root_label[:120],
            url=normalized_base,
            description="Root entry point of application",
            business_category="navigation",
        )
        seen_labels.add(root_node.label)

        # Extract page metadata, forms, and navigation links via browser DOM inspection
        page_analysis_js = """
        () => {
            const results = {
                links: [],
                forms: [],
                actions: []
            };

            // Extract internal links
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.getAttribute('href');
                const text = (a.innerText || a.getAttribute('aria-label') || '').trim();
                if (href && text && !href.startsWith('#') && !href.startsWith('javascript:')) {
                    results.links.push({ text: text.slice(0, 80), href: href });
                }
            });

            // Extract forms
            document.querySelectorAll('form').forEach((f, idx) => {
                const inputs = Array.from(f.querySelectorAll('input, select, textarea')).map(i => ({
                    name: i.getAttribute('name') || i.id || '',
                    type: i.getAttribute('type') || i.tagName.toLowerCase(),
                    placeholder: i.getAttribute('placeholder') || ''
                }));
                const submitBtn = f.querySelector('button[type="submit"], input[type="submit"], button');
                const btnText = submitBtn ? (submitBtn.innerText || submitBtn.value || '').trim() : '';
                const formName = f.getAttribute('name') || f.id || (btnText ? btnText + ' Form' : 'Form #' + (idx + 1));
                results.forms.push({
                    name: formName,
                    fieldsCount: inputs.length,
                    submitText: btnText,
                    inputs: inputs.slice(0, 8)
                });
            });

            // Extract key Web3 / actionable buttons
            document.querySelectorAll('button, [role="button"]').forEach(btn => {
                const text = (btn.innerText || btn.getAttribute('aria-label') || '').trim();
                if (text && text.length < 50) {
                    const lower = text.toLowerCase();
                    if (
                        lower.includes('wallet') || lower.includes('swap') ||
                        lower.includes('stake') || lower.includes('connect') ||
                        lower.includes('checkout') || lower.includes('claim') ||
                        lower.includes('buy')
                    ) {
                        results.actions.push({ text: text, tag: btn.tagName.toLowerCase() });
                    }
                }
            });

            return results;
        }
        """

        analysis: Dict[str, Any] = {"links": [], "forms": [], "actions": []}
        try:
            analysis = page.evaluate(page_analysis_js)
        except Exception as eval_err:
            logger.warning(f"DOM evaluation error on {normalized_base}: {eval_err}")

        # Process Forms on root page
        for form in analysis.get("forms", []):
            fname = form.get("name", "Input Form")
            if fname not in seen_labels:
                fnode = DiscoveredNode(
                    node_type="form",
                    label=fname,
                    url=normalized_base,
                    description=f"Interactive form with {form.get('fieldsCount', 0)} inputs. Submit action: {form.get('submitText', 'Submit')}",
                    business_category=classify_business_category(fname, normalized_base, element_type="form"),
                    depends_on=[root_node.label],
                )
                fnode.suggested_prompt = generate_suggested_prompt(fnode, normalized_base)
                discovered_nodes.append(fnode)
                seen_labels.add(fname)
                root_node.connects_to.append(fname)

        # Process Web3 / Transaction Actions
        for act in analysis.get("actions", [])[:5]:
            atext = act.get("text")
            if atext and atext not in seen_labels:
                anode = DiscoveredNode(
                    node_type="action",
                    label=atext,
                    url=normalized_base,
                    description=f"Interactive action trigger: {atext}",
                    business_category=classify_business_category(atext, normalized_base, element_type="action"),
                    depends_on=[root_node.label],
                )
                anode.suggested_prompt = generate_suggested_prompt(anode, normalized_base)
                discovered_nodes.append(anode)
                seen_labels.add(atext)
                root_node.connects_to.append(atext)

        # Process Links & Crawl Second Level
        candidate_links = []
        for lk in analysis.get("links", []):
            raw_href = lk.get("href", "")
            resolved_url = urljoin(normalized_base, raw_href)
            if self._is_same_origin(normalized_base, resolved_url) and resolved_url not in visited_urls:
                candidate_links.append((lk.get("text") or "Subpage", resolved_url))

        pages_crawled = 1
        for link_text, link_url in candidate_links:
            if pages_crawled >= max_pages:
                break
            if link_url in visited_urls or link_text in seen_labels:
                continue

            try:
                sub_nav = session.navigate(link_url)
                visited_urls.add(link_url)
                pages_crawled += 1

                sub_title = sub_nav.get("title", link_text)
                clean_label = sub_title.strip() if sub_title and sub_title != "Navigation Error" else link_text
                if clean_label in seen_labels:
                    clean_label = f"{clean_label} ({link_text})"

                sub_node = DiscoveredNode(
                    node_type="page",
                    label=clean_label[:120],
                    url=link_url,
                    description=f"Discovered page via link '{link_text}'",
                    business_category=classify_business_category(clean_label, link_url),
                    depends_on=[root_node.label],
                )
                sub_node.suggested_prompt = generate_suggested_prompt(sub_node, normalized_base)
                discovered_nodes.append(sub_node)
                seen_labels.add(clean_label)
                root_node.connects_to.append(clean_label)

                # Subpage forms check
                sub_analysis = page.evaluate(page_analysis_js)
                for form in sub_analysis.get("forms", []):
                    fname = form.get("name", "Form")
                    full_fname = f"{fname} ({clean_label})"
                    if full_fname not in seen_labels:
                        fnode = DiscoveredNode(
                            node_type="form",
                            label=full_fname,
                            url=link_url,
                            description=f"Form on {clean_label} with {form.get('fieldsCount', 0)} fields.",
                            business_category=classify_business_category(fname, link_url, element_type="form"),
                            depends_on=[clean_label],
                        )
                        fnode.suggested_prompt = generate_suggested_prompt(fnode, normalized_base)
                        discovered_nodes.append(fnode)
                        seen_labels.add(full_fname)
                        sub_node.connects_to.append(full_fname)

            except Exception as crawl_err:
                logger.warning(f"Error exploring child URL {link_url}: {crawl_err}")
                continue

        # Add root node at the head
        root_node.suggested_prompt = generate_suggested_prompt(root_node, normalized_base)
        all_nodes = [root_node] + discovered_nodes

        # Calculate metrics
        pages_count = sum(1 for n in all_nodes if n.node_type == "page")
        forms_count = sum(1 for n in all_nodes if n.node_type == "form")
        flows_count = sum(1 for n in all_nodes if n.node_type in ("flow", "action"))

        summary_text = (
            f"Autonomous Canary Map for {normalized_base}: Discovered {len(all_nodes)} nodes "
            f"({pages_count} pages, {forms_count} forms, {flows_count} actions/flows). "
            f"Primary entry point: '{root_node.label}'."
        )

        canary_map = CanaryMap(
            target_id=target_id,
            base_url=normalized_base,
            nodes=all_nodes,
            summary=summary_text,
            total_pages=pages_count,
            total_forms=forms_count,
            total_flows=flows_count,
            coverage_percentage=0.0,
        )

        # Persist to DB if target_id provided
        if target_id:
            try:
                save_canary_map_to_db(canary_map, target_id)
            except Exception as db_err:
                logger.error(f"Failed to persist CanaryMap to DB for target {target_id}: {db_err}")

        return canary_map


# ---------------------------------------------------------------------------
# Database Persistence Helpers
# ---------------------------------------------------------------------------

def save_canary_map_to_db(canary_map: CanaryMap, target_id: int) -> List[CanaryMapNode]:
    """
    Persist discovered CanaryMap nodes to `canary_map_nodes` table.
    Overwrites/refreshes existing nodes for this target.
    """
    saved_records: List[CanaryMapNode] = []
    with SessionLocal() as db:
        # Check target existence
        target = db.query(CanaryTarget).filter(CanaryTarget.id == target_id).first()
        if not target:
            logger.warning(f"Target {target_id} not found in DB; cannot save map nodes.")
            return saved_records

        # Clear existing nodes for clean refresh
        db.query(CanaryMapNode).filter(CanaryMapNode.target_id == target_id).delete()
        db.flush()

        for node in canary_map.nodes:
            db_node = CanaryMapNode(
                target_id=target_id,
                node_type=node.node_type,
                label=node.label,
                url=node.url,
                description=node.description,
                business_category=node.business_category,
                connects_to=json.dumps(node.connects_to) if node.connects_to else None,
                depends_on=json.dumps(node.depends_on) if node.depends_on else None,
                flow_steps=json.dumps(node.flow_steps) if node.flow_steps else None,
                suggested_prompt=node.suggested_prompt,
                is_covered=node.is_covered,
                created_at=utc_now(),
            )
            db.add(db_node)
            saved_records.append(db_node)

        db.commit()
        logger.info(f"Successfully saved {len(saved_records)} CanaryMap nodes for target {target_id}.")

    return saved_records


def get_canary_map_for_target(target_id: int) -> Optional[CanaryMap]:
    """Retrieve the stored CanaryMap topology for a CanaryTarget from the database."""
    with SessionLocal() as db:
        target = db.query(CanaryTarget).filter(CanaryTarget.id == target_id).first()
        if not target:
            return None

        db_nodes = (
            db.query(CanaryMapNode)
            .filter(CanaryMapNode.target_id == target_id)
            .order_by(CanaryMapNode.id.asc())
            .all()
        )

        if not db_nodes:
            return None

        nodes: List[DiscoveredNode] = []
        covered_count = 0

        for r in db_nodes:
            if r.is_covered:
                covered_count += 1
            nodes.append(
                DiscoveredNode(
                    node_type=r.node_type,
                    label=r.label,
                    url=r.url,
                    description=r.description,
                    business_category=r.business_category,
                    connects_to=json.loads(r.connects_to) if r.connects_to else [],
                    depends_on=json.loads(r.depends_on) if r.depends_on else [],
                    flow_steps=json.loads(r.flow_steps) if r.flow_steps else [],
                    suggested_prompt=r.suggested_prompt,
                    is_covered=r.is_covered,
                )
            )

        total = len(nodes)
        pages_count = sum(1 for n in nodes if n.node_type == "page")
        forms_count = sum(1 for n in nodes if n.node_type == "form")
        flows_count = sum(1 for n in nodes if n.node_type in ("flow", "action"))
        coverage_pct = round((covered_count / total * 100.0), 1) if total > 0 else 0.0

        return CanaryMap(
            target_id=target_id,
            base_url=target.base_url,
            nodes=nodes,
            summary=(
                f"Canary Map for {target.name}: {total} nodes "
                f"({pages_count} pages, {forms_count} forms, {flows_count} actions). "
                f"Coverage: {coverage_pct}% ({covered_count}/{total} nodes covered)."
            ),
            total_pages=pages_count,
            total_forms=forms_count,
            total_flows=flows_count,
            coverage_percentage=coverage_pct,
        )
