"""
Reporting Router — Advanced performance reporting with date-range presets,
period-over-period comparison, campaign breakdowns, trend data, and
full historical tracking in campaign_performance_daily /
account_performance_daily tables.
"""

import uuid
import logging
from datetime import datetime, date as date_type, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import (
    Credential, Campaign, AuditSnapshot, Report, ActivityLog, Account,
    SearchTermPerformance,
)
from app.services.token_service import get_mcp_client_with_fresh_token
from app.services.reporting_service import (
    get_date_range, get_comparison_range, get_comparison_range_for_dates,
    compute_metrics, compute_deltas, enrich_campaigns, ReportingService,
    store_campaign_daily_data, store_account_daily_summary,
    query_campaign_daily, query_account_daily_trend,
    has_daily_data, find_encompassing_range_data, DATE_PRESETS,
    get_currency_for_marketplace,
)
from app.services.search_term_service import SearchTermService, get_search_term_summary
from app.utils import parse_uuid, utcnow

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────

async def _get_cred(db: AsyncSession, cred_id: str = None) -> Credential:
    if cred_id:
        result = await db.execute(
            select(Credential).where(Credential.id == parse_uuid(cred_id, "credential_id"))
        )
    else:
        result = await db.execute(
            select(Credential).where(Credential.is_default == True)
        )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credential found.")
    return cred


async def _resolve_advertiser_account_id(db: AsyncSession, cred: Credential) -> Optional[str]:
    """
    Resolve the Amazon Ads advertiserAccountId (amzn1.ads-account.g.xxx format)
    from the active Account's raw_data. The report API requires this — an empty
    accessRequestedAccounts array causes a server-side serialization error.
    """
    if not cred.profile_id:
        return None
    result = await db.execute(
        select(Account).where(
            Account.credential_id == cred.id,
            Account.profile_id == cred.profile_id,
        )
    )
    active_account = result.scalar_one_or_none()
    if active_account and active_account.raw_data:
        adv_id = active_account.raw_data.get("advertiserAccountId")
        if adv_id:
            return adv_id
    return None


async def _resolve_currency(db: AsyncSession, cred: Credential) -> str:
    """Resolve the currency code for the active account's marketplace."""
    if cred.profile_id:
        result = await db.execute(
            select(Account).where(
                Account.credential_id == cred.id,
                Account.profile_id == cred.profile_id,
            )
        )
        active_account = result.scalar_one_or_none()
        if active_account and active_account.marketplace:
            return get_currency_for_marketplace(
                marketplace=active_account.marketplace,
                region=cred.region,
            )
    return get_currency_for_marketplace(region=cred.region)


def _campaign_to_dict(c: Campaign) -> dict:
    return {
        "campaign_id": c.amazon_campaign_id,
        "campaign_name": c.campaign_name or "Unknown",
        "campaign_type": c.campaign_type,
        "targeting_type": c.targeting_type,
        "state": c.state,
        "daily_budget": c.daily_budget or 0,
        "spend": c.spend or 0,
        "sales": c.sales or 0,
        "impressions": c.impressions or 0,
        "clicks": c.clicks or 0,
        "orders": c.orders or 0,
        "acos": c.acos or 0,
        "roas": c.roas or 0,
        "start_date": c.start_date,
        "synced_at": c.synced_at.isoformat() if c.synced_at else None,
    }


# ── Request models ────────────────────────────────────────────────────

class GenerateReportRequest(BaseModel):
    credential_id: Optional[str] = None
    preset: str = "this_month"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    compare: bool = False


