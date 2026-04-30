"""
Campaign Management Router — Full CRUD for Campaigns, Ad Groups, Ads, Targets, and Ad Associations.
Maps directly to the Amazon Ads MCP Campaign Management API.
All mutations flow through the approval queue unless `skip_approval` is set.
"""

import asyncio
import logging
import uuid as uuid_mod
from datetime import datetime
from typing import Optional, Callable, Awaitable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_, case

from app.database import get_db, async_session
from app.auth import get_current_user
from app.models import (
    Account, Credential, Campaign, AdGroup, Target, Ad, AdAssociation,
    ActivityLog, PendingChange, CampaignPerformanceDaily, SearchTermPerformance,
    AppSettings, SyncJob, User,
)
from app.services.account_scope import resolve_campaign_sync_scope
from app.services.token_service import get_mcp_client_with_fresh_token
from app.services.reporting_service import (
    get_date_range,
    DATE_PRESETS,
    resolve_perf_date_source,
    apply_targeting_performance_to_db_targets,
)
from app.services.product_image_service import get_product_image_url
from app.utils import (
    parse_uuid,
    safe_error_detail,
    utcnow,
    extract_target_expression,
    extract_ad_asin_sku,
    extract_ad_display_name,
    normalize_amazon_date,
    normalize_state_value,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────

async def _get_credential(db: AsyncSession, cred_id: Optional[str] = None) -> Credential:
    if cred_id:
        result = await db.execute(select(Credential).where(Credential.id == parse_uuid(cred_id, "credential_id")))
    else:
        result = await db.execute(select(Credential).where(Credential.is_default == True))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(
            status_code=404,
            detail="No credential found. Add API credentials in Settings and discover accounts on the Dashboard.",
        )
    return cred


async def _make_client(
    cred: Credential,
    db: AsyncSession,
    profile_id_override: Optional[str] = None,
):
    return await get_mcp_client_with_fresh_token(cred, db, profile_id_override=profile_id_override)


def _extract_list(data, keys=None) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        search_keys = keys or ["campaigns", "adGroups", "ads", "targets",
                                "adAssociations", "result", "results", "items"]
        for key in search_keys:
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


def _normalized_updates_for_mcp(updates: dict) -> dict:
    """Normalize mutable payload fields before calling MCP tools."""
    if not isinstance(updates, dict):
        return {}
    out = dict(updates)
    if "state" in out:
        out["state"] = normalize_state_value(out.get("state"))
    return out


# ══════════════════════════════════════════════════════════════════════
#  CAMPAIGNS — Full CRUD
# ══════════════════════════════════════════════════════════════════════

def _perf_date_where(perf_where, mode: str, val1: str, val2: Optional[str]):
    """
    Add date filtering for campaign_performance_daily.
    Uses a single source (exact_range, single_day, or best_range) to avoid double-counting
    when overlapping range keys exist in the DB.
    """
    if mode == "exact_range" or mode == "best_range":
        perf_where.append(CampaignPerformanceDaily.date == val1)
    else:
        # single_day: only rows without __ in date
        perf_where.append(CampaignPerformanceDaily.date >= val1)
        perf_where.append(CampaignPerformanceDaily.date <= val2)
        perf_where.append(func.strpos(CampaignPerformanceDaily.date, "__") <= 0)


@router.get("")
@router.get("/")
async def list_campaigns(
    credential_id: Optional[str] = Query(None),
    state: Optional[str] = Query(None, description="Filter by state: enabled, paused, archived"),
    campaign_type: Optional[str] = Query(None, description="Filter by ad product type"),
    targeting_type: Optional[str] = Query(None, description="Filter by targeting: auto, manual"),
    search: Optional[str] = Query(None, description="Search campaign name"),
    date_from: Optional[str] = Query(None, description="Filter campaigns active from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter campaigns active until date (YYYY-MM-DD)"),
    preset: Optional[str] = Query(None, description="Date preset (today, this_month, etc.) — used for performance metrics when date_from/date_to not set"),
    sort_by: Optional[str] = Query("campaign_name", description="Sort by: campaign_name, campaign_type, state, daily_budget, spend, sales, acos"),
    sort_dir: Optional[str] = Query("asc", description="Sort direction: asc or desc"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """List cached campaigns with optional filters, sorting, and pagination."""
    cred = await _get_credential(db, credential_id)

    # Resolve performance date range (for spend/sales/acos): preset or explicit dates, default this_month
    perf_start, perf_end = None, None
    if date_from and date_to:
        perf_start, perf_end = date_from, date_to
    elif preset and preset in DATE_PRESETS:
        s, e = get_date_range(preset)
        perf_start, perf_end = s.isoformat(), e.isoformat()
    else:
        s, e = get_date_range("this_month")
        perf_start, perf_end = s.isoformat(), e.isoformat()

    # Resolve single date source to avoid double-counting overlapping range keys
    perf_mode, perf_val1, perf_val2 = await resolve_perf_date_source(
        db, cred.id, perf_start, perf_end, cred.profile_id
    )

    base_query = select(Campaign).where(Campaign.credential_id == cred.id)
    if cred.profile_id is not None:
        base_query = base_query.where(Campaign.profile_id == cred.profile_id)
    else:
        base_query = base_query.where(Campaign.profile_id.is_(None))
    if state:
        base_query = base_query.where(func.lower(Campaign.state) == state.lower())
    if campaign_type:
        base_query = base_query.where(func.lower(Campaign.campaign_type) == campaign_type.lower())
    if targeting_type:
        base_query = base_query.where(func.lower(Campaign.targeting_type) == targeting_type.lower())
    if search:
        base_query = base_query.where(Campaign.campaign_name.ilike(f"%{search}%"))

    # Date range filter: campaigns that overlap with [date_from, date_to] or [perf_start, perf_end]
    # Campaign overlaps if: start_date <= date_to AND (end_date is null OR end_date >= date_from)
    camp_date_from = date_from or perf_start
    camp_date_to = date_to or perf_end
    if camp_date_from or camp_date_to:
        if camp_date_from and camp_date_to:
            base_query = base_query.where(
                and_(
                    or_(Campaign.start_date.is_(None), Campaign.start_date <= camp_date_to),
                    or_(Campaign.end_date.is_(None), Campaign.end_date >= camp_date_from),
                )
            )
        elif camp_date_from:
            base_query = base_query.where(
                or_(Campaign.end_date.is_(None), Campaign.end_date >= camp_date_from)
            )
        else:
            base_query = base_query.where(
                or_(Campaign.start_date.is_(None), Campaign.start_date <= camp_date_to)
            )

    # Total count
    count_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
    total = count_result.scalar() or 0

    # Sorting — for spend/sales/acos use aggregated perf; otherwise use Campaign columns
    sort_col = (sort_by or "campaign_name").lower()
    sort_asc = (sort_dir or "asc").lower() != "desc"
    offset = (page - 1) * page_size

    if sort_col in ("spend", "sales", "acos"):
        # Subquery for aggregated performance
        perf_where = [CampaignPerformanceDaily.credential_id == cred.id]
        if cred.profile_id is not None:
            perf_where.append(CampaignPerformanceDaily.profile_id == cred.profile_id)
        else:
            perf_where.append(CampaignPerformanceDaily.profile_id.is_(None))
        _perf_date_where(perf_where, perf_mode, perf_val1, perf_val2)
        perf_subq = (
            select(
                CampaignPerformanceDaily.amazon_campaign_id,
                func.sum(CampaignPerformanceDaily.spend).label("agg_spend"),
                func.sum(CampaignPerformanceDaily.sales).label("agg_sales"),
            )
            .where(*perf_where)
            .group_by(CampaignPerformanceDaily.amazon_campaign_id)
        ).subquery()
        base_query = base_query.outerjoin(perf_subq, Campaign.amazon_campaign_id == perf_subq.c.amazon_campaign_id)
        if sort_col == "spend":
            order_col = func.coalesce(perf_subq.c.agg_spend, Campaign.spend, 0)
        elif sort_col == "sales":
            order_col = func.coalesce(perf_subq.c.agg_sales, Campaign.sales, 0)
        else:  # acos — use spend/sales ratio; when sales=0 treat as high acos (999)
            agg_s = func.coalesce(perf_subq.c.agg_sales, Campaign.sales, 0)
            agg_c = func.coalesce(perf_subq.c.agg_spend, Campaign.spend, 0)
            order_col = case((agg_s > 0, agg_c / agg_s * 100), else_=999.0)
        query = base_query.order_by(order_col.asc() if sort_asc else order_col.desc()).offset(offset).limit(page_size)
    else:
        # Direct Campaign column sort
        if sort_col == "campaign_name":
            order_col = Campaign.campaign_name
        elif sort_col == "campaign_type":
            order_col = Campaign.campaign_type
        elif sort_col == "state":
            order_col = Campaign.state
        elif sort_col == "daily_budget":
            order_col = Campaign.daily_budget
        else:
            order_col = Campaign.campaign_name
        query = base_query.order_by(order_col.asc() if sort_asc else order_col.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    campaigns = result.scalars().all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    # Enrich with performance data from campaign_performance_daily when Campaign table has no spend
    campaign_ids = [c.amazon_campaign_id for c in campaigns]
    perf_by_campaign: dict = {}
    if campaign_ids:
        perf_where = [
            CampaignPerformanceDaily.credential_id == cred.id,
            CampaignPerformanceDaily.amazon_campaign_id.in_(campaign_ids),
        ]
        if cred.profile_id is not None:
            perf_where.append(CampaignPerformanceDaily.profile_id == cred.profile_id)
        else:
            perf_where.append(CampaignPerformanceDaily.profile_id.is_(None))
        _perf_date_where(perf_where, perf_mode, perf_val1, perf_val2)
        perf_result = await db.execute(
            select(
                CampaignPerformanceDaily.amazon_campaign_id,
                func.sum(CampaignPerformanceDaily.spend).label("spend"),
                func.sum(CampaignPerformanceDaily.sales).label("sales"),
                func.sum(CampaignPerformanceDaily.impressions).label("impressions"),
                func.sum(CampaignPerformanceDaily.clicks).label("clicks"),
                func.sum(CampaignPerformanceDaily.orders).label("orders"),
            )
            .where(*perf_where)
            .group_by(CampaignPerformanceDaily.amazon_campaign_id)
        )
        for row in perf_result.all():
            spend = float(row.spend or 0)
            sales = float(row.sales or 0)
            acos = round(spend / sales * 100, 2) if sales > 0 else None
            perf_by_campaign[row.amazon_campaign_id] = {
                "spend": spend,
                "sales": sales,
                "acos": acos,
                "impressions": int(row.impressions or 0),
                "clicks": int(row.clicks or 0),
                "orders": int(row.orders or 0),
            }

    def _get_metrics(c):
        perf = perf_by_campaign.get(c.amazon_campaign_id, {})
        if (c.spend or 0) > 0 or (c.sales or 0) > 0:
            spend, sales = float(c.spend or 0), float(c.sales or 0)
            acos = round(spend / sales * 100, 2) if sales > 0 else c.acos
            roas = round(sales / spend, 2) if spend > 0 else c.roas
            return spend, sales, c.impressions or 0, c.clicks or 0, c.orders or 0, acos, roas
        spend = perf.get("spend", 0) or 0
        sales = perf.get("sales", 0) or 0
        acos = perf.get("acos") if sales > 0 else None
        roas = round(sales / spend, 2) if spend > 0 else None
        return spend, sales, perf.get("impressions", 0), perf.get("clicks", 0), perf.get("orders", 0), acos, roas

    campaign_list = []
    for c in campaigns:
        m = _get_metrics(c)
        campaign_list.append({
            "id": str(c.id),
            "amazon_campaign_id": c.amazon_campaign_id,
            "campaign_name": c.campaign_name,
            "campaign_type": c.campaign_type,
            "targeting_type": c.targeting_type,
            "state": c.state,
            "daily_budget": c.daily_budget,
            "start_date": c.start_date,
            "end_date": c.end_date,
            "spend": m[0],
            "sales": m[1],
            "impressions": m[2],
            "clicks": m[3],
            "orders": m[4],
            "acos": m[5],
            "roas": m[6],
            "synced_at": c.synced_at.isoformat() if c.synced_at else None,
        })

    return {
        "campaigns": campaign_list,
        "count": len(campaigns),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "date_range": {"start": perf_start, "end": perf_end},
    }


class CampaignCreateRequest(BaseModel):
    campaign_data: dict = Field(..., description="Campaign payload for MCP create_campaign")
    skip_approval: bool = False


@router.post("/")
async def create_campaign(
    req: CampaignCreateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a new campaign. Routed through approval queue by default."""
    cred = await _get_credential(db, credential_id)

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.create_campaign([req.campaign_data])
            db.add(ActivityLog(
                credential_id=cred.id, action="campaign_created",
                category="campaigns",
                description=f"Created campaign: {req.campaign_data.get('name', 'Unknown')}",
                entity_type="campaign", details={"result": result},
            ))
            await db.flush()
            return {"status": "created", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="campaign_create",
            entity_type="campaign",
            entity_name=req.campaign_data.get("name", "New Campaign"),
            proposed_value=str(req.campaign_data),
            change_detail=req.campaign_data,
            mcp_payload={"tool": "campaign_management-create_campaign", "arguments": {"body": {"campaigns": [req.campaign_data]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


class AddCountryRequest(BaseModel):
    campaign_id: str = Field(..., description="Amazon campaign ID (SP Manual only)")
    countries: list[dict] = Field(
        ...,
        description="List of {countryCode, dailyBudget} for each country to add",
        min_length=1,
    )
    skip_approval: bool = False


class SingleshotCampaignRequest(BaseModel):
    campaign_name: str = Field(..., description="Campaign name")
    country_budgets: list[dict] = Field(
        ...,
        description="List of {countryCode, dailyBudget} per marketplace",
        min_length=1,
    )
    asins_by_country: dict = Field(
        default_factory=dict,
        description="Map of countryCode -> list of ASINs (e.g. {\"US\": [\"B08N5WRWNW\"], \"GB\": [\"B08N5WRWNW\"]})",
    )
    skip_approval: bool = False


class CampaignUpdateRequest(BaseModel):
    amazon_campaign_id: str
    updates: dict = Field(..., description="Fields to update (name, state, budget, etc.)")
    skip_approval: bool = False


@router.post("/add-country")
async def add_country_to_campaign(
    req: AddCountryRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Add countries to an existing SP Manual campaign with country-specific budget caps."""
    cred = await _get_credential(db, credential_id)
    campaign_payload = {"campaignId": req.campaign_id, "countryBudgets": req.countries}
    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.add_country_campaign([campaign_payload])
            db.add(ActivityLog(
                credential_id=cred.id, action="campaign_country_added",
                category="campaigns", description=f"Added countries to campaign {req.campaign_id}",
                entity_type="campaign", entity_id=req.campaign_id, details={"countries": req.countries, "result": result},
            ))
            await db.flush()
            return {"status": "updated", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Add country failed."))
    existing = await db.execute(select(Campaign).where(Campaign.credential_id == cred.id, Campaign.amazon_campaign_id == req.campaign_id))
    campaign = existing.scalar_one_or_none()
    change = PendingChange(
        credential_id=cred.id, profile_id=cred.profile_id, change_type="campaign_add_country",
        entity_type="campaign", entity_id=req.campaign_id, entity_name=campaign.campaign_name if campaign else req.campaign_id,
        campaign_id=req.campaign_id, proposed_value=str(req.countries), change_detail={"countries": req.countries},
        mcp_payload={"tool": "campaign_management-add_country_campaign", "arguments": {"body": {"campaigns": [campaign_payload]}}},
        source="manual",
    )
    db.add(change)
    await db.flush()
    return {"status": "pending_approval", "change_id": str(change.id)}


@router.post("/singleshot")
async def create_singleshot_campaign(
    req: SingleshotCampaignRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a complete SP AUTO campaign across multiple marketplaces in one operation."""
    cred = await _get_credential(db, credential_id)
    oneshot = {"name": req.campaign_name, "countryBudgets": req.country_budgets, "asinsByCountry": req.asins_by_country or {}}
    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.create_singleshot_campaign([oneshot])
            db.add(ActivityLog(
                credential_id=cred.id, action="singleshot_campaign_created",
                category="campaigns", description=f"Created singleshot campaign: {req.campaign_name}",
                entity_type="campaign", details={"result": result},
            ))
            await db.flush()
            return {"status": "created", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Singleshot campaign creation failed."))
    change = PendingChange(
        credential_id=cred.id, profile_id=cred.profile_id, change_type="campaign_create",
        entity_type="campaign", entity_name=req.campaign_name, proposed_value=str(oneshot), change_detail=oneshot,
        mcp_payload={"tool": "campaign_management-create_singleshot_sp_campaign", "arguments": {"body": {"oneshotCampaigns": [oneshot]}}},
        source="manual",
    )
    db.add(change)
    await db.flush()
    return {"status": "pending_approval", "change_id": str(change.id)}


@router.put("/{amazon_campaign_id}")
async def update_campaign(
    amazon_campaign_id: str,
    req: CampaignUpdateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a campaign. Routed through approval queue by default."""
    cred = await _get_credential(db, credential_id)
    normalized_updates = _normalized_updates_for_mcp(req.updates)
    payload = {"campaignId": amazon_campaign_id, **normalized_updates}

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.update_campaign([payload])
            # Update local cache
            existing = await db.execute(
                select(Campaign).where(
                    Campaign.credential_id == cred.id,
                    Campaign.amazon_campaign_id == amazon_campaign_id,
                )
            )
            campaign = existing.scalar_one_or_none()
            if campaign:
                if "name" in normalized_updates:
                    campaign.campaign_name = normalized_updates["name"]
                if "state" in normalized_updates:
                    campaign.state = normalize_state_value(normalized_updates["state"], for_storage=True)
                if "dailyBudget" in normalized_updates:
                    campaign.daily_budget = float(normalized_updates["dailyBudget"])
                campaign.synced_at = utcnow()

            db.add(ActivityLog(
                credential_id=cred.id, action="campaign_updated",
                category="campaigns",
                description=f"Updated campaign {amazon_campaign_id}",
                entity_type="campaign", entity_id=amazon_campaign_id,
                details={"updates": normalized_updates, "result": result},
            ))
            await db.flush()
            return {"status": "updated", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        # Look up the campaign name from cache
        existing = await db.execute(
            select(Campaign).where(
                Campaign.credential_id == cred.id,
                Campaign.amazon_campaign_id == amazon_campaign_id,
            )
        )
        campaign = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="campaign_update",
            entity_type="campaign",
            entity_id=amazon_campaign_id,
            entity_name=campaign.campaign_name if campaign else amazon_campaign_id,
            campaign_id=amazon_campaign_id,
            campaign_name=campaign.campaign_name if campaign else None,
            current_value=str(campaign.raw_data) if campaign else None,
            proposed_value=str(normalized_updates),
            change_detail=normalized_updates,
            mcp_payload={"tool": "campaign_management-update_campaign", "arguments": {"body": {"campaigns": [payload]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


class StateUpdateRequest(BaseModel):
    amazon_campaign_id: str
    state: str = Field(..., description="enabled, paused, or archived")
    skip_approval: bool = False


@router.post("/{amazon_campaign_id}/state")
async def update_campaign_state(
    amazon_campaign_id: str,
    req: StateUpdateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Quick campaign state change (enable/pause/archive)."""
    cred = await _get_credential(db, credential_id)
    payload = [{"campaignId": amazon_campaign_id, "state": req.state.upper()}]

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.update_campaign_state(payload)
            # Update local cache
            existing = await db.execute(
                select(Campaign).where(
                    Campaign.credential_id == cred.id,
                    Campaign.amazon_campaign_id == amazon_campaign_id,
                )
            )
            campaign = existing.scalar_one_or_none()
            if campaign:
                campaign.state = req.state
                campaign.synced_at = utcnow()

            db.add(ActivityLog(
                credential_id=cred.id, action="campaign_state_changed",
                category="campaigns",
                description=f"Campaign {amazon_campaign_id} state → {req.state}",
                entity_type="campaign", entity_id=amazon_campaign_id,
            ))
            await db.flush()
            return {"status": "updated", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(Campaign).where(
                Campaign.credential_id == cred.id,
                Campaign.amazon_campaign_id == amazon_campaign_id,
            )
        )
        campaign = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="campaign_state",
            entity_type="campaign",
            entity_id=amazon_campaign_id,
            entity_name=campaign.campaign_name if campaign else amazon_campaign_id,
            campaign_id=amazon_campaign_id,
            campaign_name=campaign.campaign_name if campaign else None,
            current_value=campaign.state if campaign else None,
            proposed_value=req.state,
            mcp_payload={"tool": "campaign_management-update_campaign_state", "arguments": {"body": {"campaigns": payload}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


class BudgetUpdateRequest(BaseModel):
    amazon_campaign_id: str
    daily_budget: float
    skip_approval: bool = False


@router.post("/{amazon_campaign_id}/budget")
async def update_campaign_budget(
    amazon_campaign_id: str,
    req: BudgetUpdateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Quick campaign budget update."""
    cred = await _get_credential(db, credential_id)
    payload = [{"campaignId": amazon_campaign_id, "dailyBudget": req.daily_budget}]

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.update_campaign_budget(payload)
            existing = await db.execute(
                select(Campaign).where(
                    Campaign.credential_id == cred.id,
                    Campaign.amazon_campaign_id == amazon_campaign_id,
                )
            )
            campaign = existing.scalar_one_or_none()
            if campaign:
                campaign.daily_budget = req.daily_budget
                campaign.synced_at = utcnow()

            db.add(ActivityLog(
                credential_id=cred.id, action="campaign_budget_changed",
                category="campaigns",
                description=f"Campaign {amazon_campaign_id} budget → ${req.daily_budget}",
                entity_type="campaign", entity_id=amazon_campaign_id,
            ))
            await db.flush()
            return {"status": "updated", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(Campaign).where(
                Campaign.credential_id == cred.id,
                Campaign.amazon_campaign_id == amazon_campaign_id,
            )
        )
        campaign = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="budget_update",
            entity_type="campaign",
            entity_id=amazon_campaign_id,
            entity_name=campaign.campaign_name if campaign else amazon_campaign_id,
            campaign_id=amazon_campaign_id,
            campaign_name=campaign.campaign_name if campaign else None,
            current_value=str(campaign.daily_budget) if campaign else None,
            proposed_value=str(req.daily_budget),
            mcp_payload={"tool": "campaign_management-update_campaign_budget", "arguments": {"body": {"campaigns": payload}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


@router.delete("/{amazon_campaign_id}")
async def delete_campaign(
    amazon_campaign_id: str,
    credential_id: Optional[str] = Query(None),
    skip_approval: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Delete a campaign."""
    cred = await _get_credential(db, credential_id)

    if skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.delete_campaign([amazon_campaign_id])
            db.add(ActivityLog(
                credential_id=cred.id, action="campaign_deleted",
                category="campaigns",
                description=f"Deleted campaign {amazon_campaign_id}",
                entity_type="campaign", entity_id=amazon_campaign_id,
            ))
            await db.flush()
            return {"status": "deleted", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(Campaign).where(
                Campaign.credential_id == cred.id,
                Campaign.amazon_campaign_id == amazon_campaign_id,
            )
        )
        campaign = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="campaign_delete",
            entity_type="campaign",
            entity_id=amazon_campaign_id,
            entity_name=campaign.campaign_name if campaign else amazon_campaign_id,
            campaign_id=amazon_campaign_id,
            campaign_name=campaign.campaign_name if campaign else None,
            current_value=campaign.campaign_name if campaign else amazon_campaign_id,
            proposed_value="DELETE",
            mcp_payload={"tool": "campaign_management-delete_campaign", "arguments": {"body": {"campaignIds": [amazon_campaign_id]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


# ══════════════════════════════════════════════════════════════════════
#  AD GROUPS — Full CRUD
# ══════════════════════════════════════════════════════════════════════

@router.get("/{amazon_campaign_id}/ad-groups")
async def list_ad_groups(
    amazon_campaign_id: str,
    credential_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="Filter performance by date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter performance by date (YYYY-MM-DD)"),
    preset: Optional[str] = Query(None, description="Date preset when date_from/date_to not set"),
    db: AsyncSession = Depends(get_db),
):
    """List ad groups for a specific campaign, enriched with performance from SearchTermPerformance."""
    cred = await _get_credential(db, credential_id)

    # Resolve date range for SearchTermPerformance filter
    st_start, st_end = None, None
    if date_from and date_to:
        st_start, st_end = date_from, date_to
    elif preset and preset in DATE_PRESETS:
        s, e = get_date_range(preset)
        st_start, st_end = s.isoformat(), e.isoformat()
    else:
        s, e = get_date_range("this_month")
        st_start, st_end = s.isoformat(), e.isoformat()

    query = (
        select(AdGroup)
        .where(AdGroup.credential_id == cred.id, AdGroup.amazon_campaign_id == amazon_campaign_id)
        .order_by(AdGroup.ad_group_name)
    )
    result = await db.execute(query)
    ad_groups = result.scalars().all()
    ag_ids = [g.amazon_ad_group_id for g in ad_groups if g.amazon_ad_group_id]

    perf_by_ag: dict = {}
    if ag_ids:
        st_where = [
            SearchTermPerformance.credential_id == cred.id,
            SearchTermPerformance.amazon_ad_group_id.in_(ag_ids),
            SearchTermPerformance.report_date_start <= st_end,
            SearchTermPerformance.report_date_end >= st_start,
        ]
        if cred.profile_id is not None:
            st_where.append(SearchTermPerformance.profile_id == cred.profile_id)
        else:
            st_where.append(SearchTermPerformance.profile_id.is_(None))
        perf_result = await db.execute(
            select(
                SearchTermPerformance.amazon_ad_group_id,
                func.sum(SearchTermPerformance.cost).label("spend"),
                func.sum(SearchTermPerformance.sales).label("sales"),
                func.sum(SearchTermPerformance.clicks).label("clicks"),
                func.sum(SearchTermPerformance.impressions).label("impressions"),
                func.sum(SearchTermPerformance.purchases).label("orders"),
            )
            .where(
                *st_where,
            )
            .group_by(SearchTermPerformance.amazon_ad_group_id)
        )
        for row in perf_result.all():
            spend = float(row.spend or 0)
            sales = float(row.sales or 0)
            acos = round(spend / sales * 100, 2) if sales > 0 else None
            perf_by_ag[row.amazon_ad_group_id] = {
                "spend": spend, "sales": sales, "acos": acos,
                "clicks": int(row.clicks or 0), "impressions": int(row.impressions or 0), "orders": int(row.orders or 0),
            }

    return {
        "ad_groups": [
            {
                "id": str(g.id),
                "amazon_ad_group_id": g.amazon_ad_group_id,
                "amazon_campaign_id": g.amazon_campaign_id,
                "ad_group_name": g.ad_group_name,
                "state": g.state,
                "default_bid": g.default_bid,
                "spend": perf_by_ag.get(g.amazon_ad_group_id, {}).get("spend", 0),
                "sales": perf_by_ag.get(g.amazon_ad_group_id, {}).get("sales", 0),
                "clicks": perf_by_ag.get(g.amazon_ad_group_id, {}).get("clicks", 0),
                "acos": perf_by_ag.get(g.amazon_ad_group_id, {}).get("acos"),
                "synced_at": g.synced_at.isoformat() if g.synced_at else None,
            }
            for g in ad_groups
        ],
        "count": len(ad_groups),
    }


class AdGroupCreateRequest(BaseModel):
    ad_group_data: dict
    skip_approval: bool = False


@router.post("/{amazon_campaign_id}/ad-groups")
async def create_ad_group(
    amazon_campaign_id: str,
    req: AdGroupCreateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Create an ad group within a campaign."""
    cred = await _get_credential(db, credential_id)
    payload = {"campaignId": amazon_campaign_id, **req.ad_group_data}

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.create_ad_group([payload])
            db.add(ActivityLog(
                credential_id=cred.id, action="ad_group_created",
                category="campaigns",
                description=f"Created ad group: {req.ad_group_data.get('name', 'Unknown')}",
                entity_type="ad_group", details={"result": result},
            ))
            await db.flush()
            return {"status": "created", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="ad_group_create",
            entity_type="ad_group",
            entity_name=req.ad_group_data.get("name", "New Ad Group"),
            campaign_id=amazon_campaign_id,
            proposed_value=str(req.ad_group_data),
            change_detail=req.ad_group_data,
            mcp_payload={"tool": "campaign_management-create_ad_group", "arguments": {"body": {"adGroups": [payload]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


class AdGroupUpdateRequest(BaseModel):
    updates: dict
    skip_approval: bool = False


@router.put("/ad-groups/{amazon_ad_group_id}")
async def update_ad_group(
    amazon_ad_group_id: str,
    req: AdGroupUpdateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Update an ad group (name, state, default bid)."""
    cred = await _get_credential(db, credential_id)
    normalized_updates = _normalized_updates_for_mcp(req.updates)
    payload = {"adGroupId": amazon_ad_group_id, **normalized_updates}

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.update_ad_group([payload])
            existing = await db.execute(
                select(AdGroup).where(
                    AdGroup.credential_id == cred.id,
                    AdGroup.amazon_ad_group_id == amazon_ad_group_id,
                )
            )
            ag = existing.scalar_one_or_none()
            if ag:
                if "name" in normalized_updates:
                    ag.ad_group_name = normalized_updates["name"]
                if "state" in normalized_updates:
                    ag.state = normalize_state_value(normalized_updates["state"], for_storage=True)
                if "defaultBid" in normalized_updates:
                    ag.default_bid = float(normalized_updates["defaultBid"])
                ag.synced_at = utcnow()

            db.add(ActivityLog(
                credential_id=cred.id, action="ad_group_updated",
                category="campaigns",
                description=f"Updated ad group {amazon_ad_group_id}",
                entity_type="ad_group", entity_id=amazon_ad_group_id,
            ))
            await db.flush()
            return {"status": "updated", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(AdGroup).where(
                AdGroup.credential_id == cred.id,
                AdGroup.amazon_ad_group_id == amazon_ad_group_id,
            )
        )
        ag = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="ad_group_update",
            entity_type="ad_group",
            entity_id=amazon_ad_group_id,
            entity_name=ag.ad_group_name if ag else amazon_ad_group_id,
            campaign_id=ag.amazon_campaign_id if ag else None,
            current_value=str(ag.raw_data) if ag else None,
            proposed_value=str(normalized_updates),
            change_detail=normalized_updates,
            mcp_payload={"tool": "campaign_management-update_ad_group", "arguments": {"body": {"adGroups": [payload]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


@router.delete("/ad-groups/{amazon_ad_group_id}")
async def delete_ad_group(
    amazon_ad_group_id: str,
    credential_id: Optional[str] = Query(None),
    skip_approval: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Delete an ad group."""
    cred = await _get_credential(db, credential_id)

    if skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.delete_ad_group([amazon_ad_group_id])
            db.add(ActivityLog(
                credential_id=cred.id, action="ad_group_deleted",
                category="campaigns",
                description=f"Deleted ad group {amazon_ad_group_id}",
                entity_type="ad_group", entity_id=amazon_ad_group_id,
            ))
            await db.flush()
            return {"status": "deleted", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(AdGroup).where(
                AdGroup.credential_id == cred.id,
                AdGroup.amazon_ad_group_id == amazon_ad_group_id,
            )
        )
        ag = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="ad_group_delete",
            entity_type="ad_group",
            entity_id=amazon_ad_group_id,
            entity_name=ag.ad_group_name if ag else amazon_ad_group_id,
            campaign_id=ag.amazon_campaign_id if ag else None,
            current_value=ag.ad_group_name if ag else amazon_ad_group_id,
            proposed_value="DELETE",
            mcp_payload={"tool": "campaign_management-delete_ad_group", "arguments": {"body": {"adGroupIds": [amazon_ad_group_id]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


# ══════════════════════════════════════════════════════════════════════
#  TARGETS — Full CRUD
# ══════════════════════════════════════════════════════════════════════

@router.get("/ad-groups/{amazon_ad_group_id}/targets")
async def list_targets(
    amazon_ad_group_id: str,
    credential_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="Filter performance by date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter performance by date (YYYY-MM-DD)"),
    preset: Optional[str] = Query(None, description="Date preset when date_from/date_to not set"),
    sort_by: Optional[str] = Query("expression_value", description="Sort by: expression_value, match_type, state, bid, clicks, spend, sales, acos"),
    sort_dir: Optional[str] = Query("asc", description="Sort direction: asc or desc"),
    db: AsyncSession = Depends(get_db),
):
    """List targets/keywords for an ad group, enriched with performance from SearchTermPerformance (keyword_id = amazon_target_id)."""
    cred = await _get_credential(db, credential_id)

    st_start, st_end = None, None
    if date_from and date_to:
        st_start, st_end = date_from, date_to
    elif preset and preset in DATE_PRESETS:
        s, e = get_date_range(preset)
        st_start, st_end = s.isoformat(), e.isoformat()
    else:
        s, e = get_date_range("this_month")
        st_start, st_end = s.isoformat(), e.isoformat()

    query = (
        select(Target)
        .where(Target.credential_id == cred.id, Target.amazon_ad_group_id == amazon_ad_group_id)
        .order_by(Target.expression_value)
    )
    result = await db.execute(query)
    targets = result.scalars().all()
    target_ids = [t.amazon_target_id for t in targets if t.amazon_target_id]

    perf_by_target: dict = {}
    if target_ids:
        st_where = [
            SearchTermPerformance.credential_id == cred.id,
            SearchTermPerformance.amazon_ad_group_id == amazon_ad_group_id,
            SearchTermPerformance.keyword_id.in_(target_ids),
            SearchTermPerformance.report_date_start <= st_end,
            SearchTermPerformance.report_date_end >= st_start,
        ]
        if cred.profile_id is not None:
            st_where.append(SearchTermPerformance.profile_id == cred.profile_id)
        else:
            st_where.append(SearchTermPerformance.profile_id.is_(None))
        perf_result = await db.execute(
            select(
                SearchTermPerformance.keyword_id,
                func.sum(SearchTermPerformance.cost).label("spend"),
                func.sum(SearchTermPerformance.sales).label("sales"),
                func.sum(SearchTermPerformance.clicks).label("clicks"),
                func.sum(SearchTermPerformance.impressions).label("impressions"),
                func.sum(SearchTermPerformance.purchases).label("orders"),
            )
            .where(
                *st_where,
            )
            .group_by(SearchTermPerformance.keyword_id)
        )
        for row in perf_result.all():
            if row.keyword_id:
                spend = float(row.spend or 0)
                sales = float(row.sales or 0)
                acos = round(spend / sales * 100, 2) if sales > 0 else None
                perf_by_target[row.keyword_id] = {
                    "spend": spend, "sales": sales, "acos": acos,
                    "clicks": int(row.clicks or 0), "impressions": int(row.impressions or 0), "orders": int(row.orders or 0),
                }

    target_list = []
    for t in targets:
        # Fallback: extract expression from raw_data if missing (e.g. pre-fix synced targets)
        expr_val = t.expression_value
        if not expr_val and getattr(t, "raw_data", None):
            expr_val = extract_target_expression(t.raw_data)
        perf = perf_by_target.get(t.amazon_target_id, {})
        spend = perf.get("spend") if perf else (t.spend or 0)
        sales = perf.get("sales") if perf else (t.sales or 0)
        clicks = perf.get("clicks") if perf else (t.clicks or 0)
        impressions = perf.get("impressions") if perf else (t.impressions or 0)
        acos = perf.get("acos") if perf else t.acos
        if acos is None and sales and sales > 0:
            acos = round((spend or 0) / sales * 100, 2)
        target_list.append({
            "id": str(t.id),
            "amazon_target_id": t.amazon_target_id,
            "amazon_ad_group_id": t.amazon_ad_group_id,
            "amazon_campaign_id": t.amazon_campaign_id,
            "target_type": t.target_type,
            "expression_type": t.expression_type,
            "expression_value": expr_val,
            "match_type": t.match_type,
            "state": t.state,
            "bid": t.bid,
            "clicks": clicks,
            "impressions": impressions,
            "spend": spend,
            "sales": sales,
            "acos": acos,
            "synced_at": t.synced_at.isoformat() if t.synced_at else None,
        })

    sort_col = (sort_by or "expression_value").lower()
    reverse = (sort_dir or "asc").lower() == "desc"

    def _num(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    def _target_sort_key(item: dict):
        if sort_col == "match_type":
            return (item.get("match_type") or "").lower()
        if sort_col == "state":
            return (item.get("state") or "").lower()
        if sort_col == "bid":
            return _num(item.get("bid"))
        if sort_col == "clicks":
            return int(item.get("clicks") or 0)
        if sort_col == "spend":
            return _num(item.get("spend"))
        if sort_col == "sales":
            return _num(item.get("sales"))
        if sort_col == "acos":
            val = item.get("acos")
            return _num(val) if val is not None else -1.0
        return (item.get("expression_value") or "").lower()

    target_list.sort(key=_target_sort_key, reverse=reverse)
    return {"targets": target_list, "count": len(targets)}


class TargetCreateRequest(BaseModel):
    target_data: dict
    skip_approval: bool = False


@router.post("/ad-groups/{amazon_ad_group_id}/targets")
async def create_target(
    amazon_ad_group_id: str,
    req: TargetCreateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a target/keyword in an ad group."""
    cred = await _get_credential(db, credential_id)
    payload = {"adGroupId": amazon_ad_group_id, **req.target_data}

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.create_target([payload])
            db.add(ActivityLog(
                credential_id=cred.id, action="target_created",
                category="campaigns",
                description=f"Created target in ad group {amazon_ad_group_id}",
                entity_type="target", details={"result": result},
            ))
            await db.flush()
            return {"status": "created", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="target_create",
            entity_type="target",
            entity_name=req.target_data.get("keyword") or req.target_data.get("expression", "New Target"),
            proposed_value=str(req.target_data),
            change_detail=req.target_data,
            mcp_payload={"tool": "campaign_management-create_target", "arguments": {"body": {"targets": [payload]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


class TargetUpdateRequest(BaseModel):
    updates: dict
    skip_approval: bool = False


@router.put("/targets/{amazon_target_id}")
async def update_target(
    amazon_target_id: str,
    req: TargetUpdateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a target (bid, state, etc.)."""
    cred = await _get_credential(db, credential_id)
    normalized_updates = _normalized_updates_for_mcp(req.updates)
    payload = {"targetId": amazon_target_id, **normalized_updates}

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.update_target([payload])
            existing = await db.execute(
                select(Target).where(
                    Target.credential_id == cred.id,
                    Target.amazon_target_id == amazon_target_id,
                )
            )
            target = existing.scalar_one_or_none()
            if target:
                if "bid" in normalized_updates:
                    target.bid = float(normalized_updates["bid"])
                if "state" in normalized_updates:
                    target.state = normalize_state_value(normalized_updates["state"], for_storage=True)
                target.synced_at = utcnow()

            db.add(ActivityLog(
                credential_id=cred.id, action="target_updated",
                category="campaigns",
                description=f"Updated target {amazon_target_id}",
                entity_type="target", entity_id=amazon_target_id,
            ))
            await db.flush()
            return {"status": "updated", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(Target).where(
                Target.credential_id == cred.id,
                Target.amazon_target_id == amazon_target_id,
            )
        )
        target = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="target_update",
            entity_type="target",
            entity_id=amazon_target_id,
            entity_name=target.expression_value if target else amazon_target_id,
            campaign_id=target.amazon_campaign_id if target else None,
            current_value=str(target.bid) if target else None,
            proposed_value=str(normalized_updates),
            change_detail=normalized_updates,
            mcp_payload={"tool": "campaign_management-update_target", "arguments": {"body": {"targets": [payload]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


@router.delete("/targets/{amazon_target_id}")
async def delete_target(
    amazon_target_id: str,
    credential_id: Optional[str] = Query(None),
    skip_approval: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Delete a target."""
    cred = await _get_credential(db, credential_id)

    if skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.delete_target([amazon_target_id])
            db.add(ActivityLog(
                credential_id=cred.id, action="target_deleted",
                category="campaigns",
                description=f"Deleted target {amazon_target_id}",
                entity_type="target", entity_id=amazon_target_id,
            ))
            await db.flush()
            return {"status": "deleted", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(Target).where(
                Target.credential_id == cred.id,
                Target.amazon_target_id == amazon_target_id,
            )
        )
        target = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="target_delete",
            entity_type="target",
            entity_id=amazon_target_id,
            entity_name=target.expression_value if target else amazon_target_id,
            campaign_id=target.amazon_campaign_id if target else None,
            current_value=target.expression_value if target else amazon_target_id,
            proposed_value="DELETE",
            mcp_payload={"tool": "campaign_management-delete_target", "arguments": {"body": {"targetIds": [amazon_target_id]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


# ══════════════════════════════════════════════════════════════════════
#  ADS — Full CRUD
# ══════════════════════════════════════════════════════════════════════

@router.get("/ad-groups/{amazon_ad_group_id}/ads")
async def list_ads(
    amazon_ad_group_id: str,
    credential_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="Filter performance by date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter performance by date (YYYY-MM-DD)"),
    preset: Optional[str] = Query(None, description="Date preset when date_from/date_to not set"),
    db: AsyncSession = Depends(get_db),
):
    """List ads for an ad group. Includes ad_group_metrics (spend, sales, acos) from SearchTermPerformance for context."""
    cred = await _get_credential(db, credential_id)

    st_start, st_end = None, None
    if date_from and date_to:
        st_start, st_end = date_from, date_to
    elif preset and preset in DATE_PRESETS:
        s, e = get_date_range(preset)
        st_start, st_end = s.isoformat(), e.isoformat()
    else:
        s, e = get_date_range("this_month")
        st_start, st_end = s.isoformat(), e.isoformat()

    query = (
        select(Ad)
        .where(Ad.credential_id == cred.id, Ad.amazon_ad_group_id == amazon_ad_group_id)
        .order_by(Ad.ad_name)
    )
    result = await db.execute(query)
    ads = result.scalars().all()

    # Ad group metrics from SearchTermPerformance (for single-ad groups, this is effectively the ad's performance)
    ad_group_metrics = None
    st_where = [
        SearchTermPerformance.credential_id == cred.id,
        SearchTermPerformance.amazon_ad_group_id == amazon_ad_group_id,
        SearchTermPerformance.report_date_start <= st_end,
        SearchTermPerformance.report_date_end >= st_start,
    ]
    if cred.profile_id is not None:
        st_where.append(SearchTermPerformance.profile_id == cred.profile_id)
    else:
        st_where.append(SearchTermPerformance.profile_id.is_(None))
    perf_result = await db.execute(
        select(
            func.sum(SearchTermPerformance.cost).label("spend"),
            func.sum(SearchTermPerformance.sales).label("sales"),
            func.sum(SearchTermPerformance.clicks).label("clicks"),
        )
        .where(
            *st_where,
        )
    )
    row = perf_result.first()
    if row and (row.spend or row.sales):
        spend = float(row.spend or 0)
        sales = float(row.sales or 0)
        ad_group_metrics = {
            "spend": spend,
            "sales": sales,
            "clicks": int(row.clicks or 0),
            "acos": round(spend / sales * 100, 2) if sales > 0 else None,
        }

    # Get PA-API credentials for product image fetching
    paapi_access, paapi_secret, paapi_tag = None, None, None
    try:
        from app.routers.settings import _get_paapi_from_row
        app_result = await db.execute(select(AppSettings).limit(1))
        app_row = app_result.scalar_one_or_none()
        paapi_access, paapi_secret, paapi_tag = _get_paapi_from_row(app_row)
    except Exception:
        pass

    # For single-ad groups, attach ad group metrics to the ad (best available proxy for ad-level data)
    ad_list = []
    for a in ads:
        # Fallback: extract ASIN/SKU from raw_data if missing (e.g. pre-fix synced ads)
        asin_val, sku_val = a.asin, a.sku
        if (not asin_val or not sku_val) and a.raw_data:
            ra, rs = extract_ad_asin_sku(a.raw_data)
            asin_val = asin_val or ra
            sku_val = sku_val or rs
        ad_name_val = a.ad_name or ((a.raw_data or {}).get("creative") or {}).get("headline") if a.raw_data else a.ad_name
        if not ad_name_val:
            ad_name_val = (f"ASIN: {asin_val}" if asin_val else None) or (f"SKU: {sku_val}" if sku_val else None)
        if not ad_name_val and a.raw_data:
            ra, rs = extract_ad_asin_sku(a.raw_data)
            ad_name_val = extract_ad_display_name(a.raw_data, ra, rs)
        d = {
            "id": str(a.id),
            "amazon_ad_id": a.amazon_ad_id,
            "amazon_ad_group_id": a.amazon_ad_group_id,
            "amazon_campaign_id": a.amazon_campaign_id,
            "ad_name": ad_name_val,
            "ad_type": a.ad_type,
            "state": a.state,
            "asin": asin_val,
            "sku": sku_val,
            "synced_at": a.synced_at.isoformat() if a.synced_at else None,
        }
        # Surface creative assets: raw_data, product URL, image URL
        if a.raw_data:
            d["raw_data"] = a.raw_data
        if asin_val:
            d["product_url"] = f"https://www.amazon.com/dp/{asin_val}"
        # Get image: raw_data extraction, then PA-API, then ASIN fallback URL
        img_url = await get_product_image_url(
            asin=asin_val,
            raw_data=a.raw_data,
            paapi_access_key=paapi_access,
            paapi_secret_key=paapi_secret,
            paapi_partner_tag=paapi_tag,
        )
        if img_url:
            d["image_url"] = img_url
        if ad_group_metrics and len(ads) == 1:
            d["spend"] = ad_group_metrics["spend"]
            d["sales"] = ad_group_metrics["sales"]
            d["clicks"] = ad_group_metrics["clicks"]
            d["acos"] = ad_group_metrics["acos"]
        ad_list.append(d)

    return {
        "ads": ad_list,
        "count": len(ads),
        "ad_group_metrics": ad_group_metrics,
    }


class AdCreateRequest(BaseModel):
    ad_data: dict
    skip_approval: bool = False


@router.post("/ad-groups/{amazon_ad_group_id}/ads")
async def create_ad(
    amazon_ad_group_id: str,
    req: AdCreateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Create an ad in an ad group."""
    cred = await _get_credential(db, credential_id)
    payload = {"adGroupId": amazon_ad_group_id, **req.ad_data}

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.create_ad([payload])
            db.add(ActivityLog(
                credential_id=cred.id, action="ad_created",
                category="campaigns",
                description=f"Created ad in ad group {amazon_ad_group_id}",
                entity_type="ad", details={"result": result},
            ))
            await db.flush()
            return {"status": "created", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="ad_create",
            entity_type="ad",
            entity_name=req.ad_data.get("name") or req.ad_data.get("asin", "New Ad"),
            proposed_value=str(req.ad_data),
            change_detail=req.ad_data,
            mcp_payload={"tool": "campaign_management-create_ad", "arguments": {"body": {"ads": [payload]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


class AdUpdateRequest(BaseModel):
    updates: dict
    skip_approval: bool = False


@router.put("/ads/{amazon_ad_id}")
async def update_ad(
    amazon_ad_id: str,
    req: AdUpdateRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Update an ad (name, state)."""
    cred = await _get_credential(db, credential_id)
    normalized_updates = _normalized_updates_for_mcp(req.updates)
    payload = {"adId": amazon_ad_id, **normalized_updates}

    if req.skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.update_ad([payload])
            existing = await db.execute(
                select(Ad).where(
                    Ad.credential_id == cred.id,
                    Ad.amazon_ad_id == amazon_ad_id,
                )
            )
            ad = existing.scalar_one_or_none()
            if ad:
                if "name" in normalized_updates:
                    ad.ad_name = normalized_updates["name"]
                if "state" in normalized_updates:
                    ad.state = normalize_state_value(normalized_updates["state"], for_storage=True)
                ad.synced_at = utcnow()

            db.add(ActivityLog(
                credential_id=cred.id, action="ad_updated",
                category="campaigns",
                description=f"Updated ad {amazon_ad_id}",
                entity_type="ad", entity_id=amazon_ad_id,
            ))
            await db.flush()
            return {"status": "updated", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(Ad).where(Ad.credential_id == cred.id, Ad.amazon_ad_id == amazon_ad_id)
        )
        ad = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="ad_update",
            entity_type="ad",
            entity_id=amazon_ad_id,
            entity_name=ad.ad_name if ad else amazon_ad_id,
            current_value=str(ad.raw_data) if ad else None,
            proposed_value=str(normalized_updates),
            change_detail=normalized_updates,
            mcp_payload={"tool": "campaign_management-update_ad", "arguments": {"body": {"ads": [payload]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


@router.delete("/ads/{amazon_ad_id}")
async def delete_ad(
    amazon_ad_id: str,
    credential_id: Optional[str] = Query(None),
    skip_approval: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Delete an ad."""
    cred = await _get_credential(db, credential_id)

    if skip_approval:
        client = await _make_client(cred, db)
        try:
            result = await client.delete_ad([amazon_ad_id])
            db.add(ActivityLog(
                credential_id=cred.id, action="ad_deleted",
                category="campaigns",
                description=f"Deleted ad {amazon_ad_id}",
                entity_type="ad", entity_id=amazon_ad_id,
            ))
            await db.flush()
            return {"status": "deleted", "result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))
    else:
        existing = await db.execute(
            select(Ad).where(Ad.credential_id == cred.id, Ad.amazon_ad_id == amazon_ad_id)
        )
        ad = existing.scalar_one_or_none()
        change = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type="ad_delete",
            entity_type="ad",
            entity_id=amazon_ad_id,
            entity_name=ad.ad_name if ad else amazon_ad_id,
            current_value=ad.ad_name if ad else amazon_ad_id,
            proposed_value="DELETE",
            mcp_payload={"tool": "campaign_management-delete_ad", "arguments": {"body": {"adIds": [amazon_ad_id]}}},
            source="manual",
        )
        db.add(change)
        await db.flush()
        return {"status": "pending_approval", "change_id": str(change.id)}


# ══════════════════════════════════════════════════════════════════════
#  SYNC — Pull fresh data from MCP into local cache
# ══════════════════════════════════════════════════════════════════════

async def _update_sync_job(
    db: AsyncSession,
    job_id: uuid_mod.UUID,
    step: str,
    progress_pct: int,
    stats: Optional[dict] = None,
    status: Optional[str] = None,
    error_message: Optional[str] = None,
    completed_at: Optional[datetime] = None,
) -> None:
    """Update SyncJob progress. Used by background sync task."""
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if job:
        job.step = step
        job.progress_pct = progress_pct
        if stats is not None:
            job.stats = stats
        if status:
            job.status = status
        if error_message is not None:
            job.error_message = error_message
        if completed_at is not None:
            job.completed_at = completed_at
        await db.flush()


async def run_full_sync(
    db: AsyncSession,
    credential_id: Optional[str] = None,
    job_id: Optional[uuid_mod.UUID] = None,
    on_progress: Optional[Callable[[str, int, dict], Awaitable[None]]] = None,
    profile_id_override: Optional[str] = None,
) -> dict:
    """
    Run full campaign/ad group/target/ad sync. Used by POST /sync, cron, and background job.
    When job_id and on_progress are provided, updates SyncJob after each step.
    """
    cred = await _get_credential(db, credential_id)
    active_profile_id = profile_id_override if profile_id_override is not None else cred.profile_id
    _, scope_error = await resolve_campaign_sync_scope(db, cred, profile_id_override=active_profile_id)
    if scope_error:
        raise HTTPException(status_code=400, detail=scope_error)
    client = await _make_client(cred, db, profile_id_override=active_profile_id)
    stats = {"campaigns": 0, "ad_groups": 0, "targets": 0, "ads": 0}

    async def _progress(step: str, pct: int, s: dict) -> None:
        if on_progress:
            await on_progress(step, pct, s)

    try:
        # 1. Sync campaigns
        await _progress("Pulling campaigns from Amazon Ads (SP, SB, SD)...", 10, stats)
        raw_campaigns = await client.query_campaigns()
        campaign_list = _extract_list(raw_campaigns, ["campaigns", "result", "results"])
        for camp_data in campaign_list:
            amazon_id = camp_data.get("campaignId") or camp_data.get("id") or str(uuid_mod.uuid4())
            existing = await db.execute(
                select(Campaign).where(
                    Campaign.credential_id == cred.id,
                    Campaign.amazon_campaign_id == str(amazon_id),
                )
            )
            campaign = existing.scalar_one_or_none()
            camp_name = camp_data.get("name") or camp_data.get("campaignName")
            camp_type = camp_data.get("adProduct") or camp_data.get("campaignType")
            targeting = camp_data.get("targetingType") or camp_data.get("targeting")
            state = normalize_state_value(camp_data.get("state") or camp_data.get("status"), for_storage=True)
            budget = camp_data.get("dailyBudget") or camp_data.get("budget")
            start_date = normalize_amazon_date(camp_data.get("startDate") or camp_data.get("startDateTime"))
            end_date = normalize_amazon_date(camp_data.get("endDate") or camp_data.get("endDateTime"))
            if not budget and camp_data.get("budgets"):
                for b in camp_data["budgets"]:
                    if b.get("recurrenceTimePeriod") == "DAILY":
                        mv = b.get("budgetValue", {}).get("monetaryBudgetValue", {}).get("monetaryBudget", {})
                        budget = mv.get("value")
                        break

            if campaign:
                campaign.profile_id = active_profile_id
                campaign.campaign_name = camp_name or campaign.campaign_name
                campaign.campaign_type = camp_type or campaign.campaign_type
                campaign.targeting_type = targeting or campaign.targeting_type
                campaign.state = state or campaign.state
                campaign.daily_budget = float(budget) if budget else campaign.daily_budget
                campaign.start_date = start_date or campaign.start_date
                campaign.end_date = end_date or campaign.end_date
                campaign.raw_data = camp_data
                campaign.synced_at = utcnow()
            else:
                campaign = Campaign(
                    credential_id=cred.id,
                    profile_id=active_profile_id,
                    amazon_campaign_id=str(amazon_id),
                    campaign_name=camp_name,
                    campaign_type=camp_type,
                    targeting_type=targeting,
                    state=state,
                    daily_budget=float(budget) if budget else None,
                    start_date=start_date,
                    end_date=end_date,
                    raw_data=camp_data,
                )
                db.add(campaign)
            stats["campaigns"] += 1

        await _progress("Syncing ad groups...", 35, stats)
        # 2. Sync ad groups (SP, SB, SD)
        raw_groups = await client.query_ad_groups(all_products=True)
        group_list = _extract_list(raw_groups, ["adGroups", "result", "results"])
        for grp_data in group_list:
            amazon_id = grp_data.get("adGroupId") or grp_data.get("id") or str(uuid_mod.uuid4())
            amz_campaign_id = grp_data.get("campaignId")

            local_campaign = None
            if amz_campaign_id:
                camp_result = await db.execute(
                    select(Campaign).where(
                        Campaign.credential_id == cred.id,
                        Campaign.amazon_campaign_id == str(amz_campaign_id),
                    )
                )
                local_campaign = camp_result.scalar_one_or_none()

            existing = await db.execute(
                select(AdGroup).where(
                    AdGroup.credential_id == cred.id,
                    AdGroup.amazon_ad_group_id == str(amazon_id),
                )
            )
            ad_group = existing.scalar_one_or_none()
            bid_val = grp_data.get("defaultBid") or grp_data.get("bid")
            if isinstance(bid_val, dict):
                bid_val = bid_val.get("value") or bid_val.get("monetaryBid", {}).get("value")

            if ad_group:
                ad_group.ad_group_name = grp_data.get("name") or grp_data.get("adGroupName") or ad_group.ad_group_name
                ad_group.state = grp_data.get("state") or ad_group.state
                ad_group.default_bid = float(bid_val) if bid_val else ad_group.default_bid
                ad_group.amazon_campaign_id = str(amz_campaign_id) if amz_campaign_id else ad_group.amazon_campaign_id
                ad_group.campaign_id = local_campaign.id if local_campaign else ad_group.campaign_id
                ad_group.raw_data = grp_data
                ad_group.synced_at = utcnow()
            else:
                ad_group = AdGroup(
                    credential_id=cred.id,
                    campaign_id=local_campaign.id if local_campaign else None,
                    amazon_ad_group_id=str(amazon_id),
                    amazon_campaign_id=str(amz_campaign_id) if amz_campaign_id else None,
                    ad_group_name=grp_data.get("name") or grp_data.get("adGroupName"),
                    state=grp_data.get("state"),
                    default_bid=float(bid_val) if bid_val else None,
                    raw_data=grp_data,
                )
                db.add(ad_group)
            stats["ad_groups"] += 1

        await _progress("Syncing targets (keywords, product targets)...", 60, stats)
        # 3. Sync targets (keywords/product targets for SP, SB, SD)
        raw_targets = await client.query_targets(all_products=True)
        target_list = _extract_list(raw_targets, ["targets", "result", "results"])
        _logged_target_debug = False
        for tgt_data in target_list:
            amazon_id = tgt_data.get("targetId") or tgt_data.get("id") or str(uuid_mod.uuid4())
            amz_ag_id = tgt_data.get("adGroupId")

            local_ag = None
            if amz_ag_id:
                ag_result = await db.execute(
                    select(AdGroup).where(
                        AdGroup.credential_id == cred.id,
                        AdGroup.amazon_ad_group_id == str(amz_ag_id),
                    )
                )
                local_ag = ag_result.scalar_one_or_none()

            existing = await db.execute(
                select(Target).where(
                    Target.credential_id == cred.id,
                    Target.amazon_target_id == str(amazon_id),
                )
            )
            target = existing.scalar_one_or_none()
            bid_val = tgt_data.get("bid") or tgt_data.get("defaultBid")
            if isinstance(bid_val, dict):
                bid_val = bid_val.get("value") or bid_val.get("monetaryBid", {}).get("value")

            target_details = tgt_data.get("targetDetails", {})
            expression = extract_target_expression(tgt_data)
            if not expression and tgt_data and not _logged_target_debug:
                logger.warning(f"Target extraction failed: keys={list(tgt_data.keys())}, targetDetails={list(target_details.keys()) if target_details else []}, sample={str(tgt_data)[:600]}")
                _logged_target_debug = True

            tgt_type = tgt_data.get("targetType") or tgt_data.get("type") or target_details.get("targetType")
            _kt = target_details.get("keywordTarget")
            _kt_m = _kt.get("matchType") if isinstance(_kt, dict) else None
            match_type = tgt_data.get("matchType") or target_details.get("matchType") or _kt_m

            if target:
                target.target_type = tgt_type or target.target_type
                target.expression_value = str(expression) if expression else target.expression_value
                target.match_type = match_type or target.match_type
                target.state = tgt_data.get("state") or target.state
                target.bid = float(bid_val) if bid_val else target.bid
                target.amazon_campaign_id = tgt_data.get("campaignId") or target.amazon_campaign_id
                target.amazon_ad_group_id = str(amz_ag_id) if amz_ag_id else target.amazon_ad_group_id
                target.ad_group_id = local_ag.id if local_ag else target.ad_group_id
                target.raw_data = tgt_data
                target.synced_at = utcnow()
            else:
                target = Target(
                    credential_id=cred.id,
                    ad_group_id=local_ag.id if local_ag else None,
                    amazon_target_id=str(amazon_id),
                    amazon_ad_group_id=str(amz_ag_id) if amz_ag_id else None,
                    amazon_campaign_id=tgt_data.get("campaignId"),
                    target_type=tgt_type,
                    expression_value=str(expression) if expression else None,
                    match_type=match_type,
                    state=tgt_data.get("state"),
                    bid=float(bid_val) if bid_val else None,
                    raw_data=tgt_data,
                )
                db.add(target)
            stats["targets"] += 1

        await db.flush()
        marketplace_for_reports: Optional[str] = None
        if active_profile_id:
            mp_res = await db.execute(
                select(Account.marketplace)
                .where(
                    Account.credential_id == cred.id,
                    Account.profile_id == active_profile_id,
                )
                .limit(1)
            )
            marketplace_for_reports = mp_res.scalar_one_or_none()
        try:
            perf_merge = await apply_targeting_performance_to_db_targets(
                db,
                cred.id,
                client,
                marketplace=marketplace_for_reports,
                amazon_campaign_id=None,
                lookback_days=30,
                max_wait=120,
            )
            stats["target_report_merge"] = perf_merge
        except Exception as e:
            logger.warning("Target report merge during full sync failed: %s", e)

        await _progress("Syncing ads...", 85, stats)
        # 4. Sync ads
        _logged_ad_debug = False
        try:
            raw_ads = await client.query_ads(all_products=True)
            ad_list = _extract_list(raw_ads, ["ads", "result", "results"])
            for ad_data in ad_list:
                amazon_id = ad_data.get("adId") or ad_data.get("id") or str(uuid_mod.uuid4())
                amz_ag_id = ad_data.get("adGroupId")
                amz_camp_id = ad_data.get("campaignId")

                local_ag = None
                if amz_ag_id:
                    ag_result = await db.execute(
                        select(AdGroup).where(
                            AdGroup.credential_id == cred.id,
                            AdGroup.amazon_ad_group_id == str(amz_ag_id),
                        )
                    )
                    local_ag = ag_result.scalar_one_or_none()

                existing = await db.execute(
                    select(Ad).where(
                        Ad.credential_id == cred.id,
                        Ad.amazon_ad_id == str(amazon_id),
                    )
                )
                ad = existing.scalar_one_or_none()

                ad_asin, ad_sku = extract_ad_asin_sku(ad_data)
                if not ad_asin and not ad_sku and ad_data and not _logged_ad_debug:
                    logger.warning(f"Ad ASIN extraction failed: keys={list(ad_data.keys())}, creative keys={list((ad_data.get('creative') or {}).keys())}, sample={str(ad_data)[:600]}")
                    _logged_ad_debug = True
                ad_name = extract_ad_display_name(ad_data, ad_asin, ad_sku) or ad_data.get("name") or ad_data.get("adName") or (ad_data.get("creative") or {}).get("headline")
                if ad:
                    ad.ad_name = ad_name or ad.ad_name
                    ad.ad_type = ad_data.get("adType") or ad_data.get("type") or ad.ad_type
                    ad.state = ad_data.get("state") or ad.state
                    ad.asin = ad_asin or ad.asin
                    ad.sku = ad_sku or ad.sku
                    ad.amazon_ad_group_id = str(amz_ag_id) if amz_ag_id else ad.amazon_ad_group_id
                    ad.amazon_campaign_id = str(amz_camp_id) if amz_camp_id else ad.amazon_campaign_id
                    ad.ad_group_id = local_ag.id if local_ag else ad.ad_group_id
                    ad.raw_data = ad_data
                    ad.synced_at = utcnow()
                else:
                    ad = Ad(
                        credential_id=cred.id,
                        ad_group_id=local_ag.id if local_ag else None,
                        amazon_ad_id=str(amazon_id),
                        amazon_ad_group_id=str(amz_ag_id) if amz_ag_id else None,
                        amazon_campaign_id=str(amz_camp_id) if amz_camp_id else None,
                        ad_name=ad_name,
                        ad_type=ad_data.get("adType") or ad_data.get("type"),
                        state=ad_data.get("state"),
                        asin=ad_asin,
                        sku=ad_sku,
                        raw_data=ad_data,
                    )
                    db.add(ad)
                stats["ads"] += 1
        except Exception as e:
            logger.warning(f"Ad sync failed (non-critical): {e}")

        await _progress("Finishing...", 100, stats)
        db.add(ActivityLog(
            credential_id=cred.id,
            action="full_sync",
            category="campaigns",
            description=f"Synced {stats['campaigns']} campaigns, {stats['ad_groups']} ad groups, {stats['targets']} targets, {stats['ads']} ads",
            entity_type="sync",
            details=stats,
        ))

        await db.flush()
        return {"status": "synced", "stats": stats}

    except Exception as e:
        logger.error(f"Full sync failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Campaign operation failed. Please try again."))


async def _run_sync_background(
    job_id: uuid_mod.UUID,
    credential_id: Optional[str],
    user_email: Optional[str],
    account_name: Optional[str],
    profile_id_override: Optional[str] = None,
) -> None:
    """Run sync in background, update SyncJob progress, send email on completion.

    ``profile_id_override`` lets cron / programmatic callers target a specific
    profile on a multi-profile credential without depending on whichever
    profile is currently marked default in the DB.
    """
    from app.services.email_service import send_sync_complete_email

    async def on_progress(step: str, pct: int, s: dict) -> None:
        """Update SyncJob in a separate session so polling sees progress without committing sync data."""
        async with async_session() as job_db:
            await _update_sync_job(job_db, job_id, step, pct, stats=s)
            await job_db.commit()

    async with async_session() as db:
        try:
            result = await run_full_sync(
                db,
                credential_id,
                job_id=job_id,
                on_progress=on_progress,
                profile_id_override=profile_id_override,
            )
            await db.commit()

            # Mark completed
            await _update_sync_job(
                db, job_id, "Completed", 100,
                stats=result.get("stats"),
                status="completed",
                completed_at=utcnow(),
            )
            await db.commit()

            # Send email
            if user_email:
                asyncio.create_task(asyncio.to_thread(
                    send_sync_complete_email,
                    user_email,
                    success=True,
                    stats=result.get("stats"),
                    account_name=account_name,
                ))
        except Exception as e:
            logger.exception(f"Background sync failed: {e}")
            err_msg = safe_error_detail(e, "Campaign sync failed.")
            try:
                await _update_sync_job(
                    db, job_id, "Failed", 0,
                    status="failed",
                    error_message=err_msg,
                    completed_at=utcnow(),
                )
                await db.commit()
            except Exception:
                await db.rollback()
            if user_email:
                asyncio.create_task(asyncio.to_thread(
                    send_sync_complete_email,
                    user_email,
                    success=False,
                    error_message=err_msg,
                    account_name=account_name,
                ))


@router.get("/debug/mcp-structure")
async def debug_mcp_structure(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Return sample raw_data from targets and ads to inspect MCP response structure.
    Use this to debug extraction when keywords/ads show IDs instead of actual data.
    """
    cred = await _get_credential(db, credential_id)
    # Get one target and one ad with raw_data
    t_result = await db.execute(
        select(Target).where(Target.credential_id == cred.id).limit(1)
    )
    a_result = await db.execute(
        select(Ad).where(Ad.credential_id == cred.id).limit(1)
    )
    target = t_result.scalar_one_or_none()
    ad = a_result.scalar_one_or_none()
    return {
        "target_sample": {
            "expression_value": target.expression_value if target else None,
            "raw_data_keys": list(target.raw_data.keys()) if target and target.raw_data else [],
            "raw_data": target.raw_data if target and target.raw_data else None,
        },
        "ad_sample": {
            "ad_name": ad.ad_name if ad else None,
            "asin": ad.asin if ad else None,
            "sku": ad.sku if ad else None,
            "raw_data_keys": list(ad.raw_data.keys()) if ad and ad.raw_data else [],
            "raw_data": ad.raw_data if ad and ad.raw_data else None,
        },
    }


@router.post("/sync")
async def sync_all(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Full sync (blocking): Pull campaigns, ad groups, targets, and ads from MCP.
    Used by cron and programmatic calls. For UI with progress tracking, use POST /sync/start.
    """
    return await run_full_sync(db, credential_id)


@router.post("/sync/start")
async def sync_start(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Start campaign sync in background. Returns job_id immediately.
    Poll GET /sync/{job_id} for progress. Email sent on completion.
    """
    cred = await _get_credential(db, credential_id)
    scoped_account, scope_error = await resolve_campaign_sync_scope(db, cred)
    if scope_error:
        raise HTTPException(status_code=400, detail=scope_error)
    job = SyncJob(
        credential_id=cred.id,
        user_id=user.id,
        status="running",
        step="Starting...",
        progress_pct=0,
    )
    db.add(job)
    await db.flush()

    # Get account name for email — use active profile (cred.profile_id) so email matches the account actually synced
    account_name = None
    if scoped_account:
        account_name = scoped_account.account_name or scoped_account.amazon_account_id

    asyncio.create_task(_run_sync_background(
        job.id, credential_id, user.email, account_name,
    ))
    return {"job_id": str(job.id), "status": "started"}


@router.get("/sync/latest")
async def sync_latest(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest sync job for the credential."""
    cred = await _get_credential(db, credential_id)
    result = await db.execute(
        select(SyncJob)
        .where(SyncJob.credential_id == cred.id)
        .order_by(SyncJob.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if not job:
        return {"job": None}
    return {
        "job": {
            "job_id": str(job.id),
            "status": job.status,
            "step": job.step,
            "progress_pct": job.progress_pct or 0,
            "stats": job.stats,
            "error_message": job.error_message,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
    }


@router.get("/sync/{job_id}")
async def sync_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Poll for sync job progress."""
    jid = parse_uuid(job_id, "job_id")
    result = await db.execute(select(SyncJob).where(SyncJob.id == jid))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return {
        "job_id": str(job.id),
        "status": job.status,
        "step": job.step,
        "progress_pct": job.progress_pct or 0,
        "stats": job.stats,
        "error_message": job.error_message,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


# ══════════════════════════════════════════════════════════════════════
#  STATISTICS — Summary counts for the campaign manager dashboard
# ══════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def campaign_stats(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get summary counts for campaigns, ad groups, targets, and ads.

    Scoped by ``profile_id`` so multi-profile credentials don't leak totals
    across marketplaces — matches the list endpoints' default behaviour.
    Pass ``profile_id=null`` (or omit) to fall back to the credential's
    default profile.
    """
    cred = await _get_credential(db, credential_id)
    selected_profile_id = profile_id if profile_id is not None else cred.profile_id

    def _scoped(model):
        clauses = [model.credential_id == cred.id]
        if hasattr(model, "profile_id"):
            if selected_profile_id is not None:
                clauses.append(model.profile_id == selected_profile_id)
            else:
                clauses.append(model.profile_id.is_(None))
        return and_(*clauses)

    campaigns_total = (await db.execute(
        select(func.count()).select_from(Campaign).where(_scoped(Campaign))
    )).scalar() or 0
    campaigns_enabled = (await db.execute(
        select(func.count()).select_from(Campaign).where(
            _scoped(Campaign), func.lower(Campaign.state) == "enabled"
        )
    )).scalar() or 0
    campaigns_paused = (await db.execute(
        select(func.count()).select_from(Campaign).where(
            _scoped(Campaign), func.lower(Campaign.state) == "paused"
        )
    )).scalar() or 0
    ad_groups_total = (await db.execute(
        select(func.count()).select_from(AdGroup).where(_scoped(AdGroup))
    )).scalar() or 0
    targets_total = (await db.execute(
        select(func.count()).select_from(Target).where(_scoped(Target))
    )).scalar() or 0
    ads_total = (await db.execute(
        select(func.count()).select_from(Ad).where(_scoped(Ad))
    )).scalar() or 0

    total_spend = (await db.execute(
        select(func.sum(Campaign.spend)).where(_scoped(Campaign))
    )).scalar() or 0
    total_sales = (await db.execute(
        select(func.sum(Campaign.sales)).where(_scoped(Campaign))
    )).scalar() or 0

    return {
        "credential_id": str(cred.id),
        "profile_id": selected_profile_id,
        "campaigns": {"total": campaigns_total, "enabled": campaigns_enabled, "paused": campaigns_paused},
        "ad_groups": {"total": ad_groups_total},
        "targets": {"total": targets_total},
        "ads": {"total": ads_total},
        "performance": {
            "total_spend": round(total_spend, 2),
            "total_sales": round(total_sales, 2),
        },
    }
