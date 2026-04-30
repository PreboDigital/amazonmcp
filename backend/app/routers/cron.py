"""
Cron / Scheduled Jobs — Endpoints for Upstash QStash or external cron.

These endpoints are called by QStash on a schedule. They verify CRON_SECRET
and trigger campaign sync, report generation, and search term sync for the
default credential.

Set CRON_SECRET in Railway Variables. QStash sends:
  Authorization: Bearer <QSTASH_CURRENT_SIGNING_KEY> (verify via Upstash-Signature)
  OR use a simple secret: X-Cron-Secret: <CRON_SECRET>

Admin-only /trigger/* endpoints allow manual runs from the UI.
"""

import asyncio
import hashlib
import logging
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import (
    Account,
    AccountPerformanceDaily,
    CampaignPerformanceDaily,
    ProductPerformanceDaily,
    Report,
    SearchTermPerformance,
    SyncJob,
    User,
)
from app.services.data_freshness import build_tables_and_jobs_freshness
from app.routers.campaigns import _run_sync_background, run_full_sync
from app.routers.reporting import (
    _find_report_sync_job,
    _get_cred,
    _get_exact_daily_coverage,
    _get_report_sync_progress,
    _is_report_sync_job_stale,
    _restart_report_sync_job,
    _resolve_advertiser_account_id,
    _run_report_sync_background,
)
from app.services.reporting_service import DATE_PRESETS, get_date_range
from app.services.product_reporting_service import ProductReportingService
from app.services.search_term_service import SearchTermService
from app.services.token_service import get_mcp_client_with_fresh_token
from app.utils import marketplace_today, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron", tags=["Cron"])

SCHEDULE_RANGE_PRESETS = {
    "today": "Today",
    "yesterday": "Yesterday",
    "last_7_days": "Last 7 days",
    "this_week": "This week",
    "last_week": "Last week",
    "last_30_days": "Last 30 days",
    "this_month": "This month",
    "last_month": "Last month",
    "year_to_date": "Year-to-date",
    "month_to_yesterday": "Month to yesterday",
}

DEFAULT_RANGE_BY_JOB = {
    "reports": "yesterday",
    "search-terms": "last_7_days",
    "products": "last_30_days",
}


def _get_schedule_range_preset(job: str, range_preset: Optional[str]) -> Optional[str]:
    if job == "sync":
        return None
    preset = range_preset or DEFAULT_RANGE_BY_JOB.get(job)
    if preset not in SCHEDULE_RANGE_PRESETS:
        raise HTTPException(
            400,
            f"Invalid range_preset for {job}. Use one of: {list(SCHEDULE_RANGE_PRESETS.keys())}",
        )
    return preset


def _resolve_schedule_range(
    range_preset: str,
    marketplace: Optional[str] = None,
    region: Optional[str] = None,
) -> tuple[str, str]:
    today = marketplace_today(marketplace, region)
    yesterday = today - timedelta(days=1)

    if range_preset == "month_to_yesterday":
        end_d = yesterday
        start_d = end_d.replace(day=1)
    elif range_preset in DATE_PRESETS:
        start_d, end_d = get_date_range(range_preset, marketplace=marketplace, region=region)
    else:
        raise HTTPException(
            400,
            f"Invalid range_preset. Use one of: {list(SCHEDULE_RANGE_PRESETS.keys())}",
        )

    return start_d.isoformat(), end_d.isoformat()


async def _get_cred_and_profile(
    db: AsyncSession,
    credential_id: Optional[str] = None,
    profile_id: Optional[str] = None,
):
    cred = await _get_cred(db, credential_id)
    selected_profile_id = profile_id if profile_id is not None else cred.profile_id
    return cred, selected_profile_id


async def _get_marketplace_for_profile(
    db: AsyncSession,
    cred,
    profile_id: Optional[str],
) -> Optional[str]:
    """Look up the discovered marketplace code for an active profile, if any."""
    if not profile_id:
        return None
    result = await db.execute(
        select(Account).where(
            Account.credential_id == cred.id,
            Account.profile_id == profile_id,
        )
    )
    account = result.scalar_one_or_none()
    return account.marketplace if account else None


def _schedule_profile_matches(report: Optional[Report], profile_id: Optional[str]) -> bool:
    if not report:
        return False
    raw = report.raw_response or {}
    return raw.get("profile_id") == profile_id


async def _find_aux_schedule_job(
    db: AsyncSession,
    credential_id,
    report_type: str,
    start_date: str,
    end_date: str,
    profile_id: Optional[str],
) -> Optional[Report]:
    from sqlalchemy import select

    result = await db.execute(
        select(Report)
        .where(
            Report.credential_id == credential_id,
            Report.report_type == report_type,
            Report.date_range_start == start_date,
            Report.date_range_end == end_date,
        )
        .order_by(Report.created_at.desc())
        .limit(10)
    )
    for report in result.scalars().all():
        if _schedule_profile_matches(report, profile_id):
            return report
    return None


