"""CSV / PDF exports for audits, performance, search terms, neg-keyword packets."""

from __future__ import annotations

import csv
import io
import logging
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    AuditSnapshot,
    AuditIssue,
    AuditOpportunity,
    CampaignPerformanceDaily,
    SearchTermPerformance,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    if not rows:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["(no rows)"])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    buf = io.StringIO()
    fieldnames = list(rows[0].keys())
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit-latest.csv")
async def export_audit_latest_csv(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    cred = await _get_cred(db, credential_id)
    snap = (
        await db.execute(
            select(AuditSnapshot)
            .where(AuditSnapshot.credential_id == cred.id)
            .order_by(AuditSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not snap:
        raise HTTPException(404, "No audit snapshot for this credential.")

    issues = (
        await db.execute(select(AuditIssue).where(AuditIssue.snapshot_id == snap.id))
    ).scalars().all()
    opps = (
        await db.execute(select(AuditOpportunity).where(AuditOpportunity.snapshot_id == snap.id))
    ).scalars().all()

    rows: list[dict[str, Any]] = []
    for i in issues:
        rows.append(
            {
                "type": "issue",
                "severity": i.severity,
                "issue_type": i.issue_type,
                "message": i.message,
                "campaign_id": i.campaign_id,
                "campaign_name": i.campaign_name,
            }
        )
    for o in opps:
        rows.append(
            {
                "type": "opportunity",
                "severity": "",
                "issue_type": o.opportunity_type,
                "message": o.message,
                "campaign_id": o.campaign_id,
                "campaign_name": o.campaign_name,
            }
        )
    return _csv_response(rows, f"audit-{snap.id}.csv")


@router.get("/campaign-performance.csv")
async def export_campaign_performance_csv(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    start_date: str = Query(...),
    end_date: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    cred = await _get_cred(db, credential_id)
    pid = profile_id if profile_id is not None else cred.profile_id
    q = (
        select(CampaignPerformanceDaily)
        .where(
            CampaignPerformanceDaily.credential_id == cred.id,
            CampaignPerformanceDaily.date >= start_date,
            CampaignPerformanceDaily.date <= end_date,
        )
        .order_by(CampaignPerformanceDaily.date, CampaignPerformanceDaily.campaign_name)
    )
    if pid is not None:
        q = q.where(CampaignPerformanceDaily.profile_id == pid)
    else:
        q = q.where(CampaignPerformanceDaily.profile_id.is_(None))
    rows_db = (await db.execute(q)).scalars().all()
    rows = [
        {
            "date": r.date,
            "amazon_campaign_id": r.amazon_campaign_id,
            "campaign_name": r.campaign_name or "",
            "spend": r.spend,
            "sales": r.sales,
            "clicks": r.clicks,
            "impressions": r.impressions,
            "orders": r.orders,
            "acos": r.acos,
            "roas": r.roas,
        }
        for r in rows_db
    ]
    return _csv_response(rows, "campaign-performance.csv")


@router.get("/search-terms.csv")
async def export_search_terms_csv(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    start_date: str = Query(...),
    end_date: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    cred = await _get_cred(db, credential_id)
    pid = profile_id if profile_id is not None else cred.profile_id
    q = (
        select(SearchTermPerformance)
        .where(
            SearchTermPerformance.credential_id == cred.id,
            SearchTermPerformance.date >= start_date,
            SearchTermPerformance.date <= end_date,
        )
        .order_by(SearchTermPerformance.cost.desc())
        .limit(5000)
    )
    if pid is not None:
        q = q.where(SearchTermPerformance.profile_id == pid)
    else:
        q = q.where(SearchTermPerformance.profile_id.is_(None))
    rows_db = (await db.execute(q)).scalars().all()
    rows = [
        {
            "date": r.date,
            "search_term": r.search_term,
            "campaign_name": r.campaign_name or "",
            "ad_group_name": r.ad_group_name or "",
            "clicks": r.clicks,
            "cost": r.cost,
            "purchases": r.purchases,
            "sales": r.sales,
            "acos": r.acos,
        }
        for r in rows_db
    ]
    return _csv_response(rows, "search-terms.csv")


@router.get("/neg-keyword-packet.csv")
async def export_neg_keyword_packet_csv(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    lookback_days: int = Query(30, ge=1, le=90),
    min_clicks: int = Query(5, ge=1),
    max_acos: float = Query(60.0, ge=0),
    min_spend: float = Query(5.0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Waste search terms for bulk negation planning: high spend, clicks, poor ACOS.
    Suggested neg = exact search_term text (user reviews before applying).
    """
    cred = await _get_cred(db, credential_id)
    pid = profile_id if profile_id is not None else cred.profile_id
    end = utcnow().date()
    start = end - timedelta(days=lookback_days)
    start_s = start.isoformat()
    end_s = end.isoformat()

    q = (
        select(SearchTermPerformance)
        .where(
            SearchTermPerformance.credential_id == cred.id,
            SearchTermPerformance.date >= start_s,
            SearchTermPerformance.date <= end_s,
            SearchTermPerformance.clicks >= min_clicks,
            SearchTermPerformance.cost >= min_spend,
        )
    )
    if pid is not None:
        q = q.where(SearchTermPerformance.profile_id == pid)
    else:
        q = q.where(SearchTermPerformance.profile_id.is_(None))

    rows_db = (await db.execute(q)).scalars().all()
    out_rows: list[dict[str, Any]] = []
    for r in rows_db:
        acos = r.acos
        if acos is not None and acos <= max_acos:
            continue
        if r.purchases and r.purchases > 0:
            continue
        out_rows.append(
            {
                "suggested_neg_exact": r.search_term,
                "search_term": r.search_term,
                "campaign_name": r.campaign_name or "",
                "ad_group_name": r.ad_group_name or "",
                "clicks": r.clicks,
                "cost": r.cost,
                "purchases": r.purchases,
                "acos": acos if acos is not None else "",
                "note": "Review match type before negating; exact in SP bulk sheet column",
            }
        )
    out_rows.sort(key=lambda x: float(x.get("cost") or 0), reverse=True)
    out_rows = out_rows[:2000]
    return _csv_response(out_rows, "neg-keyword-packet.csv")


@router.get("/summary.pdf")
async def export_summary_pdf(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """One-page PDF summary of latest audit snapshot metrics (text-only)."""
    try:
        from fpdf import FPDF
    except ImportError:
        raise HTTPException(500, "PDF export requires fpdf2. pip install fpdf2")

    cred = await _get_cred(db, credential_id)
    snap = (
        await db.execute(
            select(AuditSnapshot)
            .where(AuditSnapshot.credential_id == cred.id)
            .order_by(AuditSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not snap:
        raise HTTPException(404, "No audit snapshot for this credential.")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", style="B", size=14)
    pdf.cell(0, 10, text=f"Amazon Ads — {cred.name}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    lines = [
        f"Snapshot: {snap.created_at}",
        f"Campaigns: {snap.campaigns_count} (active {snap.active_campaigns}, paused {snap.paused_campaigns})",
        f"Ad groups: {snap.total_ad_groups}  Targets: {snap.total_targets}",
        f"Spend: {snap.total_spend:.2f}  Sales: {snap.total_sales:.2f}",
        f"Avg ACOS: {snap.avg_acos:.2f}%  Avg ROAS: {snap.avg_roas:.2f}",
        f"Waste identified: {snap.waste_identified:.2f}",
        f"Issues: {snap.issues_count}  Opportunities: {snap.opportunities_count}",
    ]
    for line in lines:
        pdf.multi_cell(0, 6, text=line)

    pdf_bytes = bytes(pdf.output())
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="audit-summary-{snap.id}.pdf"'},
    )
