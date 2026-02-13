"""
Audit Router — Campaign audit, performance reports, and waste identification.
All audit data (snapshots, issues, opportunities, reports) stored in PostgreSQL.
"""

import logging
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import (
    Credential, AuditSnapshot, AuditIssue, AuditOpportunity,
    Report, ActivityLog, Account,
)
from app.mcp_client import create_mcp_client
from app.services.token_service import get_mcp_client_with_fresh_token
from app.services.audit_service import AuditService
from app.utils import parse_uuid, safe_error_detail, utcnow

logger = logging.getLogger(__name__)

router = APIRouter()


class AuditRequest(BaseModel):
    credential_id: Optional[str] = None


class ReportRequest(BaseModel):
    credential_id: Optional[str] = None
    report_type: str = "campaign"  # campaign | product | inventory
    date_range: Optional[dict] = None


async def _get_cred(db: AsyncSession, cred_id: str = None) -> Credential:
    if cred_id:
        result = await db.execute(select(Credential).where(Credential.id == parse_uuid(cred_id, "credential_id")))
    else:
        result = await db.execute(select(Credential).where(Credential.is_default == True))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credential found.")
    return cred


async def _resolve_advertiser_account_id(db: AsyncSession, cred: Credential) -> Optional[str]:
    """Resolve the advertiserAccountId (amzn1 format) for report creation."""
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
        return active_account.raw_data.get("advertiserAccountId")
    return None


@router.post("/run")
async def run_audit(payload: AuditRequest, db: AsyncSession = Depends(get_db)):
    """
    Run a full campaign audit:
    1. Query all campaigns, ad groups, and targets
    2. Generate performance report
    3. Analyze and identify waste
    4. Store snapshot, issues, and opportunities in DB
    """
    cred = await _get_cred(db, payload.credential_id)
    if not cred.profile_id:
        raise HTTPException(
            status_code=400,
            detail="No account profile selected. Use the account dropdown to select an account, or run Discover Accounts first.",
        )
    client = await get_mcp_client_with_fresh_token(cred, db)
    adv_account_id = await _resolve_advertiser_account_id(db, cred)

    service = AuditService(client, advertiser_account_id=adv_account_id)

    try:
        audit_result = await service.run_full_audit()
        summary = audit_result.get("summary", {})
        analysis = audit_result.get("analysis", {})

        # Sync campaign metadata (state, type, budget) to Campaign table
        # so report data can cross-reference state later
        try:
            from app.services.reporting_service import sync_campaigns_to_db
            await sync_campaigns_to_db(db, cred.id, audit_result.get("campaigns", {}), profile_id=cred.profile_id)
        except Exception as e:
            logger.warning(f"Campaign sync during audit failed: {e}")

        date_range = audit_result.get("date_range", {})

        # Store a slim version of snapshot_data (exclude raw MCP payloads to avoid
        # multi-MB JSON that can cause serialization/commit issues)
        slim_snapshot = {
            "analysis": analysis,
            "summary": summary,
            "report_campaigns": audit_result.get("report_campaigns", []),
            "date_range": date_range,
        }

        # Create and persist snapshot
        snapshot = AuditSnapshot(
            credential_id=cred.id,
            snapshot_data=slim_snapshot,
            campaigns_count=summary.get("total_campaigns", 0),
            active_campaigns=summary.get("active_campaigns", 0),
            paused_campaigns=summary.get("paused_campaigns", 0),
            total_ad_groups=summary.get("total_ad_groups", 0),
            total_targets=summary.get("total_targets", 0),
            total_spend=summary.get("total_spend", 0),
            total_sales=summary.get("total_sales", 0),
            avg_acos=summary.get("avg_acos", 0),
            avg_roas=summary.get("avg_roas", 0),
            waste_identified=summary.get("waste_identified", 0),
            issues_count=summary.get("issues_count", 0),
            opportunities_count=summary.get("opportunities_count", 0),
            status="completed",
        )
        db.add(snapshot)
        await db.flush()

        # Persist individual issues to their own table
        issues_list = analysis.get("issues", [])
        for issue_data in issues_list:
            issue = AuditIssue(
                snapshot_id=snapshot.id,
                severity=issue_data.get("severity", "medium"),
                issue_type=issue_data.get("type", "unknown"),
                message=issue_data.get("message", ""),
                campaign_id=issue_data.get("campaign_id"),
                campaign_name=issue_data.get("campaign_name"),
                details=issue_data,
            )
            db.add(issue)

        # Persist individual opportunities to their own table
        opps_list = analysis.get("opportunities", [])
        for opp_data in opps_list:
            opportunity = AuditOpportunity(
                snapshot_id=snapshot.id,
                opportunity_type=opp_data.get("type", "unknown"),
                message=opp_data.get("message", ""),
                potential_impact=opp_data.get("potential_impact"),
                campaign_id=opp_data.get("campaign_id"),
                campaign_name=opp_data.get("campaign_name"),
                details=opp_data,
            )
            db.add(opportunity)

        # Log activity
        db.add(ActivityLog(
            credential_id=cred.id,
            action="audit_completed",
            category="audit",
            description=f"Audit completed: {snapshot.campaigns_count} campaigns, "
                        f"{len(issues_list)} issues, {len(opps_list)} opportunities",
            entity_type="audit_snapshot",
            entity_id=str(snapshot.id),
            details={
                "snapshot_id": str(snapshot.id),
                "campaigns_count": snapshot.campaigns_count,
                "issues_count": len(issues_list),
                "opportunities_count": len(opps_list),
            },
        ))

        # Also persist report campaigns to campaign_performance_daily for trends
        report_campaigns = audit_result.get("report_campaigns", [])
        if report_campaigns:
            from app.services.reporting_service import persist_campaign_daily
            await persist_campaign_daily(db, cred.id, report_campaigns, profile_id=cred.profile_id)

        # Flush to ensure all data is written before building response
        await db.flush()

        # Return a slim response (exclude raw MCP payloads)
        return {
            "summary": summary,
            "analysis": analysis,
            "report_campaigns": report_campaigns,
            "snapshot_id": str(snapshot.id),
            "date_range": date_range,
        }
    except Exception as e:
        db.add(ActivityLog(
            credential_id=cred.id,
            action="audit_failed",
            category="audit",
            description=str(e),
            status="error",
        ))
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to communicate with Amazon Ads API."))