async def _set_aux_schedule_job_state(
    db: AsyncSession,
    *,
    report: Optional[Report],
    credential_id,
    report_type: str,
    start_date: str,
    end_date: str,
    profile_id: Optional[str],
    range_preset: str,
    status: str,
    step: str,
    result_payload: Optional[dict] = None,
    pending_report_id: Optional[str] = None,
    error: Optional[str] = None,
) -> Report:
    raw = dict(report.raw_response or {}) if report else {}
    raw.update({
        "profile_id": profile_id,
        "range_preset": range_preset,
        "step": step,
        "last_attempt_at": utcnow().isoformat(),
    })
    if pending_report_id:
        raw["pending_report_id"] = pending_report_id
    else:
        raw.pop("pending_report_id", None)
    if error:
        raw["error"] = error
    else:
        raw.pop("error", None)

    if not report:
        report = Report(
            credential_id=credential_id,
            report_type=report_type,
            ad_product="ALL",
            date_range_start=start_date,
            date_range_end=end_date,
            status=status,
            raw_response=raw,
            report_data=result_payload or {"status": status},
        )
        db.add(report)
    else:
        report.status = status
        report.raw_response = raw
        report.report_data = result_payload or {"status": status}

    if status in ("completed", "failed"):
        report.completed_at = utcnow()
    else:
        report.completed_at = None

    await db.commit()
    return report


# Idempotency window: if data for the *exact same* (credential, profile,
# range, ad_product) was synced more recently than this, the cron tick is a
# no-op. Calibrated for QStash schedules running every 6h+ — a 4h window
# de-duplicates redundant intra-day ticks (e.g. last_30_days × every 6h)
# without blocking once-a-day cron from running. Manual /trigger/* paths
# pass ``min_age_hours=0`` to always re-fetch.
DEFAULT_FRESHNESS_HOURS = 4.0


async def _search_terms_recent_sync_at(
    db: AsyncSession,
    *,
    credential_id,
    profile_id: Optional[str],
    ad_product: str,
    start_date: str,
    end_date: str,
):
    """Most recent ``synced_at`` for matching SearchTermPerformance rows.

    None when nothing's been synced yet for this exact range. Lets the cron
    skip a redundant 60-120s MCP report if data is already fresh enough.
    """
    from sqlalchemy import func as _func
    q = (
        select(_func.max(SearchTermPerformance.synced_at))
        .where(
            SearchTermPerformance.credential_id == credential_id,
            SearchTermPerformance.ad_product == ad_product,
            SearchTermPerformance.report_date_start == start_date,
            SearchTermPerformance.report_date_end == end_date,
        )
    )
    if profile_id is not None:
        q = q.where(SearchTermPerformance.profile_id == profile_id)
    else:
        q = q.where(SearchTermPerformance.profile_id.is_(None))
    return (await db.execute(q)).scalar()


async def _products_recent_sync_at(
    db: AsyncSession,
    *,
    credential_id,
    profile_id: Optional[str],
    start_date: str,
    end_date: str,
):
    """Most recent ``synced_at`` for matching ProductPerformanceDaily rows."""
    from sqlalchemy import func as _func
    q = (
        select(_func.max(ProductPerformanceDaily.synced_at))
        .where(
            ProductPerformanceDaily.credential_id == credential_id,
            ProductPerformanceDaily.report_date_start == start_date,
            ProductPerformanceDaily.report_date_end == end_date,
        )
    )
    if profile_id is not None:
        q = q.where(ProductPerformanceDaily.profile_id == profile_id)
    else:
        q = q.where(ProductPerformanceDaily.profile_id.is_(None))
    return (await db.execute(q)).scalar()


def _is_data_recent(synced_at, *, min_age_hours: float) -> bool:
    """True when ``synced_at`` is fresher than ``min_age_hours`` ago."""
    if synced_at is None or min_age_hours <= 0:
        return False
    return (utcnow() - synced_at) < timedelta(hours=min_age_hours)