def _resolve_date_range(
    preset: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[date_type, date_type, str]:
    """
    Resolve (start, end) dates from preset or explicit start_date/end_date.
    Returns (start_date, end_date, preset_label).
    """
    if start_date and end_date:
        try:
            s = date_type.fromisoformat(start_date)
            e = date_type.fromisoformat(end_date)
            if s <= e:
                label = f"Custom ({start_date} – {end_date})"
                return s, e, label
        except Exception:
            pass
    safe_preset = preset if preset and preset in DATE_PRESETS else "this_month"
    s, e = get_date_range(safe_preset)
    label = safe_preset.replace("_", " ").title()
    return s, e, label


# ══════════════════════════════════════════════════════════════════════
#  GET /summary — Quick summary from cached DB campaigns
# ══════════════════════════════════════════════════════════════════════

@router.get("/summary")
async def report_summary(
    credential_id: Optional[str] = Query(None),
    preset: Optional[str] = Query("this_month"),
    start_date: Optional[str] = Query(None, description="Custom start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="Custom end date YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
):
    """
    Quick summary from cached DB campaigns + historical daily data.
    Fast endpoint for initial page load.
    Use preset or explicit start_date/end_date for custom ranges.
    """
    cred = await _get_cred(db, credential_id)
    logger.info("Reports summary: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)
    currency_code = await _resolve_currency(db, cred)

    # Determine date range
    start_d, end_d, _ = _resolve_date_range(preset or "this_month", start_date, end_date)
    start_str = start_d.isoformat()
    end_str = end_d.isoformat()

    # Try historical daily performance data first (stored from reports)
    # For single-day ranges, also check the exact date as storage key
    is_single_day = start_str == end_str
    storage_key = start_str if is_single_day else f"{start_str}__{end_str}"

    # Try exact storage key first, then smart range matching, then date-range aggregation
    # 1. Exact key: matches range rows (e.g. "2026-02-01__2026-02-12") or single-day rows
    daily_campaigns = await query_campaign_daily(db, cred.id, storage_key, storage_key, profile_id=cred.profile_id)
    if not daily_campaigns:
        # 2. Encompassing range: matches stored range keys that overlap the period
        daily_campaigns = await find_encompassing_range_data(db, cred.id, start_str, end_str, profile_id=cred.profile_id)
    if not daily_campaigns and not is_single_day:
        # 3. Date-range aggregation: matches single-day rows (from audit) stored as YYYY-MM-DD
        daily_campaigns = await query_campaign_daily(db, cred.id, start_str, end_str, profile_id=cred.profile_id)

    has_history = len(daily_campaigns) > 0
    last_synced = None

    if daily_campaigns:
        enriched = daily_campaigns  # Already enriched by query_campaign_daily
        last_synced = utcnow().isoformat()
    else:
        # Fallback: cached campaigns table
        camp_query = select(Campaign).where(Campaign.credential_id == cred.id)
        if cred.profile_id is not None:
            camp_query = camp_query.where(Campaign.profile_id == cred.profile_id)
        else:
            camp_query = camp_query.where(Campaign.profile_id.is_(None))
        camp_query = camp_query.order_by(Campaign.spend.desc().nullslast())
        result = await db.execute(camp_query)
        campaigns = result.scalars().all()
        campaign_list = [_campaign_to_dict(c) for c in campaigns]
        enriched = enrich_campaigns(campaign_list)
        last_synced = campaigns[0].synced_at.isoformat() if campaigns else None

    summary = compute_metrics(enriched)
    active = [c for c in enriched if (c.get("state") or "").lower() in ("enabled", "active")]
    paused = [c for c in enriched if (c.get("state") or "").lower() == "paused"]

    by_sales = sorted(enriched, key=lambda x: x.get("sales", 0), reverse=True)
    by_acos_worst = sorted(
        [c for c in enriched if c.get("spend", 0) > 0],
        key=lambda x: x.get("acos", 0),
        reverse=True,
    )

    snap_result = await db.execute(
        select(AuditSnapshot)
        .where(AuditSnapshot.credential_id == cred.id)
        .order_by(AuditSnapshot.created_at.desc())
        .limit(1)
    )
    latest_snap = snap_result.scalar_one_or_none()

    return {
        "summary": summary,
        "total_campaigns": len(enriched),
        "active_campaigns": len(active),
        "paused_campaigns": len(paused),
        "campaigns": enriched,
        "top_performers": by_sales[:5],
        "worst_performers": by_acos_worst[:5],
        "has_historical_data": has_history,
        "last_synced": last_synced,
        "currency_code": currency_code,
        "period": {
            "start_date": start_str,
            "end_date": end_str,
            "preset": preset if preset and preset in DATE_PRESETS else "this_month",
        },
        "latest_snapshot": {
            "id": str(latest_snap.id),
            "total_spend": latest_snap.total_spend,
            "total_sales": latest_snap.total_sales,
            "avg_acos": latest_snap.avg_acos,
            "avg_roas": latest_snap.avg_roas,
            "waste_identified": latest_snap.waste_identified,
            "issues_count": latest_snap.issues_count,
            "opportunities_count": latest_snap.opportunities_count,
            "created_at": latest_snap.created_at.isoformat(),
        } if latest_snap else None,
    }


# ══════════════════════════════════════════════════════════════════════
#  GET /trends — Daily trend data from account_performance_daily
# ══════════════════════════════════════════════════════════════════════

@router.get("/trends")
async def report_trends(
    credential_id: Optional[str] = Query(None),
    preset: Optional[str] = Query("this_month"),
    start_date: Optional[str] = Query(None, description="Custom start date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="Custom end date YYYY-MM-DD"),
    limit: int = Query(90),
    db: AsyncSession = Depends(get_db),
):
    """
    Daily trend data from account_performance_daily table.
    Falls back to audit snapshots if no daily data exists yet.
    """
    cred = await _get_cred(db, credential_id)
    logger.info("Reports trends: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)

    # Compute date range from preset or explicit dates
    start_d, end_d, _ = _resolve_date_range(preset or "this_month", start_date, end_date)
    start_date = start_d
    end_date = end_d

    # Try daily history table first
    daily_trend = await query_account_daily_trend(
        db, cred.id, start_date.isoformat(), end_date.isoformat(), profile_id=cred.profile_id
    )

    if daily_trend:
        return {"source": "daily_history", "data": daily_trend}

    # Fallback: audit snapshots
    result = await db.execute(
        select(AuditSnapshot)
        .where(AuditSnapshot.credential_id == cred.id)
        .order_by(AuditSnapshot.created_at.asc())
        .limit(limit)
    )
    snapshots = result.scalars().all()

    fallback = [
        {
            "date": s.created_at.strftime("%Y-%m-%d"),
            "spend": s.total_spend or 0,
            "sales": s.total_sales or 0,
            "acos": s.avg_acos or 0,
            "roas": s.avg_roas or 0,
            "campaigns": s.campaigns_count or 0,
            "active": s.active_campaigns or 0,
            "waste": s.waste_identified or 0,
            "issues": s.issues_count or 0,
            "opportunities": s.opportunities_count or 0,
        }
        for s in snapshots
    ]
    return {"source": "audit_snapshots", "data": fallback}


# ══════════════════════════════════════════════════════════════════════
#  POST /generate — Full report generation with historical storage
# ══════════════════════════════════════════════════════════════════════

@router.post("/generate")
async def generate_report(
    payload: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a performance report:
    1. Try MCP API for fresh date-specific data
    2. Store results in campaign_performance_daily + account_performance_daily
    3. If no MCP data, query historical daily tables
    4. Final fallback: cached campaigns table
    5. Optionally compute comparison period
    """
    cred = await _get_cred(db, payload.credential_id)
    logger.info("Reports generate: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)
    currency_code = await _resolve_currency(db, cred)

    start_d, end_d, period_label = _resolve_date_range(
        payload.preset or "this_month",
        payload.start_date,
        payload.end_date,
    )
    start_date = start_d
    end_date = end_d
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    preset = payload.preset if payload.preset in DATE_PRESETS else "this_month"

    campaigns_data = None  # None = not resolved yet, [] = resolved but empty
    daily_trend = []
    mcp_report_raw = {}
    report_source = "database"
    report_pending_id = None  # Track pending report ID for frontend UX

    # For single-day ranges, use plain ISO date; for multi-day, use range key
    is_single_day = start_str == end_str
    storage_key = start_str if is_single_day else f"{start_str}__{end_str}"
    range_key = f"{start_str}__{end_str}"   # Keep for legacy compat

    # ── Step 0: Ensure Campaign table has metadata (state, type, budget)
    camp_count_query = select(func.count(Campaign.id)).where(Campaign.credential_id == cred.id)
    if cred.profile_id is not None:
        camp_count_query = camp_count_query.where(Campaign.profile_id == cred.profile_id)
    else:
        camp_count_query = camp_count_query.where(Campaign.profile_id.is_(None))
    camp_count_result = await db.execute(camp_count_query)
    if (camp_count_result.scalar() or 0) == 0:
        try:
            client = await get_mcp_client_with_fresh_token(cred, db)
            raw_campaigns = await client.query_campaigns()
            from app.services.reporting_service import sync_campaigns_to_db
            await sync_campaigns_to_db(db, cred.id, raw_campaigns, profile_id=cred.profile_id)
            logger.info("Auto-synced campaigns to Campaign table during report generation")
        except Exception as e:
            logger.warning(f"Campaign auto-sync failed: {e}")

    # ── Step 1: Try MCP report first (user clicked Generate — get fresh data) ──
    try:
        client = await get_mcp_client_with_fresh_token(cred, db)
        adv_account_id = await _resolve_advertiser_account_id(db, cred)
        service = ReportingService(client, advertiser_account_id=adv_account_id)

        # Check for a pending report from a previous request — match by date range
        # so we don't accidentally resume a report for a different period
        pending_id = None
        pending_result = await db.execute(
            select(Report).where(
                Report.credential_id == cred.id,
                Report.status == "pending_mcp",
                Report.date_range_start == start_str,
                Report.date_range_end == end_str,
            ).order_by(Report.created_at.desc()).limit(1)
        )
        pending = pending_result.scalar_one_or_none()
        if pending and pending.raw_response:
            pending_id = pending.raw_response.get("mcp_report_id")
            logger.info(f"Resuming pending report {pending_id} for {start_str}–{end_str}")

        mcp_result = await service.generate_mcp_report(
            start_str, end_str, pending_report_id=pending_id, max_wait=150,
        )

        # If report returned a pending ID, save it for next time
        if mcp_result.get("_pending_report_id"):
            pending_report_id = mcp_result["_pending_report_id"]
            logger.info(f"Saving pending MCP report ID: {pending_report_id}")
            if pending:
                pending.raw_response = {"mcp_report_id": pending_report_id}
            else:
                pending_entry = Report(
                    credential_id=cred.id,
                    report_type="performance",
                    ad_product="ALL",
                    date_range_start=start_str,
                    date_range_end=end_str,
                    status="pending_mcp",
                    raw_response={"mcp_report_id": pending_report_id},
                )
                db.add(pending_entry)
            # Flush so the pending report is saved even if we fall through
            await db.flush()
            report_pending_id = pending_report_id

        parsed = service.parse_report_campaigns(mcp_result)
        # "campaigns" key present means MCP completed (even if 0 rows = no data for range)
        mcp_had_campaigns_key = "campaigns" in mcp_result
        if parsed or mcp_had_campaigns_key:
            campaigns_data = parsed or []  # empty list is a valid "no data" result
            mcp_report_raw = mcp_result
            report_source = "amazon_ads_api"
            logger.info(f"MCP returned {len(campaigns_data)} campaigns for {start_str} to {end_str}")

            # Merge campaign state/type from Campaign table (reports don't include state)
            camp_ids = [c["campaign_id"] for c in campaigns_data if c.get("campaign_id")]
            if camp_ids:
                camp_meta_query = select(Campaign).where(
                    Campaign.credential_id == cred.id,
                    Campaign.amazon_campaign_id.in_(camp_ids),
                )
                if cred.profile_id is not None:
                    camp_meta_query = camp_meta_query.where(Campaign.profile_id == cred.profile_id)
                else:
                    camp_meta_query = camp_meta_query.where(Campaign.profile_id.is_(None))
                camp_result = await db.execute(camp_meta_query)
                state_map = {}
                for c in camp_result.scalars().all():
                    state_map[c.amazon_campaign_id] = {
                        "state": c.state,
                        "campaign_type": c.campaign_type,
                        "targeting_type": c.targeting_type,
                        "daily_budget": c.daily_budget,
                    }
                for c in campaigns_data:
                    meta = state_map.get(c.get("campaign_id"), {})
                    if not c.get("state"):
                        c["state"] = meta.get("state", "")
                    if not c.get("campaign_type"):
                        c["campaign_type"] = meta.get("campaign_type", "")
                    if not c.get("targeting_type"):
                        c["targeting_type"] = meta.get("targeting_type", "")
                    if not c.get("daily_budget"):
                        c["daily_budget"] = meta.get("daily_budget", 0)

            # Mark pending report as completed
            if pending:
                pending.status = "completed"
                pending.completed_at = utcnow()

            # Persist to daily tables (only if there's data)
            if campaigns_data:
                # Store under proper key: plain date for single-day, range key for multi-day
                await store_campaign_daily_data(
                    db, cred.id, campaigns_data, storage_key, source="mcp_report", profile_id=cred.profile_id
                )
                await store_account_daily_summary(
                    db, cred.id, campaigns_data, storage_key, source="mcp_report", profile_id=cred.profile_id
                )
                logger.info(f"Stored {len(campaigns_data)} campaign rows for key '{storage_key}' from MCP")
    except Exception as e:
        logger.warning(f"MCP report failed, trying fallback: {e}")

    # ── Step 2: If MCP failed entirely (not resolved), check daily tables ──
    if campaigns_data is None:
        # Try 1: exact storage key match (plain date for single-day, range key for multi-day)
        cached = await query_campaign_daily(db, cred.id, storage_key, storage_key, profile_id=cred.profile_id)
        if cached:
            campaigns_data = cached
            report_source = "daily_history"
            logger.info(f"Found {len(campaigns_data)} campaigns in daily cache for key '{storage_key}'")

        # Try 2: smart range matching (encompassing or best-overlap)
        if not cached:
            encompassing = await find_encompassing_range_data(db, cred.id, start_str, end_str, profile_id=cred.profile_id)
            if encompassing:
                campaigns_data = encompassing
                report_source = "daily_history"
                logger.info(f"Found {len(campaigns_data)} campaigns from range match")

        # Try 3: date-range aggregation (single-day rows from audit, e.g. YYYY-MM-DD)
        if campaigns_data is None and not is_single_day:
            range_cached = await query_campaign_daily(db, cred.id, start_str, end_str, profile_id=cred.profile_id)
            if range_cached:
                campaigns_data = range_cached
                report_source = "daily_history"
                logger.info(f"Found {len(campaigns_data)} campaigns from date-range aggregation")

    # ── Step 3: Fallback to cached campaigns table ────────────────────
    # Only if MCP failed entirely (campaigns_data is still None).
    # If MCP returned [] (empty), that's the real answer — no data for this range.
    if campaigns_data is None:
        camp_fallback = select(Campaign).where(Campaign.credential_id == cred.id)
        if cred.profile_id is not None:
            camp_fallback = camp_fallback.where(Campaign.profile_id == cred.profile_id)
        else:
            camp_fallback = camp_fallback.where(Campaign.profile_id.is_(None))
        camp_fallback = camp_fallback.order_by(Campaign.spend.desc().nullslast())
        result = await db.execute(camp_fallback)
        campaigns = result.scalars().all()
        campaign_list = enrich_campaigns([_campaign_to_dict(c) for c in campaigns])

        if campaign_list:
            campaigns_data = campaign_list
            report_source = "campaign_cache"
            logger.info(f"Using campaign cache fallback ({len(campaigns_data)} campaigns)")
        else:
            campaigns_data = []

    # Ensure campaigns_data is always a list at this point
    campaigns_data = campaigns_data or []

    # Get daily trend for the period
    daily_trend = await query_account_daily_trend(
        db, cred.id, start_str, end_str, profile_id=cred.profile_id
    )

    # ── Compute summary ───────────────────────────────────────────────
    summary = compute_metrics(campaigns_data)

    by_sales = sorted(campaigns_data, key=lambda x: x.get("sales", 0), reverse=True)
    by_acos_worst = sorted(
        [c for c in campaigns_data if c.get("spend", 0) > 0],
        key=lambda x: x.get("acos", 0),
        reverse=True,
    )

    type_breakdown = {}
    for c in campaigns_data:
        t = c.get("targeting_type") or c.get("campaign_type") or "other"
        if t not in type_breakdown:
            type_breakdown[t] = {"spend": 0, "sales": 0, "campaigns": 0}
        type_breakdown[t]["spend"] += c.get("spend", 0)
        type_breakdown[t]["sales"] += c.get("sales", 0)
        type_breakdown[t]["campaigns"] += 1

    state_breakdown = {}
    for c in campaigns_data:
        s = (c.get("state") or "unknown").lower()
        if s not in state_breakdown:
            state_breakdown[s] = {"count": 0, "spend": 0, "sales": 0}
        state_breakdown[s]["count"] += 1
        state_breakdown[s]["spend"] += c.get("spend", 0)
        state_breakdown[s]["sales"] += c.get("sales", 0)

    response = {
        "period": {
            "start_date": start_str,
            "end_date": end_str,
            "preset": preset,
            "label": period_label,
        },
        "summary": summary,
        "campaigns": campaigns_data,
        "top_performers": by_sales[:5],
        "worst_performers": by_acos_worst[:5],
        "type_breakdown": type_breakdown,
        "state_breakdown": state_breakdown,
        "daily_trend": daily_trend,
        "report_source": report_source,
        "currency_code": currency_code,
        "generated_at": utcnow().isoformat(),
        "report_pending": report_pending_id is not None,
        "report_pending_id": report_pending_id,
    }

    # ── Comparison period ─────────────────────────────────────────────
    if payload.compare:
        if payload.start_date and payload.end_date:
            comp_start, comp_end = get_comparison_range_for_dates(start_date, end_date)
        else:
            comp_start, comp_end = get_comparison_range(preset)
        comp_start_str = comp_start.isoformat()
        comp_end_str = comp_end.isoformat()
        comp_campaigns = []
        comp_source = "database"
        comp_daily_trend = []

        comp_is_single_day = comp_start_str == comp_end_str
        comp_storage_key = comp_start_str if comp_is_single_day else f"{comp_start_str}__{comp_end_str}"
        comp_range_key = f"{comp_start_str}__{comp_end_str}"

        # Try MCP first for comparison period too
        try:
            if report_source == "amazon_ads_api":
                comp_result = await service.generate_mcp_report(comp_start_str, comp_end_str)
                comp_parsed = service.parse_report_campaigns(comp_result)
                if comp_parsed:
                    comp_campaigns = comp_parsed
                    comp_source = "amazon_ads_api"
                    await store_campaign_daily_data(
                        db, cred.id, comp_campaigns, comp_storage_key, source="mcp_report", profile_id=cred.profile_id
                    )
                    await store_account_daily_summary(
                        db, cred.id, comp_campaigns, comp_storage_key, source="mcp_report", profile_id=cred.profile_id
                    )
        except Exception as e:
            logger.warning(f"Comparison MCP report failed: {e}")

        # Fallback to cached daily tables
        if not comp_campaigns:
            # Try exact storage key first
            comp_campaigns = await query_campaign_daily(
                db, cred.id, comp_storage_key, comp_storage_key, profile_id=cred.profile_id
            )
            # Try date range query
            if not comp_campaigns:
                comp_campaigns = await query_campaign_daily(
                    db, cred.id, comp_start_str, comp_end_str, profile_id=cred.profile_id
                )
            # Try encompassing range
            if not comp_campaigns:
                comp_campaigns = await find_encompassing_range_data(
                    db, cred.id, comp_start_str, comp_end_str, profile_id=cred.profile_id
                )
            if comp_campaigns:
                comp_source = "daily_history"

        if not comp_campaigns:
            comp_campaigns = campaigns_data
            comp_source = "fallback_same_period"

        comp_summary = compute_metrics(comp_campaigns)
        deltas = compute_deltas(summary, comp_summary)

        comp_daily_trend = await query_account_daily_trend(
            db, cred.id, comp_start_str, comp_end_str, profile_id=cred.profile_id
        )

        response["comparison"] = {
            "period": {
                "start_date": comp_start_str,
                "end_date": comp_end_str,
                "label": f"Previous ({comp_start_str} to {comp_end_str})",
            },
            "summary": comp_summary,
            "deltas": deltas,
            "daily_trend": comp_daily_trend,
            "source": comp_source,
        }

    # ── Save to reports table ─────────────────────────────────────────
    try:
        report = Report(
            credential_id=cred.id,
            report_type="performance",
            ad_product="ALL",
            date_range_start=start_str,
            date_range_end=end_str,
            status="completed",
            report_data=response,
            raw_response=mcp_report_raw or None,
            completed_at=utcnow(),
        )
        db.add(report)

        db.add(ActivityLog(
            credential_id=cred.id,
            action="report_generated",
            category="audit",
            description=f"Performance report: {preset} ({start_str} – {end_str})"
                        + (f" with comparison" if payload.compare else "")
                        + f" | {len(campaigns_data)} campaigns | source={report_source}",
            entity_type="report",
            entity_id=str(report.id),
            details={
                "preset": preset,
                "start_date": start_str,
                "end_date": end_str,
                "campaigns_count": len(campaigns_data),
                "report_source": report_source,
                "compare": payload.compare,
                "total_spend": summary.get("spend", 0),
                "total_sales": summary.get("sales", 0),
            },
        ))
    except Exception as e:
        logger.warning(f"Failed to persist report record: {e}")

    return response


# ══════════════════════════════════════════════════════════════════════
#  GET /history — Previously generated reports
# ══════════════════════════════════════════════════════════════════════

@router.get("/history")
async def report_history(
    credential_id: Optional[str] = Query(None),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
):
    """List previously generated reports."""
    cred = await _get_cred(db, credential_id)

    result = await db.execute(
        select(Report)
        .where(Report.credential_id == cred.id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    reports = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "report_type": r.report_type,
            "ad_product": r.ad_product,
            "date_range_start": r.date_range_start,
            "date_range_end": r.date_range_end,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in reports
    ]


@router.get("/history/{report_id}")
async def get_report_detail(report_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieve a previously generated report with full data."""
    result = await db.execute(
        select(Report).where(Report.id == parse_uuid(report_id, "report_id"))
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "id": str(report.id),
        "report_type": report.report_type,
        "ad_product": report.ad_product,
        "date_range_start": report.date_range_start,
        "date_range_end": report.date_range_end,
        "status": report.status,
        "report_data": report.report_data,
        "created_at": report.created_at.isoformat(),
        "completed_at": report.completed_at.isoformat() if report.completed_at else None,
    }


# ══════════════════════════════════════════════════════════════════════
#  SEARCH TERM REPORTS — Sync and query search term data
# ══════════════════════════════════════════════════════════════════════

class SearchTermSyncRequest(BaseModel):
    credential_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    ad_product: str = "SPONSORED_PRODUCTS"
    pending_report_id: Optional[str] = None


@router.post("/search-terms/sync")
async def sync_search_terms(
    payload: SearchTermSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger a search term report sync from Amazon Ads.
    Creates the report, polls for completion, downloads data, and stores it.
    Reports can take 30-120+ seconds — if still processing, returns a
    pending_report_id that can be passed on the next call to resume.
    """
    cred = await _get_cred(db, payload.credential_id)

    # Get MCP client with fresh token
    client = await get_mcp_client_with_fresh_token(cred, db)
    if not client:
        raise HTTPException(status_code=503, detail="Could not create MCP client. Check credentials.")

    # Resolve advertiser account ID — must use the same resolver as generate_report
    advertiser_account_id = await _resolve_advertiser_account_id(db, cred)
    logger.info(f"Search term sync using advertiser_account_id: {advertiser_account_id}")

    service = SearchTermService(client, advertiser_account_id)
    result = await service.sync_search_terms(
        db=db,
        credential_id=cred.id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        ad_product=payload.ad_product,
        pending_report_id=payload.pending_report_id,
        profile_id=cred.profile_id,
    )

    # Log activity
    db.add(ActivityLog(
        credential_id=cred.id,
        action="search_term_sync",
        category="reporting",
        description=f"Search term sync: {result.get('status', '?')} — {result.get('rows_stored', 0)} rows",
        details=result,
    ))
    await db.flush()

    return result


@router.get("/search-terms")
async def get_search_terms(
    credential_id: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None),
    min_clicks: int = Query(0),
    non_converting_only: bool = Query(False),
    limit: int = Query(100),
    sort_by: str = Query("cost"),
    db: AsyncSession = Depends(get_db),
):
    """
    Query stored search term data with filters.
    Useful for viewing search terms in the UI or exporting.
    """
    cred = await _get_cred(db, credential_id)
    logger.info("Reports search-terms: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)

    query = select(SearchTermPerformance).where(SearchTermPerformance.credential_id == cred.id)
    if cred.profile_id is not None:
        query = query.where(SearchTermPerformance.profile_id == cred.profile_id)
    else:
        query = query.where(SearchTermPerformance.profile_id.is_(None))

    if campaign_id:
        query = query.where(SearchTermPerformance.amazon_campaign_id == campaign_id)
    if min_clicks > 0:
        query = query.where(SearchTermPerformance.clicks >= min_clicks)
    if non_converting_only:
        query = query.where(SearchTermPerformance.clicks > 0)
        query = query.where(SearchTermPerformance.purchases == 0)

    # Sort — whitelist allowed columns to prevent attribute probing
    _ALLOWED_SORT_COLS = {
        "cost", "clicks", "impressions", "purchases", "sales",
        "acos", "roas", "ctr", "cpc", "units_sold",
    }
    if sort_by not in _ALLOWED_SORT_COLS:
        sort_by = "cost"
    sort_col = getattr(SearchTermPerformance, sort_by, SearchTermPerformance.cost)
    query = query.order_by(sort_col.desc()).limit(limit)

    result = await db.execute(query)
    terms = result.scalars().all()

    return {
        "total": len(terms),
        "search_terms": [
            {
                "search_term": t.search_term,
                "keyword": t.keyword,
                "match_type": t.match_type,
                "keyword_type": t.keyword_type,
                "campaign_name": t.campaign_name,
                "ad_group_name": t.ad_group_name,
                "impressions": t.impressions or 0,
                "clicks": t.clicks or 0,
                "cost": t.cost or 0,
                "purchases": t.purchases or 0,
                "sales": t.sales or 0,
                "acos": t.acos,
                "roas": t.roas,
                "ctr": t.ctr,
                "cpc": t.cpc,
                "date_range": f"{t.report_date_start} to {t.report_date_end}",
            }
            for t in terms
        ],
    }


@router.get("/search-terms/summary")
async def search_terms_summary(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get a summary of stored search term data for the account."""
    cred = await _get_cred(db, credential_id)
    logger.info("Reports search-terms/summary: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)
    summary = await get_search_term_summary(db, cred.id, profile_id=cred.profile_id)
    return summary
