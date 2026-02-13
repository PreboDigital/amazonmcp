"""
Reporting Service — Generates performance reports from historical daily data,
cached DB campaigns, and the Amazon Ads MCP API.
Supports date-range presets, comparison periods, and stores all data for
historical tracking.
"""

import gzip
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
import httpx
from app.mcp_client import AmazonAdsMCP
from app.models import (
    CampaignPerformanceDaily, AccountPerformanceDaily,
    Campaign, Credential,
)

logger = logging.getLogger(__name__)

DATE_PRESETS = (
    "today", "yesterday", "last_7_days", "this_week", "last_week",
    "last_30_days", "this_month", "last_month", "year_to_date",
)

# ── Marketplace → Currency mapping ─────────────────────────────────────
MARKETPLACE_CURRENCY = {
    # North America
    "US": "USD", "CA": "CAD", "MX": "MXN", "BR": "BRL",
    # Europe
    "GB": "GBP", "UK": "GBP",
    "DE": "EUR", "FR": "EUR", "ES": "EUR", "IT": "EUR",
    "NL": "EUR", "BE": "EUR", "IE": "EUR", "AT": "EUR",
    "PT": "EUR", "FI": "EUR", "LU": "EUR",
    "SE": "SEK", "PL": "PLN", "TR": "TRY",
    # Asia-Pacific
    "JP": "JPY", "AU": "AUD", "IN": "INR", "SG": "SGD",
    # Middle East & Africa
    "AE": "AED", "SA": "SAR", "EG": "EGP", "ZA": "ZAR",
}

# Region-level fallback when marketplace is not known
REGION_CURRENCY = {
    "na": "USD",
    "eu": "GBP",
    "fe": "JPY",
}


def get_currency_for_marketplace(marketplace: str = None, region: str = None) -> str:
    """
    Resolve the currency code for a marketplace or region.
    Priority: marketplace-specific mapping > region fallback > USD default.
    """
    if marketplace:
        code = MARKETPLACE_CURRENCY.get(marketplace.upper())
        if code:
            return code
    if region:
        code = REGION_CURRENCY.get(region.lower())
        if code:
            return code
    return "USD"


# ── Date-range helpers ────────────────────────────────────────────────

def get_date_range(preset: str) -> Tuple[date, date]:
    """Return (start, end) dates for a named preset."""
    today = date.today()

    if preset == "today":
        return today, today
    elif preset == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    elif preset == "last_7_days":
        return today - timedelta(days=6), today
    elif preset == "this_week":
        monday = today - timedelta(days=today.weekday())
        return monday, today
    elif preset == "last_week":
        monday = today - timedelta(days=today.weekday() + 7)
        sunday = monday + timedelta(days=6)
        return monday, sunday
    elif preset == "last_30_days":
        # Match Amazon Ads dashboard: 31 days (today - 30 through today)
        return today - timedelta(days=30), today
    elif preset == "this_month":
        return today.replace(day=1), today
    elif preset == "last_month":
        first_this = today.replace(day=1)
        last_day_prev = first_this - timedelta(days=1)
        first_prev = last_day_prev.replace(day=1)
        return first_prev, last_day_prev
    elif preset == "year_to_date":
        return today.replace(month=1, day=1), today
    else:
        return today - timedelta(days=7), today


def get_comparison_range(preset: str) -> Tuple[date, date]:
    """Return the comparison period for the given preset."""
    today = date.today()

    if preset == "today":
        d = today - timedelta(days=1)
        return d, d
    elif preset == "yesterday":
        d = today - timedelta(days=2)
        return d, d
    elif preset == "last_7_days":
        return today - timedelta(days=13), today - timedelta(days=7)
    elif preset == "this_week":
        return get_date_range("last_week")
    elif preset == "last_week":
        monday = today - timedelta(days=today.weekday() + 14)
        sunday = monday + timedelta(days=6)
        return monday, sunday
    elif preset == "last_30_days":
        # Previous 31-day period
        return today - timedelta(days=61), today - timedelta(days=31)
    elif preset == "this_month":
        return get_date_range("last_month")
    elif preset == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        month_before_last = first_prev - timedelta(days=1)
        first_month_before = month_before_last.replace(day=1)
        return first_month_before, month_before_last
    elif preset == "year_to_date":
        return today.replace(year=today.year - 1, month=1, day=1), today.replace(year=today.year - 1, month=12, day=31)
    else:
        start, end = get_date_range(preset)
        duration = (end - start).days + 1
        return start - timedelta(days=duration), start - timedelta(days=1)


def get_comparison_range_for_dates(start_date: date, end_date: date) -> Tuple[date, date]:
    """For custom date ranges, return the previous period of same duration."""
    duration = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=duration - 1)
    return prev_start, prev_end


# ── Metric computation ────────────────────────────────────────────────