async def _queue_exact_daily_schedule_sync(
    db: AsyncSession,
    cred,
    profile_id: Optional[str],
    start_date: str,
    end_date: str,
    range_preset: str,
) -> dict:
    ready, synced_days, expected_days = await _get_exact_daily_coverage(
        db,
        cred.id,
        start_date,
        end_date,
        profile_id=profile_id,
    )
    if ready:
        return {
            "status": "completed",
            "range_preset": range_preset,
            "start_date": start_date,
            "end_date": end_date,
            "days_synced": synced_days,
            "days_total": expected_days,
        }

    sync_job = await _find_report_sync_job(db, cred.id, start_date, end_date, profile_id)
    if _is_report_sync_job_stale(sync_job):
        sync_job = await _restart_report_sync_job(
            db,
            sync_job,
            reason="Scheduled exact daily sync heartbeat went stale; resuming saved progress.",
        )

    if sync_job and sync_job.status in ("pending", "running"):
        return {
            "status": "running",
            "range_preset": range_preset,
            "start_date": start_date,
            "end_date": end_date,
            "sync_progress": _get_report_sync_progress(sync_job),
        }

    sync_job = Report(
        credential_id=cred.id,
        report_type="performance_sync",
        ad_product="ALL",
        date_range_start=start_date,
        date_range_end=end_date,
        status="pending",
        raw_response={
            "profile_id": profile_id,
            "range_preset": range_preset,
            "step": "Queued exact daily sync...",
            "progress_pct": 0,
            "days_synced": synced_days,
            "days_total": expected_days,
            "queued_at": utcnow().isoformat(),
            "heartbeat_at": utcnow().isoformat(),
        },
    )
    db.add(sync_job)
    await db.flush()
    await db.commit()
    asyncio.create_task(_run_report_sync_background(sync_job.id))
    return {
        "status": "queued",
        "range_preset": range_preset,
        "start_date": start_date,
        "end_date": end_date,
        "sync_progress": _get_report_sync_progress(sync_job),
    }


def _get_cron_secret() -> str:
    import os
    return os.environ.get("CRON_SECRET", "")


def _resolve_public_base_url(request: Request, configured_url: str) -> str:
    """
    Prefer an explicit PUBLIC_URL/RAILWAY_PUBLIC_DOMAIN, but fall back to the
    actual incoming request host when schedule creation runs behind a proxy.
    """
    base_url = (configured_url or "").strip().rstrip("/")
    if base_url.startswith(("http://", "https://")) and "localhost" not in base_url:
        return base_url

    forwarded_proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    request_base = str(request.base_url).rstrip("/")
    if request_base:
        return request_base

    return base_url


async def _require_cron_secret(
    x_cron_secret: str | None = Header(None, alias="X-Cron-Secret"),
    authorization: str | None = Header(None),
) -> None:
    """Verify request came from QStash or cron with valid secret."""
    secret = _get_cron_secret()
    if not secret:
        raise HTTPException(500, "CRON_SECRET not configured")
    # Accept X-Cron-Secret header or Bearer token
    token = x_cron_secret
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if token != secret:
        raise HTTPException(401, "Invalid cron secret")


