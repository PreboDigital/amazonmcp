"""
Reporting Router — Advanced performance reporting with date-range presets,
period-over-period comparison, campaign breakdowns, trend data, and
full historical tracking in campaign_performance_daily /
account_performance_daily tables.
"""

import asyncio
import copy
import uuid
import logging
from datetime import date as date_type, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, delete
from pydantic import BaseModel
from typing import Optional
from app.database import get_db, async_session
from app.models import (
    Credential, Campaign, AuditSnapshot, Report, ActivityLog, Account,
    SearchTermPerformance, CampaignPerformanceDaily, AccountPerformanceDaily,
)
from app.services.account_scope import resolve_campaign_sync_scope
from app.services.token_service import get_mcp_client_with_fresh_token
from app.services.reporting_service import (
    get_date_range, get_comparison_range, get_comparison_range_for_dates,
    compute_metrics, compute_deltas, enrich_campaigns, ReportingService,
    store_campaign_rows_by_date, store_account_daily_summary,
    query_campaign_daily, query_account_daily_trend,
    DATE_PRESETS,
    get_currency_for_marketplace,
)
from app.services.search_term_service import SearchTermService, get_search_term_summary
from app.services.product_reporting_service import (
    ProductReportingService,
    query_product_rows,
    get_product_summary,
)
from app.services.report_skip_service import (
    get_permanent_skip_dates,
    update_after_sync as update_skip_state_after_sync,
)
from app.utils import parse_uuid, utcnow

logger = logging.getLogger(__name__)
router = APIRouter()

REPORT_SYNC_STALE_SECONDS = 600


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