def compute_metrics(campaigns: list) -> dict:
    """Aggregate metrics across a list of campaign dicts."""
    total_spend = sum(float(c.get("spend") or 0) for c in campaigns)
    total_sales = sum(float(c.get("sales") or 0) for c in campaigns)
    total_impressions = sum(int(c.get("impressions") or 0) for c in campaigns)
    total_clicks = sum(int(c.get("clicks") or 0) for c in campaigns)
    total_orders = sum(int(c.get("orders") or 0) for c in campaigns)

    acos = (total_spend / total_sales * 100) if total_sales > 0 else 0
    roas = (total_sales / total_spend) if total_spend > 0 else 0
    ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0
    cpc = (total_spend / total_clicks) if total_clicks > 0 else 0
    cvr = (total_orders / total_clicks * 100) if total_clicks > 0 else 0

    return {
        "spend": round(total_spend, 2),
        "sales": round(total_sales, 2),
        "impressions": total_impressions,
        "clicks": total_clicks,
        "orders": total_orders,
        "acos": round(acos, 2),
        "roas": round(roas, 2),
        "ctr": round(ctr, 2),
        "cpc": round(cpc, 2),
        "cvr": round(cvr, 2),
    }


def compute_deltas(current: dict, previous: dict) -> dict:
    """Compute percentage change between two metric dicts."""
    deltas = {}
    for key in current:
        curr = current.get(key, 0)
        prev = previous.get(key, 0)
        if prev != 0:
            deltas[key] = round(((curr - prev) / abs(prev)) * 100, 1)
        else:
            deltas[key] = 0.0 if curr == 0 else 100.0
    return deltas


def enrich_campaigns(campaigns: list) -> list:
    """Add derived metrics (acos, roas, ctr, cpc, cvr) to each campaign dict."""
    enriched = []
    for c in campaigns:
        row = dict(c)
        spend = float(row.get("spend") or 0)
        sales = float(row.get("sales") or 0)
        impressions = int(row.get("impressions") or 0)
        clicks = int(row.get("clicks") or 0)
        orders = int(row.get("orders") or 0)
        row["acos"] = round(spend / sales * 100, 2) if sales > 0 else 0
        row["roas"] = round(sales / spend, 2) if spend > 0 else 0
        row["ctr"] = round(clicks / impressions * 100, 2) if impressions > 0 else 0
        row["cpc"] = round(spend / clicks, 2) if clicks > 0 else 0
        row["cvr"] = round(orders / clicks * 100, 2) if clicks > 0 else 0
        enriched.append(row)
    return enriched


# ══════════════════════════════════════════════════════════════════════
#  CAMPAIGN SYNC — Persist campaign metadata from MCP query results
# ══════════════════════════════════════════════════════════════════════

async def sync_campaigns_to_db(
    db: AsyncSession,
    credential_id: uuid.UUID,
    campaigns_data: dict,
    profile_id: Optional[str] = None,
):
    """
    Upsert Campaign table rows from query_campaigns MCP results.
    Extracts campaign ID, name, state, type, budget etc.
    """
    # Extract list from various MCP response formats (inline to avoid circular import)
    campaign_list = campaigns_data
    if isinstance(campaigns_data, dict):
        for key in ("campaigns", "adGroups", "targets", "ads", "result", "results", "items"):
            if key in campaigns_data and isinstance(campaigns_data[key], list):
                campaign_list = campaigns_data[key]
                break
    elif not isinstance(campaigns_data, list):
        campaign_list = []
    synced = 0

    for camp_data in campaign_list:
        amazon_id = (
            camp_data.get("campaignId") or camp_data.get("id")
            or camp_data.get("campaign_id")
        )
        if not amazon_id:
            continue

        lookup = [
            Campaign.credential_id == credential_id,
            Campaign.amazon_campaign_id == str(amazon_id),
        ]
        if profile_id is not None:
            lookup.append(Campaign.profile_id == profile_id)
        else:
            lookup.append(Campaign.profile_id.is_(None))
        result = await db.execute(select(Campaign).where(and_(*lookup)))
        campaign = result.scalar_one_or_none()

        camp_name = camp_data.get("name") or camp_data.get("campaignName") or camp_data.get("campaign_name")
        camp_type = camp_data.get("adProduct") or camp_data.get("campaignType") or camp_data.get("type")
        targeting = camp_data.get("targetingType") or camp_data.get("targeting")
        if not targeting and camp_data.get("autoCreationSettings"):
            auto_targets = camp_data["autoCreationSettings"].get("autoCreateTargets", False)
            targeting = "auto" if auto_targets else "manual"
        state = camp_data.get("state") or camp_data.get("status")

        budget = camp_data.get("dailyBudget") or camp_data.get("budget")
        if not budget and camp_data.get("budgets"):
            for b in camp_data["budgets"]:
                if b.get("recurrenceTimePeriod") == "DAILY":
                    mv = b.get("budgetValue", {}).get("monetaryBudgetValue", {}).get("monetaryBudget", {})
                    budget = mv.get("value")
                    break

        if campaign:
            campaign.profile_id = profile_id
            campaign.campaign_name = camp_name or campaign.campaign_name
            campaign.campaign_type = camp_type or campaign.campaign_type
            campaign.targeting_type = targeting or campaign.targeting_type
            campaign.state = state or campaign.state
            campaign.daily_budget = float(budget) if budget else campaign.daily_budget
            campaign.start_date = camp_data.get("startDate") or camp_data.get("startDateTime") or campaign.start_date
            campaign.end_date = camp_data.get("endDate") or camp_data.get("endDateTime") or campaign.end_date
            campaign.raw_data = camp_data
            campaign.synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            new_campaign = Campaign(
                credential_id=credential_id,
                profile_id=profile_id,
                amazon_campaign_id=str(amazon_id),
                campaign_name=camp_name,
                campaign_type=camp_type,
                targeting_type=targeting,
                state=state,
                daily_budget=float(budget) if budget else None,
                start_date=camp_data.get("startDate") or camp_data.get("startDateTime"),
                end_date=camp_data.get("endDate") or camp_data.get("endDateTime"),
                raw_data=camp_data,
            )
            db.add(new_campaign)
        synced += 1

    await db.flush()
    logger.info(f"Synced {synced} campaigns to Campaign table")
    return synced