@router.post("/sync")
async def cron_sync(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """
    Scheduled campaign sync. Call from QStash:
    POST https://your-app.railway.app/api/cron/sync
    Header: X-Cron-Secret: <CRON_SECRET>

    Non-blocking: spawns the actual sync as an asyncio task and returns the
    SyncJob id immediately. QStash's default delivery timeout (~15s) is too
    tight for a full SP/SB/SD pull on large accounts, so we mirror the
    /sync/start UI pattern instead of awaiting ``run_full_sync`` inline —
    otherwise QStash retries the cron tick mid-sync and we get duplicate
    concurrent syncs racing on the same campaigns table.
    """
    try:
        cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)

        job = SyncJob(
            credential_id=cred.id,
            user_id=None,  # cron-initiated; no user
            status="running",
            step="Queued by cron",
            progress_pct=0,
        )
        db.add(job)
        await db.flush()
        await db.commit()

        asyncio.create_task(_run_sync_background(
            job.id,
            str(cred.id),
            user_email=None,        # cron does not email per run
            account_name=None,
            profile_id_override=selected_profile_id,
        ))
        logger.info(
            "Cron sync queued: job_id=%s cred=%s profile=%s",
            job.id, cred.id, selected_profile_id,
        )
        return {
            "status": "queued",
            "job_id": str(job.id),
            "credential_id": str(cred.id),
            "profile_id": selected_profile_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Cron sync failed to queue")
        raise HTTPException(500, str(e))


@router.post("/reports")
async def cron_reports(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    range_preset: Optional[str] = Query(None),
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """
    Scheduled daily report generation (yesterday). Call from QStash:
    POST https://your-app.railway.app/api/cron/reports
    Header: X-Cron-Secret: <CRON_SECRET>
    """
    try:
        cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
        marketplace = await _get_marketplace_for_profile(db, cred, selected_profile_id)
        selected_range = _get_schedule_range_preset("reports", range_preset)
        start_date, end_date = _resolve_schedule_range(
            selected_range, marketplace=marketplace, region=cred.region
        )
        result = await _queue_exact_daily_schedule_sync(
            db,
            cred,
            selected_profile_id,
            start_date,
            end_date,
            selected_range,
        )
        logger.info("Cron reports queued/resumed: %s", result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Cron reports failed")
        raise HTTPException(500, str(e))


@router.post("/search-terms")
async def cron_search_terms(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    range_preset: Optional[str] = Query(None),
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """
    Scheduled search term sync. Call from QStash:
    POST https://your-app.railway.app/api/cron/search-terms
    Header: X-Cron-Secret: <CRON_SECRET>

    Idempotent: when SearchTermPerformance rows for the exact requested
    range were synced within :data:`DEFAULT_FRESHNESS_HOURS`, returns
    ``status=skipped_fresh`` instead of creating another 60-120s Amazon
    report. Avoids 4 redundant 30-day fetches/day on a ``every 6h ×
    last_30_days`` schedule.
    """
    try:
        cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
        marketplace = await _get_marketplace_for_profile(db, cred, selected_profile_id)
        selected_range = _get_schedule_range_preset("search-terms", range_preset)
        start_date, end_date = _resolve_schedule_range(
            selected_range, marketplace=marketplace, region=cred.region
        )
        recent_sync = await _search_terms_recent_sync_at(
            db,
            credential_id=cred.id,
            profile_id=selected_profile_id,
            ad_product="SPONSORED_PRODUCTS",
            start_date=start_date,
            end_date=end_date,
        )
        if _is_data_recent(recent_sync, min_age_hours=DEFAULT_FRESHNESS_HOURS):
            logger.info(
                "Cron search-terms skipped — data synced %s ago (range=%s..%s, profile=%s)",
                utcnow() - recent_sync, start_date, end_date, selected_profile_id,
            )
            return {
                "status": "skipped_fresh",
                "range_preset": selected_range,
                "start_date": start_date,
                "end_date": end_date,
                "last_synced_at": recent_sync.isoformat(),
            }

        job = await _find_aux_schedule_job(
            db,
            cred.id,
            "search_terms_sync",
            start_date,
            end_date,
            selected_profile_id,
        )
        pending_report_id = (job.raw_response or {}).get("pending_report_id") if job else None

        client = await get_mcp_client_with_fresh_token(cred, db, profile_id_override=selected_profile_id)
        adv_id = await _resolve_advertiser_account_id(db, cred, profile_id_override=selected_profile_id)
        service = SearchTermService(client, adv_id, marketplace=marketplace)
        result = await service.sync_search_terms(
            db=db,
            credential_id=cred.id,
            start_date=start_date,
            end_date=end_date,
            pending_report_id=pending_report_id,
            profile_id=selected_profile_id,
        )
        if result.get("_pending_report_id"):
            await _set_aux_schedule_job_state(
                db,
                report=job,
                credential_id=cred.id,
                report_type="search_terms_sync",
                start_date=start_date,
                end_date=end_date,
                profile_id=selected_profile_id,
                range_preset=selected_range,
                status="running",
                step="Waiting for Amazon search term report...",
                result_payload=result,
                pending_report_id=result["_pending_report_id"],
            )
            return {
                "status": "pending",
                "range_preset": selected_range,
                "start_date": start_date,
                "end_date": end_date,
                "pending_report_id": result["_pending_report_id"],
            }
        if result.get("status") == "completed":
            await _set_aux_schedule_job_state(
                db,
                report=job,
                credential_id=cred.id,
                report_type="search_terms_sync",
                start_date=start_date,
                end_date=end_date,
                profile_id=selected_profile_id,
                range_preset=selected_range,
                status="completed",
                step="Completed",
                result_payload=result,
            )
            return {
                "status": "completed",
                "range_preset": selected_range,
                "start_date": start_date,
                "end_date": end_date,
                "rows_stored": result.get("rows_stored", 0),
            }

        message = result.get("message") or "Search term sync failed"
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="search_terms_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="failed",
            step="Failed",
            result_payload=result,
            error=message,
        )
        raise HTTPException(500, message)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Cron search terms failed")
        raise HTTPException(500, str(e))


@router.post("/products")
async def cron_products(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    range_preset: Optional[str] = Query(None),
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """Scheduled product/business report sync.

    Idempotent — same logic as search-terms. Skips redundant fetches when
    ProductPerformanceDaily already has rows for this range synced within
    :data:`DEFAULT_FRESHNESS_HOURS`.
    """
    try:
        cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
        marketplace = await _get_marketplace_for_profile(db, cred, selected_profile_id)
        selected_range = _get_schedule_range_preset("products", range_preset)
        start_date, end_date = _resolve_schedule_range(
            selected_range, marketplace=marketplace, region=cred.region
        )
        recent_sync = await _products_recent_sync_at(
            db,
            credential_id=cred.id,
            profile_id=selected_profile_id,
            start_date=start_date,
            end_date=end_date,
        )
        if _is_data_recent(recent_sync, min_age_hours=DEFAULT_FRESHNESS_HOURS):
            logger.info(
                "Cron products skipped — data synced %s ago (range=%s..%s, profile=%s)",
                utcnow() - recent_sync, start_date, end_date, selected_profile_id,
            )
            return {
                "status": "skipped_fresh",
                "range_preset": selected_range,
                "start_date": start_date,
                "end_date": end_date,
                "last_synced_at": recent_sync.isoformat(),
            }

        job = await _find_aux_schedule_job(
            db,
            cred.id,
            "product_sync",
            start_date,
            end_date,
            selected_profile_id,
        )
        pending_report_id = (job.raw_response or {}).get("pending_report_id") if job else None

        client = await get_mcp_client_with_fresh_token(cred, db, profile_id_override=selected_profile_id)
        adv_id = await _resolve_advertiser_account_id(db, cred, profile_id_override=selected_profile_id)
        service = ProductReportingService(client, adv_id, marketplace=marketplace)
        result = await service.sync_products(
            db=db,
            credential_id=cred.id,
            start_date=start_date,
            end_date=end_date,
            pending_report_id=pending_report_id,
            profile_id=selected_profile_id,
        )
        if result.get("_pending_report_id"):
            await _set_aux_schedule_job_state(
                db,
                report=job,
                credential_id=cred.id,
                report_type="product_sync",
                start_date=start_date,
                end_date=end_date,
                profile_id=selected_profile_id,
                range_preset=selected_range,
                status="running",
                step="Waiting for Amazon product report...",
                result_payload=result,
                pending_report_id=result["_pending_report_id"],
            )
            return {
                "status": "pending",
                "range_preset": selected_range,
                "start_date": start_date,
                "end_date": end_date,
                "pending_report_id": result["_pending_report_id"],
            }
        if result.get("status") == "completed":
            await _set_aux_schedule_job_state(
                db,
                report=job,
                credential_id=cred.id,
                report_type="product_sync",
                start_date=start_date,
                end_date=end_date,
                profile_id=selected_profile_id,
                range_preset=selected_range,
                status="completed",
                step="Completed",
                result_payload=result,
            )
            return {
                "status": "completed",
                "range_preset": selected_range,
                "start_date": start_date,
                "end_date": end_date,
                "rows_stored": result.get("rows_stored", 0),
            }

        message = result.get("message") or "Product report sync failed"
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="product_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="failed",
            step="Failed",
            result_payload=result,
            error=message,
        )
        raise HTTPException(500, message)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Cron product reports failed")
        raise HTTPException(500, str(e))


# ── Admin-only manual trigger (no CRON_SECRET) ──────────────────────────

async def _run_reports(
    db: AsyncSession,
    credential_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    range_preset: Optional[str] = None,
):
    """Shared logic for scheduled exact-daily reports."""
    cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
    marketplace = await _get_marketplace_for_profile(db, cred, selected_profile_id)
    selected_range = _get_schedule_range_preset("reports", range_preset)
    start_date, end_date = _resolve_schedule_range(
        selected_range, marketplace=marketplace, region=cred.region
    )
    return await _queue_exact_daily_schedule_sync(
        db,
        cred,
        selected_profile_id,
        start_date,
        end_date,
        selected_range,
    )


async def _run_search_terms(
    db: AsyncSession,
    credential_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    range_preset: Optional[str] = None,
):
    """Shared logic for search term cron with resume support."""
    cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
    marketplace = await _get_marketplace_for_profile(db, cred, selected_profile_id)
    selected_range = _get_schedule_range_preset("search-terms", range_preset)
    start_date, end_date = _resolve_schedule_range(
        selected_range, marketplace=marketplace, region=cred.region
    )
    job = await _find_aux_schedule_job(
        db,
        cred.id,
        "search_terms_sync",
        start_date,
        end_date,
        selected_profile_id,
    )
    pending_report_id = (job.raw_response or {}).get("pending_report_id") if job else None
    client = await get_mcp_client_with_fresh_token(cred, db, profile_id_override=selected_profile_id)
    adv_id = await _resolve_advertiser_account_id(db, cred, profile_id_override=selected_profile_id)
    service = SearchTermService(client, adv_id, marketplace=marketplace)
    result = await service.sync_search_terms(
        db=db,
        credential_id=cred.id,
        start_date=start_date,
        end_date=end_date,
        pending_report_id=pending_report_id,
        profile_id=selected_profile_id,
    )
    if result.get("_pending_report_id"):
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="search_terms_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="running",
            step="Waiting for Amazon search term report...",
            result_payload=result,
            pending_report_id=result["_pending_report_id"],
        )
    elif result.get("status") == "completed":
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="search_terms_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="completed",
            step="Completed",
            result_payload=result,
        )
    else:
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="search_terms_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="failed",
            step="Failed",
            result_payload=result,
            error=result.get("message") or "Search term sync failed",
        )
    return result


