"""
Cron / Scheduled Jobs — Endpoints for Upstash QStash or external cron.

These endpoints are called by QStash on a schedule. They verify CRON_SECRET
and trigger campaign sync, report generation, and search term sync for the
default credential.

Set CRON_SECRET in Railway Variables. QStash sends:
  Authorization: Bearer <QSTASH_CURRENT_SIGNING_KEY> (verify via Upstash-Signature)
  OR use a simple secret: X-Cron-Secret: <CRON_SECRET>
"""

import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
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