# ══════════════════════════════════════════════════════════════════════
#  HISTORICAL DATA — Store & Query daily performance from DB
# ══════════════════════════════════════════════════════════════════════

async def store_campaign_daily_data(
    db: AsyncSession,
    credential_id: uuid.UUID,
    campaigns: list,
    report_date: str,
    source: str = "mcp_report",
    profile_id: Optional[str] = None,
):
    """
    Upsert campaign performance rows for a given date.
    If a row already exists for (credential, campaign, date), update it.
    """
    stored = 0
    for c in campaigns:
        campaign_id = c.get("campaign_id") or c.get("amazon_campaign_id") or ""
        if not campaign_id:
            continue

        # Check for existing row
        lookup = [
            CampaignPerformanceDaily.credential_id == credential_id,
            CampaignPerformanceDaily.amazon_campaign_id == campaign_id,
            CampaignPerformanceDaily.date == report_date,
        ]
        if profile_id is not None:
            lookup.append(CampaignPerformanceDaily.profile_id == profile_id)
        else:
            lookup.append(CampaignPerformanceDaily.profile_id.is_(None))
        result = await db.execute(
            select(CampaignPerformanceDaily).where(and_(*lookup))
        )
        existing = result.scalar_one_or_none()

        spend = float(c.get("spend") or 0)
        sales = float(c.get("sales") or 0)
        impressions = int(c.get("impressions") or 0)
        clicks = int(c.get("clicks") or 0)
        orders = int(c.get("orders") or 0)
        acos = round(spend / sales * 100, 2) if sales > 0 else 0
        roas = round(sales / spend, 2) if spend > 0 else 0
        ctr = round(clicks / impressions * 100, 2) if impressions > 0 else 0
        cpc = round(spend / clicks, 2) if clicks > 0 else 0
        cvr = round(orders / clicks * 100, 2) if clicks > 0 else 0

        if existing:
            existing.spend = spend
            existing.sales = sales
            existing.impressions = impressions
            existing.clicks = clicks
            existing.orders = orders
            existing.acos = acos
            existing.roas = roas
            existing.ctr = ctr
            existing.cpc = cpc
            existing.cvr = cvr
            existing.campaign_name = c.get("campaign_name") or existing.campaign_name
            existing.state = c.get("state") or existing.state
            existing.daily_budget = c.get("daily_budget") or existing.daily_budget
            existing.source = source
            existing.synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            row = CampaignPerformanceDaily(
                credential_id=credential_id,
                profile_id=profile_id,
                amazon_campaign_id=campaign_id,
                campaign_name=c.get("campaign_name"),
                campaign_type=c.get("campaign_type"),
                targeting_type=c.get("targeting_type"),
                state=c.get("state"),
                date=report_date,
                spend=spend,
                sales=sales,
                impressions=impressions,
                clicks=clicks,
                orders=orders,
                acos=acos,
                roas=roas,
                ctr=ctr,
                cpc=cpc,
                cvr=cvr,
                daily_budget=c.get("daily_budget"),
                source=source,
            )
            db.add(row)
        stored += 1

    await db.flush()
    logger.info(f"Stored {stored} campaign daily rows for {report_date}")
    return stored