async def _run_products(
    db: AsyncSession,
    credential_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    range_preset: Optional[str] = None,
):
    """Shared logic for product cron with resume support."""
    cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
    marketplace = await _get_marketplace_for_profile(db, cred, selected_profile_id)
    selected_range = _get_schedule_range_preset("products", range_preset)
    start_date, end_date = _resolve_schedule_range(
        selected_range, marketplace=marketplace, region=cred.region
    )
    job = await _find_aux_schedule_job(
        db,
        cred.id,
        "product_sync",
        start_date,
        end_date,
        selected_profile_id,
    )
    pending_report_id = (job.raw_response or {}).get("pending_report_id") if job else None
    client = await get_mcp_client_with_fresh_token(cred, db, profile_id_override=selected_profile_id)
    adv_id = await _resolve_advertiser_account_id(db, cred, profile_id_override=selected_profile_id)
    service = ProductReportingService(client, adv_id, marketplace=marketplace)
    result = await service.sync_products(
        db=db,
        credential_id=cred.id,
        start_date=start_date,
        end_date=end_date,
        pending_report_id=pending_report_id,
        profile_id=selected_profile_id,
    )
    if result.get("_pending_report_id"):
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="product_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="running",
            step="Waiting for Amazon product report...",
            result_payload=result,
            pending_report_id=result["_pending_report_id"],
        )
    elif result.get("status") == "completed":
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="product_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="completed",
            step="Completed",
            result_payload=result,
        )
    else:
        await _set_aux_schedule_job_state(
            db,
            report=job,
            credential_id=cred.id,
            report_type="product_sync",
            start_date=start_date,
            end_date=end_date,
            profile_id=selected_profile_id,
            range_preset=selected_range,
            status="failed",
            step="Failed",
            result_payload=result,
            error=result.get("message") or "Product sync failed",
        )
    return result


