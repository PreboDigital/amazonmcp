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

import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.routers.campaigns import run_full_sync
from app.routers.reporting import _get_cred, _resolve_advertiser_account_id
from app.services.reporting_service import (
    ReportingService,
    sync_campaigns_to_db,
    store_campaign_daily_data,
    store_account_daily_summary,
)
from app.services.search_term_service import SearchTermService
from app.services.token_service import get_mcp_client_with_fresh_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron", tags=["Cron"])


def _get_cron_secret() -> str:
    import os
    return os.environ.get("CRON_SECRET", "")


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
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """
    Scheduled campaign sync. Call from QStash:
    POST https://your-app.railway.app/api/cron/sync
    Header: X-Cron-Secret: <CRON_SECRET>
    """
    try:
        result = await run_full_sync(db, None)
        logger.info(f"Cron sync completed: {result}")
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("Cron sync failed")
        raise HTTPException(500, str(e))


@router.post("/reports")
async def cron_reports(
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """
    Scheduled report generation (last 7 days). Call from QStash:
    POST https://your-app.railway.app/api/cron/reports
    Header: X-Cron-Secret: <CRON_SECRET>
    """
    try:
        cred = await _get_cred(db, None)
        end_d = date.today()
        start_d = end_d - timedelta(days=6)
        start_str = start_d.isoformat()
        end_str = end_d.isoformat()

        client = await get_mcp_client_with_fresh_token(cred, db)
        adv_id = await _resolve_advertiser_account_id(db, cred)
        service = ReportingService(client, advertiser_account_id=adv_id)

        mcp_result = await service.generate_mcp_report(
            start_str, end_str, max_wait=180
        )
        if mcp_result.get("_pending_report_id"):
            return {"status": "pending", "report_id": mcp_result["_pending_report_id"]}

        parsed = service.parse_report_campaigns(mcp_result)
        if parsed:
            await sync_campaigns_to_db(db, cred.id, parsed, profile_id=cred.profile_id)
            for d in (start_d + timedelta(days=i) for i in range(7)):
                ds = d.isoformat()
                await store_campaign_daily_data(db, cred.id, parsed, ds, source="cron", profile_id=cred.profile_id)
                await store_account_daily_summary(db, cred.id, parsed, ds, source="cron", profile_id=cred.profile_id)

        return {"status": "ok", "rows": len(parsed) if parsed else 0}
    except Exception as e:
        logger.exception("Cron reports failed")
        raise HTTPException(500, str(e))


@router.post("/search-terms")
async def cron_search_terms(
    _: None = Depends(_require_cron_secret),
    db: AsyncSession = Depends(get_db),
):
    """
    Scheduled search term sync. Call from QStash:
    POST https://your-app.railway.app/api/cron/search-terms
    Header: X-Cron-Secret: <CRON_SECRET>
    """
    try:
        cred = await _get_cred(db, None)
        client = await get_mcp_client_with_fresh_token(cred, db)
        adv_id = await _resolve_advertiser_account_id(db, cred)
        service = SearchTermService(client, adv_id)

        end_d = date.today()
        start_d = end_d - timedelta(days=6)
        result = await service.sync_search_terms(
            db=db,
            credential_id=cred.id,
            start_date=start_d.isoformat(),
            end_date=end_d.isoformat(),
            profile_id=cred.profile_id,
        )
        logger.info(f"Cron search terms: {result}")
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("Cron search terms failed")
        raise HTTPException(500, str(e))


# ── Admin-only manual trigger (no CRON_SECRET) ──────────────────────────

async def _run_reports(db: AsyncSession):
    """Shared logic for reports cron."""
    cred = await _get_cred(db, None)
    end_d = date.today()
    start_d = end_d - timedelta(days=6)
    start_str = start_d.isoformat()
    end_str = end_d.isoformat()
    client = await get_mcp_client_with_fresh_token(cred, db)
    adv_id = await _resolve_advertiser_account_id(db, cred)
    service = ReportingService(client, advertiser_account_id=adv_id)
    mcp_result = await service.generate_mcp_report(start_str, end_str, max_wait=180)
    if mcp_result.get("_pending_report_id"):
        return {"status": "pending", "report_id": mcp_result["_pending_report_id"]}
    parsed = service.parse_report_campaigns(mcp_result)
    if parsed:
        await sync_campaigns_to_db(db, cred.id, parsed, profile_id=cred.profile_id)
        for d in (start_d + timedelta(days=i) for i in range(7)):
            ds = d.isoformat()
            await store_campaign_daily_data(db, cred.id, parsed, ds, source="cron", profile_id=cred.profile_id)
            await store_account_daily_summary(db, cred.id, parsed, ds, source="cron", profile_id=cred.profile_id)
    return {"status": "ok", "rows": len(parsed) if parsed else 0}


async def _run_search_terms(db: AsyncSession):
    """Shared logic for search terms cron."""
    cred = await _get_cred(db, None)
    client = await get_mcp_client_with_fresh_token(cred, db)
    adv_id = await _resolve_advertiser_account_id(db, cred)
    service = SearchTermService(client, adv_id)
    end_d = date.today()
    start_d = end_d - timedelta(days=6)
    return await service.sync_search_terms(
        db=db,
        credential_id=cred.id,
        start_date=start_d.isoformat(),
        end_date=end_d.isoformat(),
        profile_id=cred.profile_id,
    )


@router.post("/trigger/sync")
async def trigger_sync(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger campaign sync. Admin only."""
    try:
        result = await run_full_sync(db, None)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("Manual sync failed")
        raise HTTPException(500, str(e))


@router.post("/trigger/reports")
async def trigger_reports(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger report generation. Admin only."""
    try:
        return await _run_reports(db)
    except Exception as e:
        logger.exception("Manual reports failed")
        raise HTTPException(500, str(e))


@router.post("/trigger/search-terms")
async def trigger_search_terms(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger search term sync. Admin only."""
    try:
        result = await _run_search_terms(db)
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.exception("Manual search terms failed")
        raise HTTPException(500, str(e))


# Job type -> cron path suffix
CRON_JOB_PATHS = {"sync": "/api/cron/sync", "reports": "/api/cron/reports", "search-terms": "/api/cron/search-terms"}


class CreateScheduleRequest(BaseModel):
    job: str  # sync | reports | search-terms
    cron: str  # e.g. "0 */6 * * *"


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


@router.post("/schedules")
async def create_schedule(
    body: CreateScheduleRequest,
    _: User = Depends(require_admin),
):
    """Create a QStash schedule. Admin only. Requires QSTASH_TOKEN and CRON_SECRET."""
    from urllib.parse import quote
    from app.config import get_settings
    import httpx
    job = body.job
    cron = body.cron
    if job not in CRON_JOB_PATHS:
        raise HTTPException(400, f"Invalid job. Use: {list(CRON_JOB_PATHS.keys())}")
    if not cron or not isinstance(cron, str):
        raise HTTPException(400, "cron expression is required")
    settings = get_settings()
    if not settings.qstash_token:
        raise HTTPException(500, "QSTASH_TOKEN not configured")
    secret = _get_cron_secret()
    if not secret:
        raise HTTPException(500, "CRON_SECRET not configured")
    base_url = settings.effective_public_url
    destination = base_url.rstrip("/") + CRON_JOB_PATHS[job]
    # QStash v2: POST /v2/schedules/{destination} with destination URL-encoded
    encoded = quote(destination, safe="")
    base = (settings.qstash_url or "https://qstash.upstash.io").rstrip("/")
    schedule_id = f"amazon-ads-{job}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{base}/v2/schedules/{encoded}",
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
            return {"scheduleId": data.get("scheduleId", schedule_id), "destination": destination, "cron": cron}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create QStash schedule")
        raise HTTPException(500, str(e))


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