@router.post("/report")
async def create_report(payload: ReportRequest, db: AsyncSession = Depends(get_db)):
    """Create, retrieve, and store a specific report type in DB."""
    cred = await _get_cred(db, payload.credential_id)
    if not cred.profile_id:
        raise HTTPException(
            status_code=400,
            detail="No account profile selected. Use the account dropdown to select an account, or run Discover Accounts first.",
        )
    client = await get_mcp_client_with_fresh_token(cred, db)
    adv_account_id = await _resolve_advertiser_account_id(db, cred)
    service = AuditService(client, advertiser_account_id=adv_account_id)

    # Create report record in DB first
    report = Report(
        credential_id=cred.id,
        report_type=payload.report_type,
        ad_product="SPONSORED_PRODUCTS",
        date_range_start=payload.date_range.get("startDate") if payload.date_range else None,
        date_range_end=payload.date_range.get("endDate") if payload.date_range else None,
        status="running",
    )
    db.add(report)
    await db.flush()

    try:
        report_data = await service.create_and_retrieve_report(
            report_type=payload.report_type,
            date_range=payload.date_range,
        )

        # Update report with results
        report.status = "completed"
        report.report_data = report_data
        report.raw_response = report_data
        report.completed_at = utcnow()

        db.add(ActivityLog(
            credential_id=cred.id,
            action="report_created",
            category="audit",
            description=f"Created {payload.report_type} report",
            entity_type="report",
            entity_id=str(report.id),
        ))

        return {
            "report_id": str(report.id),
            **report_data,
        }
    except Exception as e:
        report.status = "failed"
        db.add(ActivityLog(
            credential_id=cred.id,
            action="report_failed",
            category="audit",
            description=str(e),
            status="error",
            entity_type="report",
            entity_id=str(report.id),
        ))
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to communicate with Amazon Ads API."))