@router.post("/trigger/sync")
async def trigger_sync(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger campaign sync. Admin only.

    Non-blocking — returns a SyncJob id; poll ``GET /api/campaigns/sync/{id}``
    for progress. This avoids the request stalling for 30-120s on large
    accounts (and matches the /sync/start UI flow).
    """
    try:
        cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
        job = SyncJob(
            credential_id=cred.id,
            user_id=user.id,
            status="running",
            step="Queued (admin trigger)",
            progress_pct=0,
        )
        db.add(job)
        await db.flush()
        await db.commit()
        asyncio.create_task(_run_sync_background(
            job.id,
            str(cred.id),
            user_email=user.email,
            account_name=None,
            profile_id_override=selected_profile_id,
        ))
        return {
            "status": "queued",
            "job_id": str(job.id),
            "credential_id": str(cred.id),
            "profile_id": selected_profile_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Manual sync failed to queue")
        raise HTTPException(500, str(e))


@router.post("/trigger/reports")
async def trigger_reports(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    range_preset: Optional[str] = Query(None),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger report generation. Admin only."""
    try:
        return await _run_reports(db, credential_id, profile_id, range_preset)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Manual reports failed")
        raise HTTPException(500, str(e))


@router.post("/trigger/search-terms")
async def trigger_search_terms(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    range_preset: Optional[str] = Query(None),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger search term sync. Admin only."""
    try:
        result = await _run_search_terms(db, credential_id, profile_id, range_preset)
        return {"status": "ok", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Manual search terms failed")
        raise HTTPException(500, str(e))


@router.post("/trigger/products")
async def trigger_products(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    range_preset: Optional[str] = Query(None),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger product report sync. Admin only."""
    try:
        result = await _run_products(db, credential_id, profile_id, range_preset)
        return {"status": "ok", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Manual product reports failed")
        raise HTTPException(500, str(e))


# ── Health / freshness ──────────────────────────────────────────────────


@router.get("/health")
async def cron_health(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Per-table data-freshness for the active credential/profile.

    Powers the "is the cron actually running?" UI badge and gives the AI
    chatbot a structured answer for "how stale is my data?" without it
    having to infer from sample rows.

    Returns:

    * ``tables`` — for each cached source: row_count, latest timestamp / date,
      staleness label (``fresh|warn|stale|never``).
    * ``latest_jobs`` — last SyncJob + last performance/search-term/product
      sync Report rows (with status, range, age).
    * ``schedules_configured`` — count of QStash schedules registered for
      this app (when QSTASH_TOKEN is set), so the user can see if anything
      is actually scheduled.
    """
    cred, selected_profile_id = await _get_cred_and_profile(db, credential_id, profile_id)
    out = {
        "credential_id": str(cred.id),
        "profile_id": selected_profile_id,
        "checked_at": utcnow().isoformat(),
    }

    core = await build_tables_and_jobs_freshness(db, cred, selected_profile_id)
    tables = core["tables"]
    latest_jobs = core["latest_jobs"]
    out["tables"] = tables
    out["latest_jobs"] = latest_jobs

    # ── Schedules registered with QStash ───────────────────────────────
    schedules_summary = {"configured": False, "count": 0, "by_job": {}, "error": None}
    try:
        from app.config import get_settings as _gs
        import httpx as _httpx
        _settings = _gs()
        if _settings.qstash_token:
            schedules_summary["configured"] = True
            base = (_settings.qstash_url or "https://qstash.upstash.io").rstrip("/")
            async with _httpx.AsyncClient(timeout=8.0) as _client:
                r = await _client.get(
                    f"{base}/v2/schedules",
                    headers={"Authorization": f"Bearer {_settings.qstash_token}"},
                )
                r.raise_for_status()
                data = r.json()
                items = data if isinstance(data, list) else data.get("schedules", [])
                # Filter to schedules pointing at THIS app
                public_url = _settings.effective_public_url.rstrip("/")
                local_items = [
                    s for s in items
                    if isinstance(s, dict) and (s.get("destination") or "").startswith(public_url)
                ]
                schedules_summary["count"] = len(local_items)
                by_job: dict[str, int] = {}
                for s in local_items:
                    dest = s.get("destination") or ""
                    for job, path in CRON_JOB_PATHS.items():
                        if path in dest:
                            by_job[job] = by_job.get(job, 0) + 1
                            break
                schedules_summary["by_job"] = by_job
    except Exception as exc:
        schedules_summary["error"] = str(exc)[:200]

    out["schedules"] = schedules_summary

    # ── Top-line health verdict ─────────────────────────────────────────
    # OK iff every "warn|crit"-tracked table is fresh AND a campaign-sync
    # schedule exists (other schedules are optional for core chat features).
    any_stale = any(
        t.get("staleness") in ("stale", "never")
        for t in tables.values()
    )
    out["overall"] = {
        "status": "stale" if any_stale else "fresh",
        "missing_schedules": [
            j for j in ("sync", "reports", "search-terms", "products")
            if not schedules_summary.get("by_job", {}).get(j)
        ] if schedules_summary.get("configured") else None,
    }
    return out


@router.post("/weekly-digest")
async def cron_weekly_digest(
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """
    Weekly email digest for all users with ``weekly_digest_enabled``.
    Schedule via QStash: POST /api/cron/weekly-digest with X-Cron-Secret.
    """
    from app.config import get_settings as _gs
    from app.services.digest_service import build_weekly_digest_html
    from app.services.email_service import send_weekly_digest_email

    settings = _gs()
    r = await db.execute(
        select(User).where(User.is_active == True, User.weekly_digest_enabled == True)
    )
    users = list(r.scalars().all())
    sent = 0
    for user in users:
        html = await build_weekly_digest_html(
            db, user=user, app_base_url=settings.effective_public_url
        )
        if not html:
            continue
        if await asyncio.to_thread(send_weekly_digest_email, user.email, html):
            sent += 1
    return {"sent": sent, "eligible_users": len(users)}


# Job type -> cron path suffix
CRON_JOB_PATHS = {
    "sync": "/api/cron/sync",
    "reports": "/api/cron/reports",
    "search-terms": "/api/cron/search-terms",
    "products": "/api/cron/products",
}


class CreateScheduleRequest(BaseModel):
    job: str  # sync | reports | search-terms | products
    cron: str  # e.g. "0 */6 * * *"
    credential_id: Optional[str] = None
    profile_id: Optional[str] = None
    range_preset: Optional[str] = None
    # When True and credential has multiple profiles in the Account table,
    # create one schedule per discovered profile. Profile_id is ignored.
    all_profiles: bool = False


@router.get("/schedules")
async def list_schedules(_: User = Depends(require_admin)):
    """List QStash schedules. Admin only. Requires QSTASH_TOKEN."""
    from app.config import get_settings
    import httpx
    settings = get_settings()
    if not settings.qstash_token:
        return {"schedules": [], "message": "QSTASH_TOKEN not configured"}
    base = (settings.qstash_url or "https://qstash.upstash.io").rstrip("/")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{base}/v2/schedules",
                headers={"Authorization": f"Bearer {settings.qstash_token}"},
            )
            r.raise_for_status()
            data = r.json()
            return {"schedules": data if isinstance(data, list) else data.get("schedules", [])}
    except Exception as e:
        logger.exception("Failed to list QStash schedules")
        return {"schedules": [], "error": str(e)}


async def _list_credential_profiles(
    db: AsyncSession, credential_id_str: Optional[str],
) -> list[str]:
    """All profile_ids known for this credential. Used by all_profiles=True."""
    from app.utils import parse_uuid
    from app.routers.reporting import _get_cred as _get_cred_fn

    cred = await _get_cred_fn(db, credential_id_str)
    result = await db.execute(
        select(Account.profile_id)
        .where(Account.credential_id == cred.id, Account.profile_id.isnot(None))
        .distinct()
    )
    return [pid for (pid,) in result.all() if pid]


async def _post_qstash_schedule(
    *,
    settings,
    secret: str,
    job: str,
    cron: str,
    credential_id: Optional[str],
    profile_id: Optional[str],
    selected_range: Optional[str],
    base_url: str,
) -> dict:
    """Single QStash schedule POST. Extracted so create_schedule can fan out
    over multiple profiles when ``all_profiles=True``.
    """
    import httpx
    params: dict[str, str] = {}
    if credential_id:
        params["credential_id"] = credential_id
    if profile_id:
        params["profile_id"] = profile_id
    if selected_range:
        params["range_preset"] = selected_range
    qs = urlencode(params)
    destination = base_url.rstrip("/") + CRON_JOB_PATHS[job] + (f"?{qs}" if qs else "")
    if not destination.startswith(("http://", "https://")):
        raise HTTPException(
            500,
            "PUBLIC_URL or RAILWAY_PUBLIC_DOMAIN must produce a URL with http:// or https://. "
            f"Got base: {base_url!r}.",
        )
    base = (settings.qstash_url or "https://qstash.upstash.io").rstrip("/")
    schedule_fingerprint = hashlib.sha1(
        f"{job}|{cron}|{credential_id or ''}|{profile_id or ''}|{selected_range or ''}".encode("utf-8")
    ).hexdigest()[:10]
    schedule_id = f"amazon-ads-{job}-{schedule_fingerprint}"
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{base}/v2/schedules/{destination}",
            headers={
                "Authorization": f"Bearer {settings.qstash_token}",
                "Upstash-Cron": cron,
                "Upstash-Schedule-Id": schedule_id,
                "Upstash-Forward-X-Cron-Secret": secret,
                "Content-Type": "application/json",
            },
            content=b"{}",
        )
        if r.status_code in (400, 412):
            err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            msg = err.get("error", r.text) or r.text
            raise HTTPException(r.status_code, msg)
        r.raise_for_status()
        data = r.json()
        return {
            "scheduleId": data.get("scheduleId", schedule_id),
            "destination": destination,
            "cron": cron,
            "job": job,
            "range_preset": selected_range,
            "credential_id": credential_id,
            "profile_id": profile_id,
        }


@router.post("/schedules")
async def create_schedule(
    body: CreateScheduleRequest,
    request: Request,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create one or more QStash schedules.

    Admin only. Requires ``QSTASH_TOKEN`` and ``CRON_SECRET``. When
    ``all_profiles=True``, expands into one schedule per discovered profile
    on the credential — handy for multi-marketplace credentials so the
    operator doesn't have to repeat the form 4×.
    """
    from app.config import get_settings
    job = body.job
    cron = body.cron
    if job not in CRON_JOB_PATHS:
        raise HTTPException(400, f"Invalid job. Use: {list(CRON_JOB_PATHS.keys())}")
    if not cron or not isinstance(cron, str):
        raise HTTPException(400, "cron expression is required")
    selected_range = _get_schedule_range_preset(job, body.range_preset)
    settings = get_settings()
    if not settings.qstash_token:
        raise HTTPException(500, "QSTASH_TOKEN not configured")
    secret = _get_cron_secret()
    if not secret:
        raise HTTPException(500, "CRON_SECRET not configured")
    base_url = _resolve_public_base_url(request, settings.effective_public_url)

    # Resolve target profiles
    if body.all_profiles:
        profile_ids: list[Optional[str]] = await _list_credential_profiles(db, body.credential_id)
        if not profile_ids:
            raise HTTPException(
                400,
                "all_profiles=True but no profiles are stored for this credential. "
                "Run accounts/discover first, or pass profile_id explicitly.",
            )
    else:
        profile_ids = [body.profile_id]

    created: list[dict] = []
    failures: list[dict] = []
    for pid in profile_ids:
        try:
            res = await _post_qstash_schedule(
                settings=settings,
                secret=secret,
                job=job,
                cron=cron,
                credential_id=body.credential_id,
                profile_id=pid,
                selected_range=selected_range,
                base_url=base_url,
            )
            created.append(res)
        except HTTPException as he:
            failures.append({"profile_id": pid, "status_code": he.status_code, "error": he.detail})
        except Exception as e:
            logger.exception("Failed to create QStash schedule for profile %s", pid)
            failures.append({"profile_id": pid, "status_code": 500, "error": str(e)})

    if not created:
        raise HTTPException(500, {"message": "All schedule creations failed", "failures": failures})

    # Single-schedule path → preserve old response shape (a single dict) for
    # backward compatibility with existing UI code.
    if len(created) == 1 and not body.all_profiles:
        out = dict(created[0])
        if failures:
            out["failures"] = failures
        return out

    return {
        "created": created,
        "failures": failures,
        "count": len(created),
    }


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    _: User = Depends(require_admin),
):
    """Delete a QStash schedule. Admin only. Requires QSTASH_TOKEN."""
    from app.config import get_settings
    import httpx
    settings = get_settings()
    if not settings.qstash_token:
        raise HTTPException(500, "QSTASH_TOKEN not configured")
    base = (settings.qstash_url or "https://qstash.upstash.io").rstrip("/")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{base}/v2/schedules/{schedule_id}",
                headers={"Authorization": f"Bearer {settings.qstash_token}"},
            )
            if r.status_code == 404:
                raise HTTPException(404, "Schedule not found")
            r.raise_for_status()
            return {"status": "ok", "scheduleId": schedule_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete QStash schedule")
        raise HTTPException(500, str(e))
