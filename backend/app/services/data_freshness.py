"""
Per-credential / profile data freshness — shared by cron health and UI banner.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccountPerformanceDaily,
    AdGroup,
    Campaign,
    CampaignPerformanceDaily,
    ProductPerformanceDaily,
    Report,
    SearchTermPerformance,
    SyncJob,
    Target,
)
from app.utils import utcnow


def staleness_label(dt: Optional[datetime], *, warn_hours: float, crit_hours: float) -> str:
    if not dt:
        return "never"
    age_hours = (utcnow() - dt).total_seconds() / 3600.0
    if age_hours < warn_hours:
        return "fresh"
    if age_hours < crit_hours:
        return "warn"
    return "stale"


def staleness_label_from_iso_date(
    date_str: Optional[str], *, warn_days: float, crit_days: float
) -> str:
    if not date_str:
        return "never"
    try:
        d = datetime.fromisoformat(str(date_str)[:10])
    except Exception:
        return "unknown"
    age_days = (utcnow().replace(tzinfo=None) - d).days
    if age_days < warn_days:
        return "fresh"
    if age_days < crit_days:
        return "warn"
    return "stale"


def schedule_profile_matches(report: Optional[Report], profile_id: Optional[str]) -> bool:
    if not report:
        return False
    raw = report.raw_response or {}
    return raw.get("profile_id") == profile_id


def _scope_query(query, model, credential_id, profile_id):
    q = query.where(model.credential_id == credential_id)
    if hasattr(model, "profile_id"):
        if profile_id is not None:
            q = q.where(model.profile_id == profile_id)
        else:
            q = q.where(model.profile_id.is_(None))
    return q


async def build_tables_and_jobs_freshness(
    db: AsyncSession,
    cred,
    selected_profile_id: Optional[str],
) -> dict[str, Any]:
    """Return ``{tables, latest_jobs}`` in the same shape as ``/api/cron/health``."""
    credential_id = cred.id

    async def _row_count(model) -> int:
        q = _scope_query(select(sa_func.count()).select_from(model), model, credential_id, selected_profile_id)
        return int((await db.execute(q)).scalar() or 0)

    async def _max_ts(model, col):
        q = _scope_query(select(sa_func.max(col)), model, credential_id, selected_profile_id)
        return (await db.execute(q)).scalar()

    tables: dict[str, dict] = {}

    last_camp_sync = await _max_ts(Campaign, Campaign.synced_at)
    tables["campaigns"] = {
        "row_count": await _row_count(Campaign),
        "last_synced_at": last_camp_sync.isoformat() if last_camp_sync else None,
        "staleness": staleness_label(last_camp_sync, warn_hours=24, crit_hours=72),
        "source": "Campaign sync cron (POST /api/cron/sync)",
    }
    last_ag_sync = await _max_ts(AdGroup, AdGroup.synced_at)
    tables["ad_groups"] = {
        "row_count": await _row_count(AdGroup),
        "last_synced_at": last_ag_sync.isoformat() if last_ag_sync else None,
        "staleness": staleness_label(last_ag_sync, warn_hours=24, crit_hours=72),
        "source": "Campaign sync cron",
    }
    last_t_sync = await _max_ts(Target, Target.synced_at)
    tables["targets"] = {
        "row_count": await _row_count(Target),
        "last_synced_at": last_t_sync.isoformat() if last_t_sync else None,
        "staleness": staleness_label(last_t_sync, warn_hours=24, crit_hours=72),
        "source": "Campaign sync cron",
    }

    last_acct_perf = await _max_ts(AccountPerformanceDaily, AccountPerformanceDaily.date)
    tables["account_performance_daily"] = {
        "row_count": await _row_count(AccountPerformanceDaily),
        "latest_date": str(last_acct_perf) if last_acct_perf else None,
        "staleness": staleness_label_from_iso_date(last_acct_perf, warn_days=2, crit_days=4),
        "source": "Reports cron (POST /api/cron/reports)",
    }
    last_camp_perf = await _max_ts(CampaignPerformanceDaily, CampaignPerformanceDaily.date)
    tables["campaign_performance_daily"] = {
        "row_count": await _row_count(CampaignPerformanceDaily),
        "latest_date": str(last_camp_perf) if last_camp_perf else None,
        "staleness": staleness_label_from_iso_date(last_camp_perf, warn_days=2, crit_days=4),
        "source": "Reports cron",
    }
    last_st_perf = await _max_ts(SearchTermPerformance, SearchTermPerformance.date)
    tables["search_term_performance"] = {
        "row_count": await _row_count(SearchTermPerformance),
        "latest_date": str(last_st_perf) if last_st_perf else None,
        "staleness": staleness_label_from_iso_date(last_st_perf, warn_days=2, crit_days=7),
        "source": "Search-terms cron (POST /api/cron/search-terms)",
    }
    last_prod_perf = await _max_ts(ProductPerformanceDaily, ProductPerformanceDaily.date)
    tables["product_performance_daily"] = {
        "row_count": await _row_count(ProductPerformanceDaily),
        "latest_date": str(last_prod_perf) if last_prod_perf else None,
        "staleness": staleness_label_from_iso_date(last_prod_perf, warn_days=2, crit_days=7),
        "source": "Products cron (POST /api/cron/products)",
    }

    latest_sync = (
        await db.execute(
            select(SyncJob)
            .where(SyncJob.credential_id == credential_id)
            .order_by(SyncJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    latest_jobs: dict[str, Optional[dict]] = {
        "campaign_sync": (
            {
                "id": str(latest_sync.id),
                "status": latest_sync.status,
                "step": latest_sync.step,
                "progress_pct": latest_sync.progress_pct or 0,
                "created_at": latest_sync.created_at.isoformat() if latest_sync.created_at else None,
                "completed_at": latest_sync.completed_at.isoformat() if latest_sync.completed_at else None,
                "error_message": latest_sync.error_message,
            }
            if latest_sync
            else None
        ),
    }

    for report_type, key in (
        ("performance_sync", "reports"),
        ("search_terms_sync", "search_terms"),
        ("product_sync", "products"),
    ):
        rep_q = (
            select(Report)
            .where(Report.credential_id == credential_id, Report.report_type == report_type)
            .order_by(Report.created_at.desc())
            .limit(5)
        )
        rep_rows = (await db.execute(rep_q)).scalars().all()
        match = next(
            (r for r in rep_rows if schedule_profile_matches(r, selected_profile_id)),
            rep_rows[0] if rep_rows else None,
        )
        if match is None:
            latest_jobs[key] = None
            continue
        raw = match.raw_response or {}
        latest_jobs[key] = {
            "id": str(match.id),
            "status": match.status,
            "step": raw.get("step"),
            "range_preset": raw.get("range_preset"),
            "date_range_start": match.date_range_start,
            "date_range_end": match.date_range_end,
            "progress_pct": raw.get("progress_pct"),
            "days_synced": raw.get("days_synced"),
            "days_total": raw.get("days_total"),
            "created_at": match.created_at.isoformat() if match.created_at else None,
            "completed_at": match.completed_at.isoformat() if match.completed_at else None,
            "error": raw.get("error"),
        }

    return {"tables": tables, "latest_jobs": latest_jobs}


def overall_freshness_status(tables: dict[str, dict]) -> str:
    any_stale = any(t.get("staleness") in ("stale", "never") for t in tables.values())
    return "stale" if any_stale else "fresh"