@router.get("/snapshots")
async def list_snapshots(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List audit snapshots from DB, with issue/opportunity counts."""
    query = select(AuditSnapshot).order_by(AuditSnapshot.created_at.desc()).limit(20)
    if credential_id:
        query = query.where(AuditSnapshot.credential_id == parse_uuid(credential_id, "credential_id"))

    result = await db.execute(query)
    snapshots = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "credential_id": str(s.credential_id),
            "campaigns_count": s.campaigns_count,
            "active_campaigns": s.active_campaigns,
            "paused_campaigns": s.paused_campaigns,
            "total_ad_groups": s.total_ad_groups,
            "total_targets": s.total_targets,
            "total_spend": s.total_spend,
            "total_sales": s.total_sales,
            "avg_acos": s.avg_acos,
            "avg_roas": s.avg_roas,
            "waste_identified": s.waste_identified,
            "issues_count": s.issues_count,
            "opportunities_count": s.opportunities_count,
            "status": s.status,
            "created_at": s.created_at.isoformat(),
            "date_range": (s.snapshot_data or {}).get("date_range"),
        }
        for s in snapshots
    ]


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific audit snapshot with its issues and opportunities from DB."""
    result = await db.execute(
        select(AuditSnapshot).where(AuditSnapshot.id == parse_uuid(snapshot_id, "snapshot_id"))
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    # Load issues from DB
    issues_result = await db.execute(
        select(AuditIssue).where(AuditIssue.snapshot_id == snapshot.id)
        .order_by(AuditIssue.severity)
    )
    issues = issues_result.scalars().all()

    # Load opportunities from DB
    opps_result = await db.execute(
        select(AuditOpportunity).where(AuditOpportunity.snapshot_id == snapshot.id)
    )
    opportunities = opps_result.scalars().all()

    return {
        "id": str(snapshot.id),
        "credential_id": str(snapshot.credential_id),
        "snapshot_data": snapshot.snapshot_data,
        "campaigns_count": snapshot.campaigns_count,
        "active_campaigns": snapshot.active_campaigns,
        "paused_campaigns": snapshot.paused_campaigns,
        "total_ad_groups": snapshot.total_ad_groups,
        "total_targets": snapshot.total_targets,
        "total_spend": snapshot.total_spend,
        "total_sales": snapshot.total_sales,
        "avg_acos": snapshot.avg_acos,
        "avg_roas": snapshot.avg_roas,
        "waste_identified": snapshot.waste_identified,
        "issues_count": snapshot.issues_count,
        "opportunities_count": snapshot.opportunities_count,
        "status": snapshot.status,
        "created_at": snapshot.created_at.isoformat(),
        "date_range": (snapshot.snapshot_data or {}).get("date_range"),
        "issues": [
            {
                "id": str(i.id),
                "severity": i.severity,
                "issue_type": i.issue_type,
                "message": i.message,
                "campaign_id": i.campaign_id,
                "campaign_name": i.campaign_name,
                "details": i.details,
            }
            for i in issues
        ],
        "opportunities": [
            {
                "id": str(o.id),
                "opportunity_type": o.opportunity_type,
                "message": o.message,
                "potential_impact": o.potential_impact,
                "campaign_id": o.campaign_id,
                "campaign_name": o.campaign_name,
                "details": o.details,
            }
            for o in opportunities
        ],
    }


@router.delete("/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str, db: AsyncSession = Depends(get_db)):
    """Delete an audit snapshot and its associated issues/opportunities."""
    result = await db.execute(
        select(AuditSnapshot).where(AuditSnapshot.id == parse_uuid(snapshot_id, "snapshot_id"))
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    await db.delete(snapshot)  # cascade deletes issues + opportunities
    # Note: db.commit() removed — get_db dependency handles commit/rollback automatically
    logger.info(f"Deleted audit snapshot {snapshot_id}")
    return {"deleted": True, "id": snapshot_id}


@router.get("/reports")
async def list_reports(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List all stored reports from DB."""
    query = select(Report).order_by(Report.created_at.desc()).limit(20)
    if credential_id:
        query = query.where(Report.credential_id == parse_uuid(credential_id, "credential_id"))

    result = await db.execute(query)
    reports = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "credential_id": str(r.credential_id),
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


@router.get("/reports/{report_id}")
async def get_report(report_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific report with full data from DB."""
    result = await db.execute(
        select(Report).where(Report.id == parse_uuid(report_id, "report_id"))
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "id": str(report.id),
        "credential_id": str(report.credential_id),
        "report_type": report.report_type,
        "ad_product": report.ad_product,
        "date_range_start": report.date_range_start,
        "date_range_end": report.date_range_end,
        "status": report.status,
        "report_data": report.report_data,
        "created_at": report.created_at.isoformat(),
        "completed_at": report.completed_at.isoformat() if report.completed_at else None,
    }


@router.get("/issues")
async def list_issues(
    credential_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50),
    db: AsyncSession = Depends(get_db),
):
    """Query audit issues directly from DB with filtering."""
    query = (
        select(AuditIssue)
        .join(AuditSnapshot, AuditIssue.snapshot_id == AuditSnapshot.id)
        .order_by(AuditIssue.created_at.desc())
        .limit(limit)
    )
    if credential_id:
        query = query.where(AuditSnapshot.credential_id == parse_uuid(credential_id, "credential_id"))
    if severity:
        query = query.where(AuditIssue.severity == severity)

    result = await db.execute(query)
    issues = result.scalars().all()
    return [
        {
            "id": str(i.id),
            "snapshot_id": str(i.snapshot_id),
            "severity": i.severity,
            "issue_type": i.issue_type,
            "message": i.message,
            "campaign_id": i.campaign_id,
            "campaign_name": i.campaign_name,
            "created_at": i.created_at.isoformat(),
        }
        for i in issues
    ]