async def _resolve_advertiser_account_id(
    db: AsyncSession,
    cred: Credential,
    profile_id_override: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve the Amazon Ads advertiserAccountId (amzn1.ads-account.g.xxx format)
    from the active Account's raw_data. The report API requires this — an empty
    accessRequestedAccounts array causes a server-side serialization error.
    """
    profile_id = profile_id_override if profile_id_override is not None else cred.profile_id
    if not profile_id:
        return None
    result = await db.execute(
        select(Account).where(
            Account.credential_id == cred.id,
            Account.profile_id == profile_id,
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


def _days_inclusive(start_date: str, end_date: str) -> int:
    start_d = date_type.fromisoformat(start_date)
    end_d = date_type.fromisoformat(end_date)
    return max(1, (end_d - start_d).days + 1)


async def _get_exact_daily_coverage(
    db: AsyncSession,
    credential_id,
    start_date: str,
    end_date: str,
    profile_id: Optional[str] = None,
) -> tuple[bool, int, int]:
    expected_days = _days_inclusive(start_date, end_date)
    where = [
        AccountPerformanceDaily.credential_id == credential_id,
        AccountPerformanceDaily.date >= start_date,
        AccountPerformanceDaily.date <= end_date,
        func.strpos(AccountPerformanceDaily.date, "__") <= 0,
    ]
    if profile_id is not None:
        where.append(AccountPerformanceDaily.profile_id == profile_id)
    else:
        where.append(AccountPerformanceDaily.profile_id.is_(None))

    result = await db.execute(
        select(func.count(func.distinct(AccountPerformanceDaily.date))).where(and_(*where))
    )
    synced_days = int(result.scalar() or 0)
    return synced_days >= expected_days, synced_days, expected_days


async def _find_report_sync_job(
    db: AsyncSession,
    credential_id,
    start_date: str,
    end_date: str,
    profile_id: Optional[str],
) -> Optional[Report]:
    result = await db.execute(
        select(Report)
        .where(
            Report.credential_id == credential_id,
            Report.report_type == "performance_sync",
            Report.date_range_start == start_date,
            Report.date_range_end == end_date,
        )
        .order_by(Report.created_at.desc())
        .limit(10)
    )
    for report in result.scalars().all():
        raw = report.raw_response or {}
        if raw.get("profile_id") == profile_id:
            return report
    return None


async def _find_completed_performance_report(
    db: AsyncSession,
    credential_id,
    start_date: str,
    end_date: str,
    profile_id: Optional[str],
) -> Optional[Report]:
    """
    Prefer a completed performance report with matching profile metadata.
    Older reports may not have profile metadata, so keep the newest legacy
    match as a fallback for single-profile credentials.
    """
    result = await db.execute(
        select(Report)
        .where(
            Report.credential_id == credential_id,
            Report.report_type == "performance",
            Report.status == "completed",
            Report.date_range_start == start_date,
            Report.date_range_end == end_date,
        )
        .order_by(Report.created_at.desc())
        .limit(10)
    )
    legacy_match = None
    for report in result.scalars().all():
        payload = report.report_data if isinstance(report.report_data, dict) else {}
        raw = report.raw_response if isinstance(report.raw_response, dict) else {}
        stored_profile_id = payload.get("profile_id") or raw.get("profile_id")
        if stored_profile_id == profile_id:
            return report
        if stored_profile_id is None and legacy_match is None:
            legacy_match = report
    return legacy_match


async def _find_recent_rolling_performance_report(
    db: AsyncSession,
    credential_id,
    start_date: str,
    end_date: str,
    profile_id: Optional[str],
    max_gap_days: int = 3,
) -> Optional[Report]:
    """
    For rolling ranges like "this_month", reuse a recently completed report
    with the same start date when the requested end date advanced by only a
    few days and exact daily coverage is not ready yet.
    """
    try:
        req_start = date_type.fromisoformat(start_date)
        req_end = date_type.fromisoformat(end_date)
    except ValueError:
        return None

    result = await db.execute(
        select(Report)
        .where(
            Report.credential_id == credential_id,
            Report.report_type == "performance",
            Report.status == "completed",
            Report.date_range_start == start_date,
        )
        .order_by(Report.date_range_end.desc(), Report.created_at.desc())
        .limit(20)
    )

    legacy_match = None
    for report in result.scalars().all():
        payload = report.report_data if isinstance(report.report_data, dict) else {}
        raw = report.raw_response if isinstance(report.raw_response, dict) else {}
        stored_profile_id = payload.get("profile_id") or raw.get("profile_id")
        if stored_profile_id not in (profile_id, None):
            continue
        try:
            report_end = date_type.fromisoformat(report.date_range_end)
            report_start = date_type.fromisoformat(report.date_range_start)
        except (TypeError, ValueError):
            continue
        if report_start != req_start:
            continue
        if report_end >= req_end or report_end < req_start:
            continue
        gap_days = (req_end - report_end).days
        if gap_days < 0 or gap_days > max_gap_days:
            continue
        if stored_profile_id == profile_id:
            return report
        if legacy_match is None:
            legacy_match = report
    return legacy_match


def _hydrate_cached_report_response(
    cached_report: Report,
    *,
    currency_code: str,
    report_pending_id: Optional[str] = None,
    sync_progress: Optional[dict] = None,
    sync_error: Optional[str] = None,
) -> dict:
    payload = copy.deepcopy(cached_report.report_data or {})
    payload["currency_code"] = currency_code
    payload["generated_at"] = utcnow().isoformat()
    payload["report_pending"] = report_pending_id is not None
    payload["report_pending_id"] = report_pending_id
    payload["sync_progress"] = sync_progress
    payload["sync_error"] = sync_error
    payload["cached_report_id"] = str(cached_report.id)
    payload["cached_report_completed_at"] = (
        cached_report.completed_at.isoformat() if cached_report.completed_at else None
    )
    return payload


def _parse_report_sync_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _get_report_sync_progress(sync_job: Optional[Report]) -> Optional[dict]:
    if not sync_job:
        return None
    raw = dict(sync_job.raw_response or {})
    return {
        "job_id": str(sync_job.id),
        "status": sync_job.status,
        "step": raw.get("step"),
        "progress_pct": raw.get("progress_pct") or 0,
        "days_synced": raw.get("days_synced") or 0,
        "days_total": raw.get("days_total") or 0,
        "current_date": raw.get("current_date"),
        "heartbeat_at": raw.get("heartbeat_at"),
        "started_at": raw.get("started_at"),
    }


def _coerce_report_sync_days(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _get_report_sync_resume_state(
    sync_job: Report,
    start_date: str,
    end_date: str,
) -> tuple[str, int, Optional[str]]:
    raw = dict(sync_job.raw_response or {})
    total_days = _days_inclusive(start_date, end_date)
    days_synced = min(_coerce_report_sync_days(raw.get("days_synced"), 0), total_days)

    resume_date = raw.get("current_date")
    if not resume_date:
        resume_date = (
            date_type.fromisoformat(start_date) + timedelta(days=days_synced)
        ).isoformat()

    try:
        resume_day = date_type.fromisoformat(resume_date)
    except (TypeError, ValueError):
        resume_day = date_type.fromisoformat(start_date) + timedelta(days=days_synced)

    range_start = date_type.fromisoformat(start_date)
    range_end = date_type.fromisoformat(end_date)
    if resume_day < range_start:
        resume_day = range_start
    if resume_day > range_end:
        resume_day = range_end

    return resume_day.isoformat(), days_synced, raw.get("pending_report_id")


def _is_report_sync_job_stale(sync_job: Optional[Report]) -> bool:
    if not sync_job or sync_job.status not in ("pending", "running"):
        return False

    raw = dict(sync_job.raw_response or {})
    last_heartbeat = (
        _parse_report_sync_timestamp(raw.get("heartbeat_at"))
        or _parse_report_sync_timestamp(raw.get("started_at"))
        or sync_job.created_at
    )
    if not last_heartbeat:
        return False

    age_seconds = (utcnow() - last_heartbeat).total_seconds()
    return age_seconds > REPORT_SYNC_STALE_SECONDS


async def _restart_report_sync_job(
    db: AsyncSession,
    sync_job: Report,
    reason: str = "Exact daily sync was resumed after a stale heartbeat.",
) -> Report:
    raw = dict(sync_job.raw_response or {})
    raw.update({
        "step": reason,
        "heartbeat_at": utcnow().isoformat(),
        "restart_requested_at": utcnow().isoformat(),
        "restart_count": _coerce_report_sync_days(raw.get("restart_count"), 0) + 1,
    })
    sync_job.status = "pending"
    sync_job.completed_at = None
    sync_job.raw_response = raw
    sync_job.report_data = {
        "status": "pending",
        "message": reason,
        "days_synced": _coerce_report_sync_days(raw.get("days_synced"), 0),
        "days_total": _coerce_report_sync_days(raw.get("days_total"), 0),
    }
    await db.commit()
    asyncio.create_task(_run_report_sync_background(sync_job.id))
    return sync_job


async def _mark_report_sync_job_stale(
    db: AsyncSession,
    sync_job: Report,
    reason: str = "Exact daily sync stopped reporting progress and was restarted.",
) -> None:
    raw = dict(sync_job.raw_response or {})
    raw.update({
        "step": "Failed",
        "error": reason,
        "heartbeat_at": utcnow().isoformat(),
        "stale": True,
    })
    sync_job.status = "failed"
    sync_job.completed_at = utcnow()
    sync_job.raw_response = raw
    sync_job.report_data = {"status": "failed", "message": reason}
    await db.commit()


async def _finalize_report_sync_job_if_ready(
    db: AsyncSession,
    sync_job: Optional[Report],
    profile_id: Optional[str],
    synced_days: int,
    expected_days: int,
) -> None:
    """
    If exact daily coverage already exists, stale/racing performance_sync jobs
    should no longer keep the UI in a pending state.
    """
    if not sync_job or sync_job.status not in ("pending", "running"):
        return

    raw = dict(sync_job.raw_response or {})
    if raw.get("profile_id") != profile_id:
        return

    sync_job.status = "completed"
    sync_job.completed_at = utcnow()
    sync_job.report_data = {
        "status": "completed",
        "days_synced": synced_days,
        "days_total": expected_days,
        "profile_id": profile_id,
        "completed_from": "coverage_check",
    }
    raw.update({
        "step": "Completed",
        "progress_pct": 100,
        "days_synced": synced_days,
        "days_total": expected_days,
        "heartbeat_at": utcnow().isoformat(),
    })
    sync_job.raw_response = raw
    await db.commit()


async def _clear_exact_daily_slice(
    db: AsyncSession,
    credential_id,
    report_date: str,
    profile_id: Optional[str],
) -> None:
    campaign_where = [
        CampaignPerformanceDaily.credential_id == credential_id,
        CampaignPerformanceDaily.date == report_date,
        func.strpos(CampaignPerformanceDaily.date, "__") <= 0,
    ]
    account_where = [
        AccountPerformanceDaily.credential_id == credential_id,
        AccountPerformanceDaily.date == report_date,
        func.strpos(AccountPerformanceDaily.date, "__") <= 0,
    ]
    if profile_id is not None:
        campaign_where.append(CampaignPerformanceDaily.profile_id == profile_id)
        account_where.append(AccountPerformanceDaily.profile_id == profile_id)
    else:
        campaign_where.append(CampaignPerformanceDaily.profile_id.is_(None))
        account_where.append(AccountPerformanceDaily.profile_id.is_(None))

    await db.execute(delete(CampaignPerformanceDaily).where(and_(*campaign_where)))
    await db.execute(delete(AccountPerformanceDaily).where(and_(*account_where)))


async def _clear_legacy_range_slice(
    db: AsyncSession,
    credential_id,
    start_date: str,
    end_date: str,
    profile_id: Optional[str],
) -> None:
    range_key = f"{start_date}__{end_date}"
    campaign_where = [
        CampaignPerformanceDaily.credential_id == credential_id,
        CampaignPerformanceDaily.date == range_key,
    ]
    account_where = [
        AccountPerformanceDaily.credential_id == credential_id,
        AccountPerformanceDaily.date == range_key,
    ]
    if profile_id is not None:
        campaign_where.append(CampaignPerformanceDaily.profile_id == profile_id)
        account_where.append(AccountPerformanceDaily.profile_id == profile_id)
    else:
        campaign_where.append(CampaignPerformanceDaily.profile_id.is_(None))
        account_where.append(AccountPerformanceDaily.profile_id.is_(None))

    await db.execute(delete(CampaignPerformanceDaily).where(and_(*campaign_where)))
    await db.execute(delete(AccountPerformanceDaily).where(and_(*account_where)))


def _should_abort_skipped_sync(
    skipped_count: int,
    total_days: int,
    max_skip_ratio: float = 0.5,
    floor: int = 2,
) -> bool:
    """Return True when so many days have failed that the sync should hard-fail.

    A small number of stuck days (Amazon refuses to produce data for specific
    historical dates) is benign and should not abort. But when most of the
    window fails, that's a systemic issue (auth/scope) and we want to surface
    it as an explicit failure, not as "completed_with_skips".

    The ``floor`` ensures very short syncs (e.g. 1–2 day catch-up runs)
    aren't aborted on a single failure.
    """
    if skipped_count <= floor:
        return False
    threshold = max(floor, int(total_days * max_skip_ratio))
    return skipped_count > threshold


async def _run_report_sync_background(report_id: uuid.UUID) -> None:
    async with async_session() as db:
        result = await db.execute(select(Report).where(Report.id == report_id))
        report = result.scalar_one_or_none()
        if not report:
            return

        raw = dict(report.raw_response or {})
        start_date = report.date_range_start
        end_date = report.date_range_end
        profile_id = raw.get("profile_id")
        total_days = _days_inclusive(start_date, end_date)

        try:
            cred_result = await db.execute(select(Credential).where(Credential.id == report.credential_id))
            cred = cred_result.scalar_one_or_none()
            if not cred:
                raise RuntimeError("Credential not found for report sync")

            resume_day_str, synced_days, pending_report_id = _get_report_sync_resume_state(
                report,
                start_date,
                end_date,
            )
            report.status = "running"
            raw.update({
                "profile_id": profile_id,
                "step": f"Preparing daily report sync for {resume_day_str}",
                "progress_pct": min(99, int((synced_days / max(total_days, 1)) * 100)),
                "days_synced": synced_days,
                "days_total": total_days,
                "started_at": raw.get("started_at") or utcnow().isoformat(),
                "heartbeat_at": utcnow().isoformat(),
                "current_date": resume_day_str,
            })
            if pending_report_id:
                raw["pending_report_id"] = pending_report_id
            else:
                raw.pop("pending_report_id", None)
            report.raw_response = raw
            await db.commit()
            logger.info(
                "Performance report sync started/resumed: report_id=%s profile_id=%s range=%s..%s total_days=%d synced_days=%d resume_day=%s",
                str(report.id), profile_id, start_date, end_date, total_days, synced_days, resume_day_str,
            )

            client = await get_mcp_client_with_fresh_token(
                cred,
                db,
                profile_id_override=profile_id,
            )
            advertiser_account_id = await _resolve_advertiser_account_id(
                db,
                cred,
                profile_id_override=profile_id,
            )
            service = ReportingService(client, advertiser_account_id=advertiser_account_id)

            current = date_type.fromisoformat(resume_day_str)
            end = date_type.fromisoformat(end_date)

            # Track per-day failures so a single broken date (Amazon
            # occasionally refuses to produce reports for specific historical
            # dates) doesn't abort the whole 30-day sync. Without this, every
            # daily cron retries the same broken day forever and never
            # produces fresh data — the 2026-03-28 loop bug observed in prod.
            existing_skipped = (report.raw_response or {}).get("skipped_days") or []
            skipped_days: list[dict] = list(existing_skipped) if isinstance(existing_skipped, list) else []
            # Honour a soft cap so a totally broken sync (e.g. expired token)
            # still surfaces as a failure, instead of "completed with 30/30 skips".
            max_skip_ratio = 0.5

            # Phase 5: respect the credential-scoped permanent skip list.
            # Dates that have failed across ``PROMOTE_THRESHOLD`` consecutive
            # syncs are flagged as doomed and we don't even try them again.
            permanent_skip = get_permanent_skip_dates(cred, profile_id)
            permanent_skipped_this_run: list[str] = []
            synced_day_strs: list[str] = []

            while current <= end:
                day_str = current.isoformat()
                if day_str in permanent_skip:
                    permanent_skipped_this_run.append(day_str)
                    skipped_days.append({
                        "date": day_str,
                        "error": "permanent_skip: previously promoted by report_skip_service",
                        "permanent": True,
                    })
                    raw = dict(report.raw_response or {})
                    raw["skipped_days"] = skipped_days
                    raw["heartbeat_at"] = utcnow().isoformat()
                    raw["current_date"] = (current + timedelta(days=1)).isoformat() \
                        if current < end else day_str
                    raw["permanent_skipped"] = list(permanent_skipped_this_run)
                    raw.pop("pending_report_id", None)
                    report.raw_response = raw
                    await db.commit()
                    logger.info(
                        "Report sync skipping permanently-flagged date day=%s report_id=%s",
                        day_str, str(report.id),
                    )
                    current += timedelta(days=1)
                    continue
                raw = dict(report.raw_response or {})
                if raw.get("current_date") != day_str:
                    pending_report_id = None
                raw.update({
                    "step": f"Syncing exact daily performance for {day_str}",
                    "progress_pct": min(99, int((synced_days / max(total_days, 1)) * 100)),
                    "days_synced": synced_days,
                    "days_total": total_days,
                    "current_date": day_str,
                    "heartbeat_at": utcnow().isoformat(),
                })
                if pending_report_id:
                    raw["pending_report_id"] = pending_report_id
                else:
                    raw.pop("pending_report_id", None)
                if skipped_days:
                    raw["skipped_days"] = skipped_days
                report.raw_response = raw
                await db.commit()
                logger.info(
                    "Performance report sync progress: report_id=%s day=%s (%d/%d)",
                    str(report.id), day_str, synced_days, total_days,
                )

                day_failed_reason: Optional[str] = None
                day_result: dict = {}
                try:
                    while True:
                        raw = dict(report.raw_response or {})
                        raw.update({
                            "step": (
                                f"Polling pending Amazon report for {day_str}"
                                if pending_report_id
                                else f"Fetching Amazon report for {day_str}"
                            ),
                            "heartbeat_at": utcnow().isoformat(),
                        })
                        if pending_report_id:
                            raw["pending_report_id"] = pending_report_id
                        else:
                            raw.pop("pending_report_id", None)
                        report.raw_response = raw
                        await db.commit()
                        day_result = await service.generate_mcp_report(
                            day_str,
                            day_str,
                            pending_report_id=pending_report_id,
                            max_wait=180,
                        )
                        if not day_result or (
                            "campaigns" not in day_result
                            and "_pending_report_id" not in day_result
                        ):
                            raise RuntimeError(
                                f"Amazon report fetch failed for {day_str}"
                            )
                        if day_result.get("_pending_report_id"):
                            pending_report_id = day_result["_pending_report_id"]
                            raw = dict(report.raw_response or {})
                            raw.update({
                                "step": f"Amazon report still processing for {day_str}; continuing to poll",
                                "days_synced": synced_days,
                                "days_total": total_days,
                                "current_date": day_str,
                                "progress_pct": min(99, int((synced_days / max(total_days, 1)) * 100)),
                                "heartbeat_at": utcnow().isoformat(),
                                "pending_report_id": pending_report_id,
                            })
                            report.raw_response = raw
                            await db.commit()
                            await asyncio.sleep(30)
                            continue
                        pending_report_id = None
                        break
                except Exception as day_err:
                    day_failed_reason = str(day_err)[:300]
                    pending_report_id = None
                    logger.warning(
                        "Performance report sync skipping day=%s after error: %s",
                        day_str,
                        day_err,
                    )

                if day_failed_reason is not None:
                    skipped_days.append({"date": day_str, "error": day_failed_reason})
                    raw = dict(report.raw_response or {})
                    raw.update({
                        "skipped_days": skipped_days,
                        "heartbeat_at": utcnow().isoformat(),
                        "current_date": (current + timedelta(days=1)).isoformat()
                            if current < end else day_str,
                    })
                    raw.pop("pending_report_id", None)
                    report.raw_response = raw
                    await db.commit()
                    # Only count *new* failures against the 50% abort
                    # threshold — pre-promoted permanent skips are an
                    # optimisation, not a fresh systemic failure.
                    new_failure_count = sum(
                        1 for s in skipped_days
                        if isinstance(s, dict) and not s.get("permanent")
                    )
                    if _should_abort_skipped_sync(
                        new_failure_count, total_days, max_skip_ratio=max_skip_ratio
                    ):
                        raise RuntimeError(
                            f"Aborting sync: {new_failure_count} of {total_days} days "
                            f"failed (>{int(max_skip_ratio*100)}%). Last error: {day_failed_reason}"
                        )
                    current += timedelta(days=1)
                    continue

                day_rows = service.parse_report_campaign_rows(day_result)
                for row in day_rows:
                    row["report_date"] = row.get("report_date") or day_str

                await _clear_exact_daily_slice(db, cred.id, day_str, profile_id)
                if day_rows:
                    await store_campaign_rows_by_date(
                        db,
                        cred.id,
                        day_rows,
                        day_str,
                        source="performance_sync",
                        profile_id=profile_id,
                    )
                else:
                    await store_account_daily_summary(
                        db,
                        cred.id,
                        [],
                        day_str,
                        source="performance_sync",
                        profile_id=profile_id,
                    )
                synced_days += 1
                synced_day_strs.append(day_str)
                next_day = current + timedelta(days=1)
                raw = dict(report.raw_response or {})
                raw.update({
                    "days_synced": synced_days,
                    "progress_pct": min(99, int((synced_days / max(total_days, 1)) * 100)),
                    "heartbeat_at": utcnow().isoformat(),
                    "current_date": next_day.isoformat() if next_day <= end else day_str,
                })
                raw.pop("pending_report_id", None)
                if skipped_days:
                    raw["skipped_days"] = skipped_days
                report.raw_response = raw
                await db.commit()
                logger.info(
                    "Performance report sync stored: report_id=%s day=%s synced_days=%d/%d rows=%d",
                    str(report.id), day_str, synced_days, total_days, len(day_rows),
                )
                current += timedelta(days=1)

            await _clear_legacy_range_slice(db, cred.id, start_date, end_date, profile_id)

            # Phase 5: promote chronically-failing dates to the permanent
            # skip list and clear counters / permanent flags for dates that
            # finally succeeded. Filter the ones that were already in the
            # permanent list this run — they were not new failures.
            new_skipped_for_counter = [
                s for s in skipped_days
                if isinstance(s, dict) and not s.get("permanent")
            ]
            skip_state_report = await update_skip_state_after_sync(
                db,
                cred,
                profile_id,
                skipped_days=new_skipped_for_counter,
                synced_day_strs=synced_day_strs,
            )

            completed_with_skips = bool(skipped_days)
            # Keep status="completed" (don't break legacy lookups that filter on
            # exact match). Surface the skip count via report_data + raw_response
            # metadata + activity log description instead.
            report.status = "completed"
            report.completed_at = utcnow()
            report.report_data = {
                "status": "completed_with_skips" if completed_with_skips else "completed",
                "days_synced": synced_days,
                "days_total": total_days,
                "days_skipped": len(skipped_days),
                "skipped_days": skipped_days,
                "profile_id": profile_id,
                "skip_state": skip_state_report,
                "permanent_skipped_this_run": permanent_skipped_this_run,
            }
            raw = dict(report.raw_response or {})
            raw.update({
                "step": "Completed with skips" if completed_with_skips else "Completed",
                "progress_pct": 100,
                "days_synced": synced_days,
                "days_total": total_days,
                "heartbeat_at": utcnow().isoformat(),
            })
            raw.pop("pending_report_id", None)
            raw.pop("current_date", None)
            if skipped_days:
                raw["skipped_days"] = skipped_days
            report.raw_response = raw
            description_parts: list[str] = [
                f"Daily performance sync completed for {start_date} – {end_date}"
            ]
            if completed_with_skips:
                description_parts.append(
                    f" with {len(skipped_days)} day(s) skipped "
                    f"({', '.join(s['date'] for s in skipped_days[:5])}"
                    f"{'…' if len(skipped_days) > 5 else ''})"
                )
            promoted = skip_state_report.get("promoted_to_permanent") or []
            if promoted:
                description_parts.append(
                    f" — promoted to permanent skip list: {', '.join(promoted[:5])}"
                    f"{'…' if len(promoted) > 5 else ''}"
                )
            cleared = skip_state_report.get("cleared_after_success") or []
            if cleared:
                description_parts.append(
                    f" — cleared from skip list: {', '.join(cleared[:5])}"
                    f"{'…' if len(cleared) > 5 else ''}"
                )
            description = "".join(description_parts)
            db.add(ActivityLog(
                credential_id=cred.id,
                action="performance_sync",
                category="reporting",
                description=description,
                entity_type="report",
                entity_id=str(report.id),
                details=report.report_data,
                status="warning" if completed_with_skips else "success",
            ))
            await db.commit()
        except Exception as exc:
            logger.exception("Performance report sync failed: %s", exc)
            await db.rollback()
            fail_result = await db.execute(select(Report).where(Report.id == report_id))
            failed_report = fail_result.scalar_one_or_none()
            if failed_report:
                raw = dict(failed_report.raw_response or {})
                raw.update({
                    "step": "Failed",
                    "error": str(exc),
                    "heartbeat_at": utcnow().isoformat(),
                })
                failed_report.status = "failed"
                failed_report.completed_at = utcnow()
                failed_report.raw_response = raw
                failed_report.report_data = {"status": "failed", "message": str(exc)}
                await db.commit()


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


def _snapshot_rows_to_trend(snapshots: list[AuditSnapshot]) -> list[dict]:
    """
    Collapse snapshots to one point per day (latest snapshot for each day).
    """
    by_day = {}
    for s in snapshots:
        day = s.created_at.strftime("%Y-%m-%d")
        prev = by_day.get(day)
        if not prev or s.created_at > prev.created_at:
            by_day[day] = s

    trend = []
    for day in sorted(by_day.keys()):
        s = by_day[day]
        trend.append({
            "date": day,
            "spend": s.total_spend or 0,
            "sales": s.total_sales or 0,
            "acos": s.avg_acos or 0,
            "roas": s.avg_roas or 0,
            "campaigns": s.campaigns_count or 0,
            "active": s.active_campaigns or 0,
            "waste": s.waste_identified or 0,
            "issues": s.issues_count or 0,
            "opportunities": s.opportunities_count or 0,
        })
    return trend


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

    has_exact_daily, _, _ = await _get_exact_daily_coverage(
        db, cred.id, start_str, end_str, profile_id=cred.profile_id
    )
    daily_campaigns = []
    if has_exact_daily:
        daily_campaigns = await query_campaign_daily(
            db, cred.id, start_str, end_str, profile_id=cred.profile_id
        )
    else:
        cached_report = await _find_completed_performance_report(
            db,
            cred.id,
            start_str,
            end_str,
            profile_id=cred.profile_id,
        )
        if not cached_report:
            cached_report = await _find_recent_rolling_performance_report(
                db,
                cred.id,
                start_str,
                end_str,
                profile_id=cred.profile_id,
            )
        cached_payload = (
            cached_report.report_data
            if cached_report and isinstance(cached_report.report_data, dict)
            else {}
        )
        if cached_payload:
            cached_campaigns = cached_payload.get("campaigns") or []
            return {
                "summary": cached_payload.get("summary") or {},
                "total_campaigns": len(cached_campaigns),
                "active_campaigns": len([
                    c for c in cached_campaigns
                    if (c.get("state") or "").lower() in ("enabled", "active")
                ]),
                "paused_campaigns": len([
                    c for c in cached_campaigns
                    if (c.get("state") or "").lower() == "paused"
                ]),
                "campaigns": cached_campaigns,
                "top_performers": cached_payload.get("top_performers") or [],
                "worst_performers": cached_payload.get("worst_performers") or [],
                "has_historical_data": True,
                "requires_sync": False,
                "last_synced": cached_report.completed_at.isoformat() if cached_report.completed_at else None,
                "currency_code": currency_code,
                "period": {
                    "start_date": start_str,
                    "end_date": end_str,
                    "preset": preset if preset and preset in DATE_PRESETS else "this_month",
                },
                "latest_snapshot": None,
                "report_source": cached_payload.get("report_source") or "cached_report",
                "data_may_not_match_range": (
                    cached_report.date_range_end != end_str if cached_report else False
                ),
            }

    has_history = len(daily_campaigns) > 0
    last_synced = utcnow().isoformat() if has_history else None
    enriched = daily_campaigns if daily_campaigns else []

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
        "requires_sync": not has_history,
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
    """Daily trend data from exact account_performance_daily rows only."""
    cred = await _get_cred(db, credential_id)
    logger.info("Reports trends: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)

    # Compute date range from preset or explicit dates
    start_d, end_d, _ = _resolve_date_range(preset or "this_month", start_date, end_date)
    start_date = start_d
    end_date = end_d

    exact_daily_ready, _, _ = await _get_exact_daily_coverage(
        db,
        cred.id,
        start_date.isoformat(),
        end_date.isoformat(),
        profile_id=cred.profile_id,
    )
    if exact_daily_ready:
        daily_trend = await query_account_daily_trend(
            db, cred.id, start_date.isoformat(), end_date.isoformat(), profile_id=cred.profile_id
        )
        if daily_trend:
            return {"source": "daily_history", "data": daily_trend}

    cached_report = await _find_completed_performance_report(
        db,
        cred.id,
        start_date.isoformat(),
        end_date.isoformat(),
        profile_id=cred.profile_id,
    )
    if not cached_report:
        cached_report = await _find_recent_rolling_performance_report(
            db,
            cred.id,
            start_date.isoformat(),
            end_date.isoformat(),
            profile_id=cred.profile_id,
        )
    cached_payload = (
        cached_report.report_data
        if cached_report and isinstance(cached_report.report_data, dict)
        else {}
    )
    cached_trend = cached_payload.get("daily_trend") or []
    if cached_trend:
        return {
            "source": cached_payload.get("report_source") or "cached_report",
            "data": cached_trend,
        }

    return {"source": "none", "data": []}


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

    campaigns_data = []
    daily_trend = []
    mcp_report_raw = {}
    report_source = "database"
    report_pending_id = None
    sync_error = None
    sync_progress = None

    # For single-day ranges, use plain ISO date; for multi-day, use range key
    is_single_day = start_str == end_str

    # ── Step 0: Ensure Campaign table has metadata (state, type, budget)
    camp_count_query = select(func.count(Campaign.id)).where(Campaign.credential_id == cred.id)
    if cred.profile_id is not None:
        camp_count_query = camp_count_query.where(Campaign.profile_id == cred.profile_id)
    else:
        camp_count_query = camp_count_query.where(Campaign.profile_id.is_(None))
    camp_count_result = await db.execute(camp_count_query)
    if (camp_count_result.scalar() or 0) == 0:
        _, scope_error = await resolve_campaign_sync_scope(db, cred)
        if scope_error:
            logger.info("Skipping campaign metadata auto-sync during report generation: %s", scope_error)
        else:
            try:
                client = await get_mcp_client_with_fresh_token(cred, db)
                raw_campaigns = await client.query_campaigns()
                from app.services.reporting_service import sync_campaigns_to_db
                await sync_campaigns_to_db(db, cred.id, raw_campaigns, profile_id=cred.profile_id)
                logger.info("Auto-synced campaigns to Campaign table during report generation")
            except Exception as e:
                logger.warning(f"Campaign auto-sync failed: {e}")

    logger.info("Report generate: date range %s to %s (preset=%s)", start_str, end_str, preset)
    exact_daily_ready, synced_days, expected_days = await _get_exact_daily_coverage(
        db,
        cred.id,
        start_str,
        end_str,
        profile_id=cred.profile_id,
    )
    sync_job = await _find_report_sync_job(
        db,
        cred.id,
        start_str,
        end_str,
        profile_id=cred.profile_id,
    )
    if _is_report_sync_job_stale(sync_job):
        logger.warning(
            "Performance report sync stale; restarting: report_id=%s profile_id=%s range=%s..%s",
            str(sync_job.id), cred.profile_id, start_str, end_str,
        )
        sync_job = await _restart_report_sync_job(
            db,
            sync_job,
            reason="Exact daily sync heartbeat went stale; resuming saved progress.",
        )

    if exact_daily_ready:
        await _finalize_report_sync_job_if_ready(
            db,
            sync_job,
            cred.profile_id,
            synced_days,
            expected_days,
        )
        campaigns_data = await query_campaign_daily(
            db,
            cred.id,
            start_str,
            end_str,
            profile_id=cred.profile_id,
        )
        daily_trend = await query_account_daily_trend(
            db,
            cred.id,
            start_str,
            end_str,
            profile_id=cred.profile_id,
        )
        report_source = "daily_history"
    else:
        report_source = "sync_pending"
        recent_failed = (
            sync_job
            and sync_job.status == "failed"
            and sync_job.completed_at
            and (utcnow() - sync_job.completed_at).total_seconds() < 120
        )
        if sync_job and sync_job.status in ("pending", "running"):
            report_pending_id = str(sync_job.id)
            sync_progress = _get_report_sync_progress(sync_job)
        elif recent_failed:
            sync_error = (sync_job.report_data or {}).get("message") or (sync_job.raw_response or {}).get("error")
            report_source = "sync_failed"
            sync_progress = _get_report_sync_progress(sync_job)
        else:
            sync_job = Report(
                credential_id=cred.id,
                report_type="performance_sync",
                ad_product="ALL",
                date_range_start=start_str,
                date_range_end=end_str,
                status="pending",
                raw_response={
                    "profile_id": cred.profile_id,
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
            report_pending_id = str(sync_job.id)
            await db.commit()
            asyncio.create_task(_run_report_sync_background(sync_job.id))
            sync_progress = _get_report_sync_progress(sync_job)

        cached_report = await _find_completed_performance_report(
            db,
            cred.id,
            start_str,
            end_str,
            profile_id=cred.profile_id,
        )
        if not cached_report:
            cached_report = await _find_recent_rolling_performance_report(
                db,
                cred.id,
                start_str,
                end_str,
                profile_id=cred.profile_id,
            )
        if cached_report and isinstance(cached_report.report_data, dict):
            response = _hydrate_cached_report_response(
                cached_report,
                currency_code=currency_code,
                report_pending_id=report_pending_id,
                sync_progress=sync_progress,
                sync_error=sync_error,
            )
            response["data_may_not_match_range"] = cached_report.date_range_end != end_str
            return response

        campaigns_data = []
        daily_trend = []

    # ── Compute summary ───────────────────────────────────────────────
    summary = compute_metrics(campaigns_data)

    # Log metrics for comparison with Amazon Ads dashboard
    logger.info(
        "Report metrics [%s–%s] source=%s campaigns=%d | spend=%.2f sales=%.2f clicks=%d impressions=%d orders=%d acos=%.1f%% roas=%.2f",
        start_str,
        end_str,
        report_source,
        len(campaigns_data),
        summary.get("spend", 0),
        summary.get("sales", 0),
        summary.get("clicks", 0),
        summary.get("impressions", 0),
        summary.get("orders", 0),
        summary.get("acos", 0),
        summary.get("roas", 0),
    )

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

    data_may_not_match = False

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
        "sync_progress": sync_progress,
        "data_may_not_match_range": data_may_not_match,
        "sync_error": sync_error,
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
        comp_source = "unavailable"
        comp_daily_trend = []
        comp_exact_ready, comp_synced_days, comp_expected_days = await _get_exact_daily_coverage(
            db,
            cred.id,
            comp_start_str,
            comp_end_str,
            profile_id=cred.profile_id,
        )
        comp_job = await _find_report_sync_job(
            db,
            cred.id,
            comp_start_str,
            comp_end_str,
            profile_id=cred.profile_id,
        )
        if _is_report_sync_job_stale(comp_job):
            logger.warning(
                "Comparison performance report sync stale; restarting: report_id=%s profile_id=%s range=%s..%s",
                str(comp_job.id), cred.profile_id, comp_start_str, comp_end_str,
            )
            comp_job = await _restart_report_sync_job(
                db,
                comp_job,
                reason="Comparison exact daily sync heartbeat went stale; resuming saved progress.",
            )

        if comp_exact_ready:
            await _finalize_report_sync_job_if_ready(
                db,
                comp_job,
                cred.profile_id,
                comp_synced_days,
                comp_expected_days,
            )
            comp_campaigns = await query_campaign_daily(
                db, cred.id, comp_start_str, comp_end_str, profile_id=cred.profile_id
            )
            comp_source = "daily_history"
            comp_summary = compute_metrics(comp_campaigns)
            deltas = compute_deltas(summary, comp_summary)
            comp_daily_trend = await query_account_daily_trend(
                db, cred.id, comp_start_str, comp_end_str, profile_id=cred.profile_id
            )
        else:
            comp_summary = {}
            deltas = {}
            comp_recent_failed = (
                comp_job
                and comp_job.status == "failed"
                and comp_job.completed_at
                and (utcnow() - comp_job.completed_at).total_seconds() < 120
            )
            if comp_job and comp_job.status in ("pending", "running"):
                report_pending_id = report_pending_id or str(comp_job.id)
            elif not comp_recent_failed:
                comp_job = Report(
                    credential_id=cred.id,
                    report_type="performance_sync",
                    ad_product="ALL",
                    date_range_start=comp_start_str,
                    date_range_end=comp_end_str,
                    status="pending",
                    raw_response={
                        "profile_id": cred.profile_id,
                        "step": "Queued exact daily sync...",
                        "progress_pct": 0,
                        "days_synced": comp_synced_days,
                        "days_total": comp_expected_days,
                        "queued_at": utcnow().isoformat(),
                        "heartbeat_at": utcnow().isoformat(),
                    },
                )
                db.add(comp_job)
                await db.flush()
                report_pending_id = report_pending_id or str(comp_job.id)
                await db.commit()
                asyncio.create_task(_run_report_sync_background(comp_job.id))

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
            "unavailable": comp_source == "unavailable",
        }
        response["report_pending"] = report_pending_id is not None
        response["report_pending_id"] = report_pending_id

    # ── Save to reports table ─────────────────────────────────────────
    try:
        if response["report_pending"] or sync_error:
            return response
        report = Report(
            credential_id=cred.id,
            report_type="performance",
            ad_product="ALL",
            date_range_start=start_str,
            date_range_end=end_str,
            status="completed",
            report_data={**response, "profile_id": cred.profile_id},
            raw_response={
                **(mcp_report_raw or {}),
                "profile_id": cred.profile_id,
            } or None,
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
        .where(
            Report.credential_id == cred.id,
            Report.report_type != "performance_sync",
        )
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


@router.delete("/history/{report_id}")
async def delete_report(
    report_id: str,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Delete a report from history. Removes the stored report record."""
    cred = await _get_cred(db, credential_id)
    result = await db.execute(select(Report).where(Report.id == parse_uuid(report_id, "report_id")))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.credential_id != cred.id:
        raise HTTPException(status_code=403, detail="Report does not belong to your account.")
    await db.delete(report)
    await db.flush()
    return {"status": "deleted", "id": report_id}


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
    cred_id = cred.id
    cred_profile_id = cred.profile_id

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
        credential_id=cred_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        ad_product=payload.ad_product,
        pending_report_id=payload.pending_report_id,
        profile_id=cred_profile_id,
    )

    # Log activity
    db.add(ActivityLog(
        credential_id=cred_id,
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
    start_date: Optional[str] = Query(None, description="Filter by report range start (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Filter by report range end (YYYY-MM-DD)"),
    min_clicks: int = Query(0),
    non_converting_only: bool = Query(False),
    limit: int = Query(100),
    sort_by: str = Query("cost"),
    db: AsyncSession = Depends(get_db),
):
    """
    Query stored search term data with filters.
    Useful for viewing search terms in the UI or exporting.
    Filters by report_date_start/end when start_date and end_date provided.
    """
    cred = await _get_cred(db, credential_id)
    logger.info("Reports search-terms: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)

    query = select(SearchTermPerformance).where(SearchTermPerformance.credential_id == cred.id)
    if cred.profile_id is not None:
        query = query.where(SearchTermPerformance.profile_id == cred.profile_id)
    else:
        query = query.where(SearchTermPerformance.profile_id.is_(None))

    if start_date and end_date:
        query = query.where(SearchTermPerformance.report_date_start <= end_date)
        query = query.where(SearchTermPerformance.report_date_end >= start_date)

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
    start_date: Optional[str] = Query(None, description="Filter by report range start (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Filter by report range end (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """Get a summary of stored search term data for the account."""
    cred = await _get_cred(db, credential_id)
    logger.info("Reports search-terms/summary: credential_id=%s profile_id=%s", str(cred.id), cred.profile_id)
    summary = await get_search_term_summary(
        db,
        cred.id,
        profile_id=cred.profile_id,
        start_date=start_date,
        end_date=end_date,
    )
    return summary


# ══════════════════════════════════════════════════════════════════════
#  PRODUCT / BUSINESS REPORTS — Sync and query product analytics
# ══════════════════════════════════════════════════════════════════════

class ProductSyncRequest(BaseModel):
    credential_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    ad_product: str = "SPONSORED_PRODUCTS"
    pending_report_id: Optional[str] = None


@router.post("/products/sync")
async def sync_product_reports(
    payload: ProductSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger product/business report sync from Amazon Ads.
    Stores daily per-product rows for analytics and comparisons.
    """
    cred = await _get_cred(db, payload.credential_id)
    client = await get_mcp_client_with_fresh_token(cred, db)
    if not client:
        raise HTTPException(status_code=503, detail="Could not create MCP client. Check credentials.")

    advertiser_account_id = await _resolve_advertiser_account_id(db, cred)
    logger.info("Product report sync using advertiser_account_id: %s", advertiser_account_id)

    service = ProductReportingService(client, advertiser_account_id)
    result = await service.sync_products(
        db=db,
        credential_id=cred.id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        ad_product=payload.ad_product,
        pending_report_id=payload.pending_report_id,
        profile_id=cred.profile_id,
    )

    db.add(ActivityLog(
        credential_id=cred.id,
        action="product_report_sync",
        category="reporting",
        description=f"Product report sync: {result.get('status', '?')} — {result.get('rows_stored', 0)} rows",
        details=result,
    ))
    await db.flush()
    return result


@router.get("/products")
async def get_products(
    credential_id: Optional[str] = Query(None),
    preset: Optional[str] = Query("this_month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100),
    sort_by: str = Query("sales"),
    db: AsyncSession = Depends(get_db),
):
    """Query stored product performance rows for the selected date range."""
    cred = await _get_cred(db, credential_id)
    start_d, end_d, _ = _resolve_date_range(preset or "this_month", start_date, end_date)
    start_str = start_d.isoformat()
    end_str = end_d.isoformat()

    rows = await query_product_rows(
        db=db,
        credential_id=cred.id,
        start_date=start_str,
        end_date=end_str,
        profile_id=cred.profile_id,
        limit=limit,
        sort_by=sort_by,
    )
    return {
        "total": len(rows),
        "products": rows,
        "date_range": f"{start_str} to {end_str}",
    }


@router.get("/products/summary")
async def product_summary(
    credential_id: Optional[str] = Query(None),
    preset: Optional[str] = Query("this_month"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    compare: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """
    Product analytics summary for the selected range with optional previous-period comparison.
    """
    cred = await _get_cred(db, credential_id)
    start_d, end_d, _ = _resolve_date_range(preset or "this_month", start_date, end_date)
    start_str = start_d.isoformat()
    end_str = end_d.isoformat()

    current = await get_product_summary(
        db=db,
        credential_id=cred.id,
        start_date=start_str,
        end_date=end_str,
        profile_id=cred.profile_id,
    )
    response = {
        **current,
        "period": {
            "start_date": start_str,
            "end_date": end_str,
            "preset": preset if preset and preset in DATE_PRESETS else "this_month",
        },
    }

    if compare:
        if start_date and end_date:
            comp_start, comp_end = get_comparison_range_for_dates(start_d, end_d)
        else:
            comp_start, comp_end = get_comparison_range(preset if preset in DATE_PRESETS else "this_month")
        comp_start_str = comp_start.isoformat()
        comp_end_str = comp_end.isoformat()

        previous = await get_product_summary(
            db=db,
            credential_id=cred.id,
            start_date=comp_start_str,
            end_date=comp_end_str,
            profile_id=cred.profile_id,
        )
        if previous.get("has_data"):
            deltas = compute_deltas(current.get("summary", {}), previous.get("summary", {}))
            response["comparison"] = {
                "period": {
                    "start_date": comp_start_str,
                    "end_date": comp_end_str,
                },
                "summary": previous.get("summary", {}),
                "deltas": deltas,
                "unavailable": False,
            }
        else:
            response["comparison"] = {
                "period": {
                    "start_date": comp_start_str,
                    "end_date": comp_end_str,
                },
                "summary": {},
                "deltas": {},
                "unavailable": True,
            }

    return response