async def store_account_daily_summary(
    db: AsyncSession,
    credential_id: uuid.UUID,
    campaigns: list,
    report_date: str,
    source: str = "mcp_report",
    profile_id: Optional[str] = None,
):
    """
    Compute and upsert the account-level aggregate row for a given date.
    """
    metrics = compute_metrics(campaigns)
    active = len([c for c in campaigns if (c.get("state") or "").lower() in ("enabled", "active")])
    paused = len([c for c in campaigns if (c.get("state") or "").lower() == "paused"])

    lookup = [
        AccountPerformanceDaily.credential_id == credential_id,
        AccountPerformanceDaily.date == report_date,
    ]
    if profile_id is not None:
        lookup.append(AccountPerformanceDaily.profile_id == profile_id)
    else:
        lookup.append(AccountPerformanceDaily.profile_id.is_(None))
    result = await db.execute(
        select(AccountPerformanceDaily).where(and_(*lookup))
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.total_spend = metrics["spend"]
        existing.total_sales = metrics["sales"]
        existing.total_impressions = metrics["impressions"]
        existing.total_clicks = metrics["clicks"]
        existing.total_orders = metrics["orders"]
        existing.avg_acos = metrics["acos"]
        existing.avg_roas = metrics["roas"]
        existing.avg_ctr = metrics["ctr"]
        existing.avg_cpc = metrics["cpc"]
        existing.avg_cvr = metrics["cvr"]
        existing.total_campaigns = len(campaigns)
        existing.active_campaigns = active
        existing.paused_campaigns = paused
        existing.source = source
        existing.synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        row = AccountPerformanceDaily(
            credential_id=credential_id,
            profile_id=profile_id,
            date=report_date,
            total_spend=metrics["spend"],
            total_sales=metrics["sales"],
            total_impressions=metrics["impressions"],
            total_clicks=metrics["clicks"],
            total_orders=metrics["orders"],
            avg_acos=metrics["acos"],
            avg_roas=metrics["roas"],
            avg_ctr=metrics["ctr"],
            avg_cpc=metrics["cpc"],
            avg_cvr=metrics["cvr"],
            total_campaigns=len(campaigns),
            active_campaigns=active,
            paused_campaigns=paused,
            source=source,
        )
        db.add(row)

    await db.flush()
    logger.info(f"Stored account daily summary for {report_date}")


async def persist_campaign_daily(
    db: AsyncSession,
    credential_id: uuid.UUID,
    campaigns: list,
    report_date: str = None,
    profile_id: Optional[str] = None,
):
    """
    Convenience wrapper: store campaign + account daily data.
    If report_date is not provided, uses today's date.
    """
    from datetime import date as date_type
    if not report_date:
        report_date = date_type.today().isoformat()
    await store_campaign_daily_data(db, credential_id, campaigns, report_date, source="audit", profile_id=profile_id)
    await store_account_daily_summary(db, credential_id, campaigns, report_date, source="audit", profile_id=profile_id)


async def query_campaign_daily(
    db: AsyncSession,
    credential_id: uuid.UUID,
    start_date: str,
    end_date: str,
    profile_id: Optional[str] = None,
) -> list:
    """
    Query campaign_performance_daily for a date range and return
    aggregated per-campaign metrics (summed across days).
    Cross-references Campaign table for state/type data that reports don't include.

    IMPORTANT: Never mix range keys (e.g. "2026-01-15__2026-02-13") with single-day
    keys (e.g. "2026-02-13") in the same query — that would double-count metrics.
    For range queries, prefer exact range key match; otherwise use single-day rows only.
    """
    base_where = [
        CampaignPerformanceDaily.credential_id == credential_id,
        CampaignPerformanceDaily.profile_id == profile_id
        if profile_id is not None
        else CampaignPerformanceDaily.profile_id.is_(None),
    ]

    # Range query: prefer exact range key to avoid mixing with single-day rows
    if start_date != end_date:
        range_key = f"{start_date}__{end_date}"
        exact_result = await db.execute(
            select(
                CampaignPerformanceDaily.amazon_campaign_id,
                func.max(CampaignPerformanceDaily.campaign_name).label("campaign_name"),
                func.max(CampaignPerformanceDaily.campaign_type).label("campaign_type"),
                func.max(CampaignPerformanceDaily.targeting_type).label("targeting_type"),
                func.max(CampaignPerformanceDaily.state).label("state"),
                func.sum(CampaignPerformanceDaily.spend).label("spend"),
                func.sum(CampaignPerformanceDaily.sales).label("sales"),
                func.sum(CampaignPerformanceDaily.impressions).label("impressions"),
                func.sum(CampaignPerformanceDaily.clicks).label("clicks"),
                func.sum(CampaignPerformanceDaily.orders).label("orders"),
                func.max(CampaignPerformanceDaily.daily_budget).label("daily_budget"),
            )
            .where(and_(*base_where, CampaignPerformanceDaily.date == range_key))
            .group_by(CampaignPerformanceDaily.amazon_campaign_id)
            .order_by(func.sum(CampaignPerformanceDaily.spend).desc())
        )
        exact_rows = exact_result.all()
        if exact_rows:
            rows = exact_rows
        else:
            # No exact range key — use single-day rows only (exclude range keys to avoid double-count)
            range_where = base_where + [
                CampaignPerformanceDaily.date >= start_date,
                CampaignPerformanceDaily.date <= end_date,
                func.strpos(CampaignPerformanceDaily.date, "__") <= 0,  # single-day only
            ]
            result = await db.execute(
                select(
                    CampaignPerformanceDaily.amazon_campaign_id,
                    func.max(CampaignPerformanceDaily.campaign_name).label("campaign_name"),
                    func.max(CampaignPerformanceDaily.campaign_type).label("campaign_type"),
                    func.max(CampaignPerformanceDaily.targeting_type).label("targeting_type"),
                    func.max(CampaignPerformanceDaily.state).label("state"),
                    func.sum(CampaignPerformanceDaily.spend).label("spend"),
                    func.sum(CampaignPerformanceDaily.sales).label("sales"),
                    func.sum(CampaignPerformanceDaily.impressions).label("impressions"),
                    func.sum(CampaignPerformanceDaily.clicks).label("clicks"),
                    func.sum(CampaignPerformanceDaily.orders).label("orders"),
                    func.max(CampaignPerformanceDaily.daily_budget).label("daily_budget"),
                )
                .where(and_(*range_where))
                .group_by(CampaignPerformanceDaily.amazon_campaign_id)
                .order_by(func.sum(CampaignPerformanceDaily.spend).desc())
            )
            rows = result.all()
    else:
        # Single-day or exact key match — use exact date to avoid mixing with range keys
        result = await db.execute(
            select(
                CampaignPerformanceDaily.amazon_campaign_id,
                func.max(CampaignPerformanceDaily.campaign_name).label("campaign_name"),
                func.max(CampaignPerformanceDaily.campaign_type).label("campaign_type"),
                func.max(CampaignPerformanceDaily.targeting_type).label("targeting_type"),
                func.max(CampaignPerformanceDaily.state).label("state"),
                func.sum(CampaignPerformanceDaily.spend).label("spend"),
                func.sum(CampaignPerformanceDaily.sales).label("sales"),
                func.sum(CampaignPerformanceDaily.impressions).label("impressions"),
                func.sum(CampaignPerformanceDaily.clicks).label("clicks"),
                func.sum(CampaignPerformanceDaily.orders).label("orders"),
                func.max(CampaignPerformanceDaily.daily_budget).label("daily_budget"),
            )
            .where(and_(*base_where, CampaignPerformanceDaily.date == start_date))
            .group_by(CampaignPerformanceDaily.amazon_campaign_id)
            .order_by(func.sum(CampaignPerformanceDaily.spend).desc())
        )
        rows = result.all()

    # Build a lookup of campaign state/type from the Campaign table
    campaign_ids = [r.amazon_campaign_id for r in rows]
    state_lookup = {}
    if campaign_ids:
        camp_lookup = [
            Campaign.credential_id == credential_id,
            Campaign.amazon_campaign_id.in_(campaign_ids),
        ]
        if profile_id is not None:
            camp_lookup.append(Campaign.profile_id == profile_id)
        else:
            camp_lookup.append(Campaign.profile_id.is_(None))
        camp_result = await db.execute(
            select(Campaign).where(and_(*camp_lookup))
        )
        for c in camp_result.scalars().all():
            state_lookup[c.amazon_campaign_id] = {
                "state": c.state,
                "campaign_type": c.campaign_type,
                "targeting_type": c.targeting_type,
                "daily_budget": c.daily_budget,
            }

    campaigns = []
    for r in rows:
        camp_meta = state_lookup.get(r.amazon_campaign_id, {})
        campaigns.append({
            "campaign_id": r.amazon_campaign_id,
            "campaign_name": r.campaign_name or "Unknown",
            "campaign_type": r.campaign_type or camp_meta.get("campaign_type"),
            "targeting_type": r.targeting_type or camp_meta.get("targeting_type"),
            "state": r.state or camp_meta.get("state", ""),
            "spend": float(r.spend or 0),
            "sales": float(r.sales or 0),
            "impressions": int(r.impressions or 0),
            "clicks": int(r.clicks or 0),
            "orders": int(r.orders or 0),
            "daily_budget": float(r.daily_budget or 0) or camp_meta.get("daily_budget", 0),
        })
    return enrich_campaigns(campaigns)


async def query_account_daily_trend(
    db: AsyncSession,
    credential_id: uuid.UUID,
    start_date: str,
    end_date: str,
    profile_id: Optional[str] = None,
) -> list:
    """
    Query account_performance_daily for a date range — one row per day,
    ideal for trend charts.
    """
    trend_where = [
        AccountPerformanceDaily.credential_id == credential_id,
        AccountPerformanceDaily.date >= start_date,
        AccountPerformanceDaily.date <= end_date,
    ]
    if profile_id is not None:
        trend_where.append(AccountPerformanceDaily.profile_id == profile_id)
    else:
        trend_where.append(AccountPerformanceDaily.profile_id.is_(None))
    result = await db.execute(
        select(AccountPerformanceDaily)
        .where(and_(*trend_where))
        .order_by(AccountPerformanceDaily.date.asc())
    )
    rows = result.scalars().all()
    return [
        {
            "date": r.date,
            "spend": r.total_spend or 0,
            "sales": r.total_sales or 0,
            "impressions": r.total_impressions or 0,
            "clicks": r.total_clicks or 0,
            "orders": r.total_orders or 0,
            "acos": r.avg_acos or 0,
            "roas": r.avg_roas or 0,
            "ctr": r.avg_ctr or 0,
            "cpc": r.avg_cpc or 0,
            "campaigns": r.total_campaigns or 0,
            "active": r.active_campaigns or 0,
        }
        for r in rows
    ]


async def find_encompassing_range_data(
    db: AsyncSession,
    credential_id: uuid.UUID,
    start_date: str,
    end_date: str,
    profile_id: Optional[str] = None,
) -> list:
    """
    Find stored campaign data from a range key that encompasses or closely
    matches the requested dates.

    Matching strategy (in priority order):
    1. Exact encompassing — stored range fully covers requested range
    2. Best overlap — stored range shares the same start and its end is
       within a few days of the requested end (handles rolling "This Month"
       advancing by 1 day each day without needing to re-generate)
    3. Sub-range contained — the requested range is entirely within a stored
       range (e.g., "yesterday" within a stored "this_month")

    Note: Returns aggregated data from the matched range — best-effort fallback
    until the user generates fresh data for the exact range.
    """
    # Use strpos for literal "__" — SQL LIKE treats _ as wildcard, so contains("__") would match incorrectly
    range_where = [
        CampaignPerformanceDaily.credential_id == credential_id,
        func.strpos(CampaignPerformanceDaily.date, "__") > 0,
    ]
    if profile_id is not None:
        range_where.append(CampaignPerformanceDaily.profile_id == profile_id)
    else:
        range_where.append(CampaignPerformanceDaily.profile_id.is_(None))
    result = await db.execute(
        select(CampaignPerformanceDaily.date).where(and_(*range_where)).distinct()
    )
    range_keys = [r[0] for r in result.all()]

    # Parse all valid range keys
    parsed_keys = []
    for rk in range_keys:
        parts = rk.split("__")
        if len(parts) == 2:
            parsed_keys.append((rk, parts[0], parts[1]))

    # Priority 1: Exact encompassing — stored range fully covers requested
    for rk, rk_start, rk_end in parsed_keys:
        if rk_start <= start_date and rk_end >= end_date:
            logger.info(f"Found encompassing range key: {rk} for requested {start_date}–{end_date}")
            return await query_campaign_daily(db, credential_id, rk, rk, profile_id=profile_id)

    # Priority 2: Best overlap — same start (or very close), end within a
    # few days of requested end.  This handles "This Month" rolling forward:
    # yesterday's "Feb 1–Feb 10" is still useful for today's "Feb 1–Feb 11".
    best_match = None
    best_overlap = 0
    try:
        req_start = date.fromisoformat(start_date)
        req_end = date.fromisoformat(end_date)
        req_days = (req_end - req_start).days + 1

        for rk, rk_start, rk_end in parsed_keys:
            try:
                rs = date.fromisoformat(rk_start)
                re_ = date.fromisoformat(rk_end)
            except ValueError:
                continue

            # Compute overlap between stored range and requested range
            overlap_start = max(rs, req_start)
            overlap_end = min(re_, req_end)
            overlap_days = (overlap_end - overlap_start).days + 1

            if overlap_days <= 0:
                continue

            # Overlap ratio: what fraction of the requested range is covered?
            overlap_ratio = overlap_days / req_days if req_days > 0 else 0

            # Accept if ≥70% overlap (handles rolling ranges like This Month)
            if overlap_ratio >= 0.7 and overlap_days > best_overlap:
                best_overlap = overlap_days
                best_match = rk
    except (ValueError, TypeError):
        pass

    if best_match:
        logger.info(
            f"Found best-match range key: {best_match} ({best_overlap} day overlap) "
            f"for requested {start_date}–{end_date}"
        )
        return await query_campaign_daily(db, credential_id, best_match, best_match, profile_id=profile_id)

    return []


async def has_daily_data(
    db: AsyncSession,
    credential_id: uuid.UUID,
    start_date: str,
    end_date: str,
    profile_id: Optional[str] = None,
) -> bool:
    """Check if we have any daily performance data for the given range."""
    has_where = [
        AccountPerformanceDaily.credential_id == credential_id,
        AccountPerformanceDaily.date >= start_date,
        AccountPerformanceDaily.date <= end_date,
    ]
    if profile_id is not None:
        has_where.append(AccountPerformanceDaily.profile_id == profile_id)
    else:
        has_where.append(AccountPerformanceDaily.profile_id.is_(None))
    result = await db.execute(
        select(func.count(AccountPerformanceDaily.id)).where(and_(*has_where))
    )
    count = result.scalar() or 0
    return count > 0


# ══════════════════════════════════════════════════════════════════════
#  MCP REPORT — Create & retrieve from Amazon Ads API
# ══════════════════════════════════════════════════════════════════════

class ReportingService:
    """Wraps the MCP client to create & retrieve Amazon Ads reports."""

    def __init__(self, client: Optional[AmazonAdsMCP] = None, advertiser_account_id: Optional[str] = None):
        self.client = client
        self.advertiser_account_id = advertiser_account_id

    async def generate_mcp_report(
        self,
        start_date: str,
        end_date: str,
        pending_report_id: Optional[str] = None,
        max_wait: int = 150,
    ) -> dict:
        """
        Create a report via the MCP API for the given date range.
        Amazon Ads reports are async (take 2-5 minutes).

        Strategy:
        1. If a pending_report_id is provided, try to retrieve it first
        2. Otherwise, create a new report
        3. Poll for completion (up to ~150s to allow Amazon enough time)
        4. If still pending, return the reportId so the caller can save it
        5. On next invocation, the caller passes the pending ID to resume

        Returns:
            dict with either {"campaigns": [...]} if data is available,
            or {"_pending_report_id": "..."} if still processing.
        """
        if not self.client:
            return {}

        try:
            report_ids = []

            # Phase 1: Check a pending report from a previous request
            if pending_report_id:
                logger.info(f"Checking pending report: {pending_report_id}")
                try:
                    check = await self.client.retrieve_report([pending_report_id])
                    status = self.client._get_report_status(check)
                    if status == "COMPLETED":
                        rows = await self._download_report_data(check)
                        if rows:
                            logger.info(f"Pending report completed: {len(rows)} rows")
                            return {"campaigns": rows}
                    elif status == "PENDING":
                        # Still pending — try polling briefly
                        report_ids = [pending_report_id]
                        logger.info("Pending report still processing, will poll briefly")
                except Exception as e:
                    logger.warning(f"Failed to check pending report: {e}")

            # Phase 2: Create a new report if no pending ID or pending failed
            if not report_ids:
                logger.info(f"MCP report requested: startDate={start_date} endDate={end_date}")
                report_config = {
                    "reports": [
                        {
                            "format": "GZIP_JSON",
                            "periods": [
                                {"datePeriod": {"startDate": start_date, "endDate": end_date}}
                            ],
                        }
                    ],
                }
                result = await self.client.create_campaign_report(
                    report_config,
                    advertiser_account_id=self.advertiser_account_id,
                )
                logger.info(f"MCP create_campaign_report response keys: {list(result.keys()) if isinstance(result, dict) else type(result)}")

                report_ids = self._extract_report_ids(result)
                logger.info(f"Extracted report IDs: {report_ids}")

            if not report_ids:
                logger.warning(f"No report IDs extracted from creation response")
                return {}

            # Phase 3: Poll for completion
            completed = await self.client.poll_report(
                report_ids, max_wait=max_wait, interval=10
            )

            # Phase 4: Download data if completed
            status = self.client._get_report_status(completed)
            if status == "COMPLETED":
                rows = await self._download_report_data(completed)
                # Return campaigns even if empty — 0 rows means no data for this
                # date range, which is a valid result (not the same as "still pending")
                logger.info(f"Report completed with {len(rows)} rows")
                return {"campaigns": rows}

            # Still pending — return the ID so caller can save and retry later
            logger.info(f"Report still {status} after polling — returning ID for later retrieval")
            return {"_pending_report_id": report_ids[0]}

        except Exception as e:
            logger.warning(f"MCP report generation failed: {e}")
            return {}

    @staticmethod
    async def _download_report_data(report_response: dict) -> list:
        """
        Download and decompress report data from completedReportParts S3 URLs.
        Returns a flat list of campaign rows.
        """
        all_rows = []
        if not isinstance(report_response, dict):
            return all_rows

        for entry in report_response.get("success", []):
            if not isinstance(entry, dict):
                continue
            report = entry.get("report", {})
            if not isinstance(report, dict):
                continue

            status = report.get("status")
            if status != "COMPLETED":
                logger.info(f"Report status is {status}, skipping download")
                continue

            parts = report.get("completedReportParts", [])
            for part in parts:
                url = part.get("url")
                if not url:
                    continue
                try:
                    async with httpx.AsyncClient(timeout=30.0) as http:
                        resp = await http.get(url)
                        resp.raise_for_status()

                    # Decompress gzip data
                    try:
                        data = gzip.decompress(resp.content)
                        text = data.decode("utf-8")
                    except Exception:
                        # Maybe not gzipped
                        text = resp.content.decode("utf-8")

                    import json
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        all_rows.extend(parsed)
                        logger.info(f"Downloaded report part: {len(parsed)} rows")
                    elif isinstance(parsed, dict):
                        # Some formats wrap rows in a key
                        for key in ("rows", "data", "campaigns", "results"):
                            if key in parsed and isinstance(parsed[key], list):
                                all_rows.extend(parsed[key])
                                break
                        else:
                            all_rows.append(parsed)
                except Exception as e:
                    logger.warning(f"Failed to download report part: {e}")

        logger.info(f"Total downloaded report rows: {len(all_rows)}")
        return all_rows

    @staticmethod
    def _extract_report_ids(result: dict) -> list:
        """Extract report IDs from various MCP response formats."""
        if not isinstance(result, dict):
            return []

        # Format 1: {"success": [{"report": {"reportId": "..."}}]}
        for entry in result.get("success", []):
            if isinstance(entry, dict):
                report = entry.get("report", {})
                if isinstance(report, dict) and "reportId" in report:
                    return [report["reportId"]]

        # Format 2: Direct reportIds
        if "reportIds" in result:
            return result["reportIds"]

        # Format 3: reports array with reportId
        if "reports" in result and isinstance(result["reports"], list):
            ids = [
                r["reportId"]
                for r in result["reports"]
                if isinstance(r, dict) and "reportId" in r
            ]
            if ids:
                return ids

        # Format 4: Single reportId
        if "reportId" in result:
            return [result["reportId"]]

        return []

    @staticmethod
    def parse_report_campaigns(report_data: dict) -> list:
        """
        Normalise MCP report data into a flat campaign list.
        Handles:
         - {"success": [{"report": {"completedReportParts": [{"url": ...}], ...}}]}
         - {"campaigns": [...]} or {"results": [...]} etc.
         - Raw list of campaign dicts
        """
        raw = []

        if isinstance(report_data, dict):
            # Handle the async report response format:
            # {"success": [{"report": {"status": "COMPLETED", ...}}]}
            for entry in report_data.get("success", []):
                if isinstance(entry, dict):
                    report = entry.get("report", {})
                    if isinstance(report, dict):
                        # completedReportParts contain download URLs or inline data
                        parts = report.get("completedReportParts", [])
                        if parts:
                            logger.info(f"Report has {len(parts)} completed parts")
                            for part in parts:
                                if isinstance(part, dict):
                                    # Parts may contain inline data or URLs
                                    part_data = part.get("data", [])
                                    if isinstance(part_data, list):
                                        raw.extend(part_data)
                                    part_rows = part.get("rows", [])
                                    if isinstance(part_rows, list):
                                        raw.extend(part_rows)
                        # Also check if report contains campaigns/rows directly
                        for key in ("campaigns", "results", "rows", "data"):
                            if key in report and isinstance(report[key], list):
                                raw.extend(report[key])
                                break

            # Fallback: direct data keys
            if not raw:
                for key in ("campaigns", "results", "rows", "data", "result", "items"):
                    if key in report_data and isinstance(report_data[key], list):
                        raw = report_data[key]
                        break

        elif isinstance(report_data, list):
            raw = report_data

        if raw:
            logger.info(f"Parsing {len(raw)} report rows. First row keys: {list(raw[0].keys()) if raw and isinstance(raw[0], dict) else 'N/A'}")
        else:
            logger.info("No report rows found to parse")

        normalised = []
        for c in raw:
            if not isinstance(c, dict):
                continue

            # Handle nested metric fields from MCP report format
            # e.g., {"campaign.id": "...", "campaign.name": "...", "metric.clicks": 5}
            campaign_id = (
                c.get("campaign.id") or c.get("campaignId")
                or c.get("campaign_id") or ""
            )
            campaign_name = (
                c.get("campaign.name") or c.get("campaignName")
                or c.get("campaign_name") or "Unknown"
            )
            spend = float(
                c.get("metric.totalCost") or c.get("metric.supplyCost")
                or c.get("cost") or c.get("spend") or 0
            )
            sales = float(
                c.get("metric.sales") or c.get("sales")
                or c.get("attributedSales14d") or c.get("revenue") or 0
            )
            impressions = int(c.get("metric.impressions") or c.get("impressions") or 0)
            clicks = int(c.get("metric.clicks") or c.get("clicks") or 0)
            orders = int(
                c.get("metric.purchases") or c.get("orders")
                or c.get("attributedConversions14d") or c.get("conversions") or 0
            )

            normalised.append({
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "state": c.get("state", c.get("status", "")),
                "spend": spend,
                "sales": sales,
                "impressions": impressions,
                "clicks": clicks,
                "orders": orders,
                "daily_budget": float(
                    c.get("dailyBudget") or c.get("daily_budget")
                    or c.get("budget") or 0
                ),
                "targeting_type": c.get("targetingType") or c.get("targeting_type") or "",
                "campaign_type": c.get("campaignType") or c.get("campaign_type") or "",
            })
        return enrich_campaigns(normalised)
