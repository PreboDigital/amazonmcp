"""AI action validator.

Validates AI-proposed actions (inline + queue) against the live database
*before* they hit the approval queue or the apply-inline path. Stops three
classes of bug we kept seeing in production:

1. **Hallucinated IDs** — AI invents a ``targetId`` / ``campaignId`` /
   ``adGroupId`` that does not exist for this credential/profile. MCP
   rejects → user sees "approved → failed" → "AI never works".
2. **Out-of-range bids/budgets** — AI proposes ``bid: 0.001`` or
   ``dailyBudget: 99999``. Amazon's documented bounds reject these.
3. **Malformed payload shape** — missing required body wrappers,
   wrong-typed values, etc. ``app.utils.normalize_mcp_arguments`` already
   reshapes most of this; the validator is the final gate.

The validator is *pure* with respect to the DB: it never mutates state.
It returns a ``ValidationResult`` describing whether the action is safe
to execute and, if so, the normalized payload.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ad, AdGroup, Campaign, Credential, Target
from app.utils import normalize_mcp_call

logger = logging.getLogger(__name__)


# ── Bounds (Amazon Ads API documented limits) ─────────────────────────
MIN_BID = 0.02
MAX_BID = 1000.0  # SP/SB accept very high bids; Amazon rejects > 1000 USD/equiv
MIN_DAILY_BUDGET = 1.0
MAX_DAILY_BUDGET = 1_000_000.0
MAX_KEYWORD_LENGTH = 80  # Amazon SP keyword limit
MAX_NAME_LENGTH = 255

ALLOWED_INLINE_TOOLS: frozenset[str] = frozenset({
    "campaign_management-update_target_bid",
    "campaign_management-update_target",
    "campaign_management-update_campaign_budget",
    "campaign_management-update_campaign",
    "campaign_management-update_campaign_state",
    "campaign_management-delete_campaign",
    "campaign_management-create_ad_group",
    "campaign_management-update_ad_group",
    "campaign_management-delete_ad_group",
    "campaign_management-create_ad",
    "campaign_management-update_ad",
    "campaign_management-delete_ad",
    "campaign_management-create_target",
    "campaign_management-delete_target",
})

ALLOWED_QUEUE_TOOLS: frozenset[str] = ALLOWED_INLINE_TOOLS | frozenset({
    "_harvest_execute",
    "_ai_campaign_create",
    "_request_sync",
})

VALID_SYNC_KINDS: frozenset[str] = frozenset({
    "campaigns",
    "reports",
    "search_terms",
    "products",
})

VALID_SYNC_RANGE_PRESETS: frozenset[str] = frozenset({
    "today",
    "yesterday",
    "last_7_days",
    "last_30_days",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "month_to_yesterday",
    "year_to_date",
})

VALID_MATCH_TYPES: frozenset[str] = frozenset({"EXACT", "PHRASE", "BROAD"})
VALID_STATES: frozenset[str] = frozenset({"ENABLED", "PAUSED", "ARCHIVED"})
VALID_AD_PRODUCTS: frozenset[str] = frozenset({
    "SPONSORED_PRODUCTS",
    "SPONSORED_BRANDS",
    "SPONSORED_DISPLAY",
})
VALID_TARGETING_TYPES: frozenset[str] = frozenset({"AUTO", "MANUAL"})

# Plan caps — defensive bounds so a runaway AI plan can't queue 1000 keywords
MAX_AD_GROUPS_PER_PLAN = 20
MAX_KEYWORDS_PER_AD_GROUP = 200


@dataclass
class ValidationResult:
    """Outcome of validating a single AI-proposed action."""

    ok: bool
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    normalized_action: Optional[dict] = None
    tool: Optional[str] = None
    arguments: Optional[dict] = None

    def to_user_message(self) -> str:
        """Human-readable failure reason for surfacing back to chat UI."""
        if self.ok:
            return "ok"
        return self.error or "Invalid action"


# ── Helpers ───────────────────────────────────────────────────────────

def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _coerce_state(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    up = value.strip().upper()
    return up if up in VALID_STATES else None


def _coerce_match_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    up = value.strip().upper()
    return up if up in VALID_MATCH_TYPES else None


def _bid_in_range(bid: Optional[float]) -> Optional[str]:
    if bid is None:
        return None
    if bid < MIN_BID:
        return f"Bid {bid} below minimum ${MIN_BID:.2f}"
    if bid > MAX_BID:
        return f"Bid {bid} exceeds maximum ${MAX_BID:.2f}"
    return None


def _budget_in_range(budget: Optional[float]) -> Optional[str]:
    if budget is None:
        return None
    if budget < MIN_DAILY_BUDGET:
        return f"Daily budget {budget} below minimum ${MIN_DAILY_BUDGET:.2f}"
    if budget > MAX_DAILY_BUDGET:
        return f"Daily budget {budget} exceeds maximum ${MAX_DAILY_BUDGET:,.2f}"
    return None


async def _target_exists(
    db: AsyncSession,
    cred_id,
    profile_id: Optional[str],
    amazon_target_id: str,
) -> bool:
    stmt = select(Target.id).where(
        Target.credential_id == cred_id,
        Target.amazon_target_id == amazon_target_id,
    )
    if profile_id is not None:
        # Targets table doesn't carry profile_id directly; we trust cred scope.
        pass
    result = await db.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None


async def _campaign_exists(
    db: AsyncSession,
    cred_id,
    profile_id: Optional[str],
    amazon_campaign_id: str,
) -> bool:
    stmt = select(Campaign.id).where(
        Campaign.credential_id == cred_id,
        Campaign.amazon_campaign_id == amazon_campaign_id,
    )
    if profile_id is not None:
        stmt = stmt.where(Campaign.profile_id == profile_id)
    result = await db.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None


async def _ad_group_exists(
    db: AsyncSession,
    cred_id,
    amazon_ad_group_id: str,
) -> bool:
    stmt = select(AdGroup.id).where(
        AdGroup.credential_id == cred_id,
        AdGroup.amazon_ad_group_id == amazon_ad_group_id,
    )
    result = await db.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None


async def _ad_exists(
    db: AsyncSession,
    cred_id,
    amazon_ad_id: str,
) -> bool:
    stmt = select(Ad.id).where(
        Ad.credential_id == cred_id,
        Ad.amazon_ad_id == amazon_ad_id,
    )
    result = await db.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None


# ── Validators per tool ───────────────────────────────────────────────

async def _validate_target_bid_or_state(
    body: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[Optional[str], list[str]]:
    targets = body.get("targets")
    if not isinstance(targets, list) or not targets:
        return "body.targets must be a non-empty list", []
    warnings: list[str] = []
    for idx, t in enumerate(targets):
        if not isinstance(t, dict):
            return f"body.targets[{idx}] must be an object", warnings
        tid = t.get("targetId")
        if not tid:
            return f"body.targets[{idx}].targetId is required", warnings
        if not await _target_exists(db, cred.id, profile_id, str(tid)):
            return f"Target {tid} not found for this account — sync targets first", warnings
        if "bid" in t:
            bid = _to_float(t["bid"])
            if bid is None:
                return f"body.targets[{idx}].bid is not numeric", warnings
            err = _bid_in_range(bid)
            if err:
                return err, warnings
            t["bid"] = bid
        if "state" in t:
            st = _coerce_state(t["state"])
            if st is None:
                return f"body.targets[{idx}].state must be ENABLED|PAUSED|ARCHIVED", warnings
            t["state"] = st
    return None, warnings


async def _validate_campaign_update(
    body: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
    require_budget: bool = False,
) -> tuple[Optional[str], list[str]]:
    campaigns = body.get("campaigns")
    if not isinstance(campaigns, list) or not campaigns:
        return "body.campaigns must be a non-empty list", []
    warnings: list[str] = []
    for idx, c in enumerate(campaigns):
        if not isinstance(c, dict):
            return f"body.campaigns[{idx}] must be an object", warnings
        cid = c.get("campaignId")
        if not cid:
            return f"body.campaigns[{idx}].campaignId is required", warnings
        if not await _campaign_exists(db, cred.id, profile_id, str(cid)):
            return f"Campaign {cid} not found for this account — sync campaigns first", warnings
        if "dailyBudget" in c:
            budget = _to_float(c["dailyBudget"])
            if budget is None:
                return f"body.campaigns[{idx}].dailyBudget is not numeric", warnings
            err = _budget_in_range(budget)
            if err:
                return err, warnings
            c["dailyBudget"] = budget
        elif require_budget:
            return f"body.campaigns[{idx}].dailyBudget is required", warnings
        if "state" in c:
            st = _coerce_state(c["state"])
            if st is None:
                return f"body.campaigns[{idx}].state must be ENABLED|PAUSED|ARCHIVED", warnings
            c["state"] = st
        if "name" in c:
            name = (c.get("name") or "").strip()
            if not name:
                return f"body.campaigns[{idx}].name cannot be empty", warnings
            if len(name) > MAX_NAME_LENGTH:
                return f"body.campaigns[{idx}].name exceeds {MAX_NAME_LENGTH} chars", warnings
            c["name"] = name
    return None, warnings


async def _validate_ad_group_update(
    body: dict,
    db: AsyncSession,
    cred: Credential,
) -> tuple[Optional[str], list[str]]:
    groups = body.get("adGroups")
    if not isinstance(groups, list) or not groups:
        return "body.adGroups must be a non-empty list", []
    warnings: list[str] = []
    for idx, g in enumerate(groups):
        if not isinstance(g, dict):
            return f"body.adGroups[{idx}] must be an object", warnings
        gid = g.get("adGroupId")
        if not gid:
            return f"body.adGroups[{idx}].adGroupId is required", warnings
        if not await _ad_group_exists(db, cred.id, str(gid)):
            return f"Ad group {gid} not found for this account — sync ad groups first", warnings
        if "defaultBid" in g:
            bid = _to_float(g["defaultBid"])
            if bid is None:
                return f"body.adGroups[{idx}].defaultBid is not numeric", warnings
            err = _bid_in_range(bid)
            if err:
                return err, warnings
            g["defaultBid"] = bid
        if "state" in g:
            st = _coerce_state(g["state"])
            if st is None:
                return f"body.adGroups[{idx}].state must be ENABLED|PAUSED|ARCHIVED", warnings
            g["state"] = st
        if "name" in g:
            name = (g.get("name") or "").strip()
            if not name:
                return f"body.adGroups[{idx}].name cannot be empty", warnings
            if len(name) > MAX_NAME_LENGTH:
                return f"body.adGroups[{idx}].name exceeds {MAX_NAME_LENGTH} chars", warnings
            g["name"] = name
    return None, warnings


async def _validate_ad_update(
    body: dict,
    db: AsyncSession,
    cred: Credential,
) -> tuple[Optional[str], list[str]]:
    ads = body.get("ads")
    if not isinstance(ads, list) or not ads:
        return "body.ads must be a non-empty list", []
    warnings: list[str] = []
    for idx, a in enumerate(ads):
        if not isinstance(a, dict):
            return f"body.ads[{idx}] must be an object", warnings
        aid = a.get("adId")
        if not aid:
            return f"body.ads[{idx}].adId is required", warnings
        if not await _ad_exists(db, cred.id, str(aid)):
            return f"Ad {aid} not found for this account — sync ads first", warnings
        if "state" in a:
            st = _coerce_state(a["state"])
            if st is None:
                return f"body.ads[{idx}].state must be ENABLED|PAUSED|ARCHIVED", warnings
            a["state"] = st
    return None, warnings


async def _validate_create_target(
    body: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[Optional[str], list[str]]:
    targets = body.get("targets")
    if not isinstance(targets, list) or not targets:
        return "body.targets must be a non-empty list", []
    warnings: list[str] = []
    for idx, t in enumerate(targets):
        if not isinstance(t, dict):
            return f"body.targets[{idx}] must be an object", warnings

        cid = t.get("campaignId")
        gid = t.get("adGroupId")
        if not cid:
            return f"body.targets[{idx}].campaignId is required", warnings
        if not gid:
            return f"body.targets[{idx}].adGroupId is required", warnings
        if not await _campaign_exists(db, cred.id, profile_id, str(cid)):
            return f"Campaign {cid} not found — sync campaigns first", warnings
        if not await _ad_group_exists(db, cred.id, str(gid)):
            return f"Ad group {gid} not found — sync ad groups first", warnings

        expression = t.get("expression") or t.get("keywordText") or t.get("keyword")
        if not expression or not isinstance(expression, str):
            return f"body.targets[{idx}].expression (keyword text) is required", warnings
        expr = expression.strip()
        if not expr:
            return f"body.targets[{idx}].expression cannot be empty", warnings
        if len(expr) > MAX_KEYWORD_LENGTH:
            return f"body.targets[{idx}].expression exceeds {MAX_KEYWORD_LENGTH} chars", warnings
        t["expression"] = expr

        match_type = _coerce_match_type(t.get("matchType"))
        if not match_type:
            return f"body.targets[{idx}].matchType must be EXACT|PHRASE|BROAD", warnings
        t["matchType"] = match_type

        if "bid" in t:
            bid = _to_float(t["bid"])
            if bid is None:
                return f"body.targets[{idx}].bid is not numeric", warnings
            err = _bid_in_range(bid)
            if err:
                return err, warnings
            t["bid"] = bid

        if "state" in t:
            st = _coerce_state(t["state"])
            if st is None:
                return f"body.targets[{idx}].state must be ENABLED|PAUSED|ARCHIVED", warnings
            t["state"] = st

    return None, warnings


async def _validate_delete_target(
    body: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[Optional[str], list[str]]:
    ids = body.get("targetIds")
    if not isinstance(ids, list) or not ids:
        return "body.targetIds must be a non-empty list", []
    warnings: list[str] = []
    for idx, tid in enumerate(ids):
        if not tid:
            return f"body.targetIds[{idx}] is empty", warnings
        if not await _target_exists(db, cred.id, profile_id, str(tid)):
            warnings.append(f"Target {tid} not in cache (may already be deleted)")
    return None, warnings


async def _validate_delete_campaign(
    body: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[Optional[str], list[str]]:
    ids = body.get("campaignIds")
    if not isinstance(ids, list) or not ids:
        return "body.campaignIds must be a non-empty list", []
    warnings: list[str] = []
    for idx, cid in enumerate(ids):
        if not cid:
            return f"body.campaignIds[{idx}] is empty", warnings
        if not await _campaign_exists(db, cred.id, profile_id, str(cid)):
            warnings.append(f"Campaign {cid} not in cache (may already be deleted)")
    return None, warnings


async def _validate_delete_ad_group(
    body: dict,
    db: AsyncSession,
    cred: Credential,
) -> tuple[Optional[str], list[str]]:
    ids = body.get("adGroupIds")
    if not isinstance(ids, list) or not ids:
        return "body.adGroupIds must be a non-empty list", []
    warnings: list[str] = []
    for idx, gid in enumerate(ids):
        if not gid:
            return f"body.adGroupIds[{idx}] is empty", warnings
        if not await _ad_group_exists(db, cred.id, str(gid)):
            warnings.append(f"Ad group {gid} not in cache (may already be deleted)")
    return None, warnings


async def _validate_delete_ad(
    body: dict,
    db: AsyncSession,
    cred: Credential,
) -> tuple[Optional[str], list[str]]:
    ids = body.get("adIds")
    if not isinstance(ids, list) or not ids:
        return "body.adIds must be a non-empty list", []
    warnings: list[str] = []
    for idx, aid in enumerate(ids):
        if not aid:
            return f"body.adIds[{idx}] is empty", warnings
        if not await _ad_exists(db, cred.id, str(aid)):
            warnings.append(f"Ad {aid} not in cache (may already be deleted)")
    return None, warnings


async def _validate_create_ad_group(
    body: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[Optional[str], list[str]]:
    groups = body.get("adGroups")
    if not isinstance(groups, list) or not groups:
        return "body.adGroups must be a non-empty list", []
    warnings: list[str] = []
    for idx, g in enumerate(groups):
        if not isinstance(g, dict):
            return f"body.adGroups[{idx}] must be an object", warnings
        cid = g.get("campaignId")
        if not cid:
            return f"body.adGroups[{idx}].campaignId is required", warnings
        if not await _campaign_exists(db, cred.id, profile_id, str(cid)):
            return f"Campaign {cid} not found — sync campaigns first", warnings
        name = (g.get("name") or "").strip()
        if not name:
            return f"body.adGroups[{idx}].name is required", warnings
        if len(name) > MAX_NAME_LENGTH:
            return f"body.adGroups[{idx}].name exceeds {MAX_NAME_LENGTH} chars", warnings
        g["name"] = name
        bid = _to_float(g.get("defaultBid"))
        if bid is None:
            return f"body.adGroups[{idx}].defaultBid is required and must be numeric", warnings
        bid_err = _bid_in_range(bid)
        if bid_err:
            return f"body.adGroups[{idx}] {bid_err}", warnings
        g["defaultBid"] = bid
        if "state" in g:
            st = _coerce_state(g["state"])
            if st is None:
                return f"body.adGroups[{idx}].state must be ENABLED|PAUSED|ARCHIVED", warnings
            g["state"] = st
    return None, warnings


async def _validate_create_ad(
    body: dict,
    db: AsyncSession,
    cred: Credential,
) -> tuple[Optional[str], list[str]]:
    ads = body.get("ads")
    if not isinstance(ads, list) or not ads:
        return "body.ads must be a non-empty list", []
    warnings: list[str] = []
    for idx, a in enumerate(ads):
        if not isinstance(a, dict):
            return f"body.ads[{idx}] must be an object", warnings
        gid = a.get("adGroupId")
        if not gid:
            return f"body.ads[{idx}].adGroupId is required", warnings
        if not await _ad_group_exists(db, cred.id, str(gid)):
            return f"Ad group {gid} not found — sync ad groups first", warnings
        if not (a.get("asin") or a.get("sku")):
            return f"body.ads[{idx}] requires asin or sku", warnings
        if "state" in a:
            st = _coerce_state(a["state"])
            if st is None:
                return f"body.ads[{idx}].state must be ENABLED|PAUSED|ARCHIVED", warnings
            a["state"] = st
        if "name" in a:
            name = (a.get("name") or "").strip()
            if len(name) > MAX_NAME_LENGTH:
                return f"body.ads[{idx}].name exceeds {MAX_NAME_LENGTH} chars", warnings
            a["name"] = name
    return None, warnings


# ── Queue-only tool plan validators ───────────────────────────────────

async def _validate_harvest_args(
    args: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """Validate ``_harvest_execute`` arguments before queuing.

    Required: ``source_campaign_id`` exists. Optional: ``target_campaign_id``
    exists when ``target_mode == 'existing'``; ``match_type`` is one of the
    allowed values; numeric thresholds are non-negative.
    """
    warnings: list[str] = []
    if not isinstance(args, dict):
        return "harvest arguments must be an object", warnings

    src = args.get("source_campaign_id")
    if not src:
        return "harvest: source_campaign_id is required", warnings
    if not await _campaign_exists(db, cred.id, profile_id, str(src)):
        return f"harvest: source campaign {src} not found — sync campaigns first", warnings

    target_mode = (args.get("target_mode") or "new").lower()
    if target_mode not in ("new", "existing"):
        return "harvest: target_mode must be 'new' or 'existing'", warnings

    if target_mode == "existing":
        target_id = args.get("target_campaign_id")
        if not target_id:
            return "harvest: target_campaign_id is required when target_mode='existing'", warnings
        if not await _campaign_exists(db, cred.id, profile_id, str(target_id)):
            return f"harvest: target campaign {target_id} not found — sync campaigns first", warnings
        # Amazon SP keywords/targets live on an ad group, not a campaign.
        # Require the caller to pin the exact ad group instead of letting
        # the harvester pick the first ad group it finds at run-time
        # (which silently lands keywords in product-targeting groups when
        # the campaign mixes ad-group types).
        target_ag_id = args.get("target_ad_group_id")
        if not target_ag_id:
            return (
                "harvest: target_ad_group_id is required when target_mode='existing'",
                warnings,
            )
        if not await _ad_group_exists(db, cred.id, str(target_ag_id)):
            return (
                f"harvest: target ad group {target_ag_id} not found — sync ad groups first",
                warnings,
            )

    match_type = args.get("match_type")
    if match_type is not None and _coerce_match_type(match_type) is None:
        return "harvest: match_type must be EXACT|PHRASE|BROAD", warnings

    sales_threshold = _to_float(args.get("sales_threshold"))
    if sales_threshold is not None and sales_threshold < 0:
        return "harvest: sales_threshold cannot be negative", warnings

    acos_threshold = args.get("acos_threshold")
    if acos_threshold is not None:
        v = _to_float(acos_threshold)
        if v is None or v < 0:
            return "harvest: acos_threshold must be a non-negative number", warnings

    clicks_threshold = args.get("clicks_threshold")
    if clicks_threshold is not None:
        try:
            ci = int(clicks_threshold)
        except (TypeError, ValueError):
            return "harvest: clicks_threshold must be an integer", warnings
        if ci < 0:
            return "harvest: clicks_threshold cannot be negative", warnings

    lookback_days = args.get("lookback_days")
    if lookback_days is not None:
        try:
            li = int(lookback_days)
        except (TypeError, ValueError):
            return "harvest: lookback_days must be an integer", warnings
        if li < 1 or li > 90:
            return "harvest: lookback_days must be between 1 and 90", warnings

    return None, warnings


async def _validate_campaign_plan(
    args: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[Optional[str], list[str]]:
    """Validate an ``_ai_campaign_create`` plan structure before queuing.

    Catches bad plans (missing ASIN, malformed budgets, too many keywords,
    unknown ad product) before they hit the multi-step CampaignCreationService
    where partial failures leave orphan resources.
    """
    warnings: list[str] = []
    if not isinstance(args, dict):
        return "campaign plan must be an object", warnings

    plan = args.get("plan") if "plan" in args else args
    if not isinstance(plan, dict):
        return "campaign plan: 'plan' object is required", warnings

    campaign = plan.get("campaign")
    if not isinstance(campaign, dict):
        return "campaign plan: 'campaign' object is required", warnings

    name = (campaign.get("name") or "").strip()
    if not name:
        return "campaign plan: campaign.name is required", warnings
    if len(name) > MAX_NAME_LENGTH:
        return f"campaign plan: campaign.name exceeds {MAX_NAME_LENGTH} chars", warnings

    ad_product = (
        campaign.get("adProduct")
        or campaign.get("ad_product")
        or campaign.get("type")
        or "SPONSORED_PRODUCTS"
    )
    if isinstance(ad_product, str):
        ad_product = ad_product.strip().upper()
    if ad_product not in VALID_AD_PRODUCTS:
        return (
            f"campaign plan: adProduct {ad_product!r} must be one of "
            f"{sorted(VALID_AD_PRODUCTS)}",
            warnings,
        )

    targeting = (
        campaign.get("targetingType")
        or campaign.get("targeting_type")
        or "MANUAL"
    )
    if isinstance(targeting, str):
        targeting = targeting.strip().upper()
    if targeting not in VALID_TARGETING_TYPES:
        return f"campaign plan: targetingType {targeting!r} must be AUTO|MANUAL", warnings

    daily_budget = _to_float(campaign.get("dailyBudget") or campaign.get("daily_budget"))
    if daily_budget is None:
        return "campaign plan: campaign.dailyBudget is required and must be numeric", warnings
    err = _budget_in_range(daily_budget)
    if err:
        return f"campaign plan: {err}", warnings

    # SP requires an ASIN somewhere in the plan
    ad = plan.get("ad") if isinstance(plan.get("ad"), dict) else {}
    plan_asin = (
        ad.get("asin")
        or campaign.get("asin")
        or args.get("product_asin")
        or args.get("asin")
    )
    if ad_product == "SPONSORED_PRODUCTS" and not plan_asin:
        return "campaign plan: SPONSORED_PRODUCTS requires an ASIN (plan.ad.asin)", warnings

    ad_groups = plan.get("ad_groups") or plan.get("adGroups") or []
    if not isinstance(ad_groups, list) or not ad_groups:
        return "campaign plan: at least one ad_group is required", warnings
    if len(ad_groups) > MAX_AD_GROUPS_PER_PLAN:
        return (
            f"campaign plan: too many ad groups ({len(ad_groups)} > {MAX_AD_GROUPS_PER_PLAN})",
            warnings,
        )

    for ag_idx, ag in enumerate(ad_groups):
        if not isinstance(ag, dict):
            return f"campaign plan: ad_groups[{ag_idx}] must be an object", warnings
        ag_name = (ag.get("name") or "").strip()
        if not ag_name:
            return f"campaign plan: ad_groups[{ag_idx}].name is required", warnings
        if len(ag_name) > MAX_NAME_LENGTH:
            return (
                f"campaign plan: ad_groups[{ag_idx}].name exceeds {MAX_NAME_LENGTH} chars",
                warnings,
            )

        default_bid = _to_float(ag.get("defaultBid") or ag.get("default_bid"))
        if default_bid is None:
            return f"campaign plan: ad_groups[{ag_idx}].defaultBid is required", warnings
        bid_err = _bid_in_range(default_bid)
        if bid_err:
            return f"campaign plan: ad_groups[{ag_idx}] {bid_err}", warnings

        keywords = ag.get("keywords") or []
        if not isinstance(keywords, list):
            return f"campaign plan: ad_groups[{ag_idx}].keywords must be a list", warnings
        if len(keywords) > MAX_KEYWORDS_PER_AD_GROUP:
            return (
                f"campaign plan: ad_groups[{ag_idx}] has {len(keywords)} keywords "
                f"(max {MAX_KEYWORDS_PER_AD_GROUP})",
                warnings,
            )
        # Manual campaigns must include keywords, auto must not (Amazon rejects)
        if targeting == "MANUAL" and not keywords:
            return (
                f"campaign plan: ad_groups[{ag_idx}] is in a MANUAL campaign and "
                "needs at least one keyword",
                warnings,
            )
        if targeting == "AUTO" and keywords:
            warnings.append(
                f"ad_groups[{ag_idx}] keywords ignored for AUTO targeting"
            )

        for kw_idx, kw in enumerate(keywords):
            if not isinstance(kw, dict):
                return (
                    f"campaign plan: ad_groups[{ag_idx}].keywords[{kw_idx}] must be an object",
                    warnings,
                )
            text = (kw.get("text") or kw.get("keyword") or "").strip()
            if not text:
                return (
                    f"campaign plan: ad_groups[{ag_idx}].keywords[{kw_idx}].text is required",
                    warnings,
                )
            if len(text) > MAX_KEYWORD_LENGTH:
                return (
                    f"campaign plan: ad_groups[{ag_idx}].keywords[{kw_idx}].text exceeds "
                    f"{MAX_KEYWORD_LENGTH} chars",
                    warnings,
                )
            mt = _coerce_match_type(kw.get("match_type") or kw.get("matchType") or "BROAD")
            if not mt:
                return (
                    f"campaign plan: ad_groups[{ag_idx}].keywords[{kw_idx}].match_type "
                    "must be EXACT|PHRASE|BROAD",
                    warnings,
                )
            sb = kw.get("suggested_bid") or kw.get("suggestedBid") or kw.get("bid")
            if sb is not None:
                bid = _to_float(sb)
                if bid is None:
                    return (
                        f"campaign plan: ad_groups[{ag_idx}].keywords[{kw_idx}].bid is not numeric",
                        warnings,
                    )
                bid_err = _bid_in_range(bid)
                if bid_err:
                    return (
                        f"campaign plan: ad_groups[{ag_idx}].keywords[{kw_idx}] {bid_err}",
                        warnings,
                    )

    return None, warnings


def _validate_request_sync(args: dict) -> tuple[Optional[str], list[str]]:
    """Validate a ``_request_sync`` action.

    This is a non-mutating, advisory action: the AI is asking the UI to
    trigger a data refresh. The router does *not* persist a PendingChange
    for it — instead it surfaces the request to the client which can show
    a "Sync now" button.
    """
    if not isinstance(args, dict):
        return "_request_sync arguments must be an object", []
    kind = args.get("kind") or args.get("type")
    if not isinstance(kind, str):
        return "_request_sync: 'kind' is required", []
    kind_norm = kind.strip().lower()
    if kind_norm not in VALID_SYNC_KINDS:
        return (
            f"_request_sync: kind {kind!r} must be one of {sorted(VALID_SYNC_KINDS)}",
            [],
        )
    args["kind"] = kind_norm

    preset = args.get("range_preset") or args.get("preset")
    if preset is not None:
        if not isinstance(preset, str):
            return "_request_sync: range_preset must be a string", []
        preset_norm = preset.strip().lower()
        if preset_norm not in VALID_SYNC_RANGE_PRESETS:
            return (
                f"_request_sync: range_preset {preset!r} must be one of "
                f"{sorted(VALID_SYNC_RANGE_PRESETS)}",
                [],
            )
        args["range_preset"] = preset_norm
    return None, []


# ── Public entry point ────────────────────────────────────────────────

async def validate_ai_action(
    action: dict,
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str] = None,
    *,
    allow_queue_only_tools: bool = True,
) -> ValidationResult:
    """Validate one AI-proposed action.

    Args:
        action: Raw action dict from ``ai_service._parse_chat_response``.
        db: Async session scoped to the request.
        cred: Active credential.
        profile_id: Profile scope; falls back to ``cred.profile_id`` if None.
        allow_queue_only_tools: When False, ``_harvest_execute`` /
            ``_ai_campaign_create`` are rejected (use False on the inline path).

    Returns:
        :class:`ValidationResult`. When ``ok`` is True, the normalized
        ``tool`` + ``arguments`` are ready to pass to ``call_tool`` / persist.
    """
    if not isinstance(action, dict):
        return ValidationResult(ok=False, error="Action is not a dict")

    raw_tool = action.get("tool")
    raw_args = action.get("arguments") or {}
    if not isinstance(raw_tool, str) or not raw_tool.strip():
        return ValidationResult(ok=False, error="Action is missing 'tool'")

    if profile_id is None:
        profile_id = cred.profile_id

    # Gate _harvest_execute / _ai_campaign_create — never inline-applicable.
    if raw_tool.startswith("_"):
        if not allow_queue_only_tools:
            return ValidationResult(
                ok=False,
                error=f"Tool {raw_tool!r} is queue-only and cannot run inline",
                tool=raw_tool,
            )
        if raw_tool not in ALLOWED_QUEUE_TOOLS:
            return ValidationResult(ok=False, error=f"Unknown tool {raw_tool!r}", tool=raw_tool)

        args = raw_args if isinstance(raw_args, dict) else {}
        if raw_tool == "_harvest_execute":
            err, warnings = await _validate_harvest_args(args, db, cred, profile_id)
        elif raw_tool == "_ai_campaign_create":
            err, warnings = await _validate_campaign_plan(args, db, cred, profile_id)
        elif raw_tool == "_request_sync":
            err, warnings = _validate_request_sync(args)
        else:  # pragma: no cover — already filtered by allow-list
            err, warnings = None, []

        if err:
            return ValidationResult(ok=False, error=err, warnings=warnings, tool=raw_tool, arguments=args)

        normalized = dict(action)
        normalized["tool"] = raw_tool
        normalized["arguments"] = args
        return ValidationResult(
            ok=True,
            warnings=warnings,
            tool=raw_tool,
            arguments=args,
            normalized_action=normalized,
        )

    tool, arguments = normalize_mcp_call(raw_tool, raw_args)

    allowed = ALLOWED_QUEUE_TOOLS if allow_queue_only_tools else ALLOWED_INLINE_TOOLS
    if tool not in allowed:
        return ValidationResult(ok=False, error=f"Tool {tool!r} not permitted", tool=tool)

    body = arguments.get("body") if isinstance(arguments, dict) else None
    if not isinstance(body, dict):
        return ValidationResult(ok=False, error="arguments.body must be an object", tool=tool)

    err: Optional[str] = None
    warnings: list[str] = []

    if tool in ("campaign_management-update_target_bid", "campaign_management-update_target"):
        err, warnings = await _validate_target_bid_or_state(body, db, cred, profile_id)
    elif tool == "campaign_management-create_target":
        err, warnings = await _validate_create_target(body, db, cred, profile_id)
    elif tool == "campaign_management-delete_target":
        err, warnings = await _validate_delete_target(body, db, cred, profile_id)
    elif tool == "campaign_management-update_campaign_budget":
        err, warnings = await _validate_campaign_update(
            body, db, cred, profile_id, require_budget=True
        )
    elif tool == "campaign_management-update_campaign_state":
        err, warnings = await _validate_campaign_update(body, db, cred, profile_id)
    elif tool == "campaign_management-update_campaign":
        err, warnings = await _validate_campaign_update(body, db, cred, profile_id)
    elif tool == "campaign_management-update_ad_group":
        err, warnings = await _validate_ad_group_update(body, db, cred)
    elif tool == "campaign_management-update_ad":
        err, warnings = await _validate_ad_update(body, db, cred)
    elif tool == "campaign_management-delete_campaign":
        err, warnings = await _validate_delete_campaign(body, db, cred, profile_id)
    elif tool == "campaign_management-create_ad_group":
        err, warnings = await _validate_create_ad_group(body, db, cred, profile_id)
    elif tool == "campaign_management-delete_ad_group":
        err, warnings = await _validate_delete_ad_group(body, db, cred)
    elif tool == "campaign_management-create_ad":
        err, warnings = await _validate_create_ad(body, db, cred)
    elif tool == "campaign_management-delete_ad":
        err, warnings = await _validate_delete_ad(body, db, cred)
    else:  # pragma: no cover — already filtered by allow-list above
        return ValidationResult(ok=False, error=f"Tool {tool!r} has no validator", tool=tool)

    if err:
        return ValidationResult(ok=False, error=err, warnings=warnings, tool=tool, arguments=arguments)

    normalized = dict(action)
    normalized["tool"] = tool
    normalized["arguments"] = arguments
    return ValidationResult(
        ok=True,
        warnings=warnings,
        tool=tool,
        arguments=arguments,
        normalized_action=normalized,
    )


async def validate_ai_actions(
    actions: list[dict],
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str] = None,
    *,
    allow_queue_only_tools: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Validate a batch. Returns ``(accepted_normalized, rejected_with_reason)``."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    for action in actions or []:
        result = await validate_ai_action(
            action, db, cred, profile_id, allow_queue_only_tools=allow_queue_only_tools
        )
        if result.ok and result.normalized_action is not None:
            if result.warnings:
                normalized = dict(result.normalized_action)
                normalized.setdefault("validator_warnings", []).extend(result.warnings)
                accepted.append(normalized)
            else:
                accepted.append(result.normalized_action)
        else:
            rejected.append({
                "action": action,
                "error": result.error,
                "tool": result.tool,
            })
    if rejected:
        logger.warning(
            "AI action validator rejected %d/%d actions: %s",
            len(rejected),
            len(actions or []),
            [r["error"] for r in rejected],
        )
    return accepted, rejected
