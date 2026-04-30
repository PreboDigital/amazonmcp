"""Unified activity / change ledger from logs, approvals, and bid changes."""

from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.models import (
    ActivityLog,
    PendingChange,
    BidChange,
    OptimizationRun,
)
from app.routers.reporting import _get_cred

router = APIRouter(prefix="/activity", tags=["Activity"])


@router.get("/ledger")
async def change_ledger(
    credential_id: Optional[str] = Query(None),
    limit: int = Query(80, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Merged timeline: activity log, approval queue events, applied bid changes."""
    cred = await _get_cred(db, credential_id)
    events: list[dict] = []

    logs = (
        await db.execute(
            select(ActivityLog)
            .where(ActivityLog.credential_id == cred.id)
            .order_by(ActivityLog.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    for log in logs:
        events.append(
            {
                "at": log.created_at.isoformat() if log.created_at else None,
                "kind": "activity",
                "action": log.action,
                "category": log.category,
                "description": log.description,
                "status": log.status,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "details": log.details,
            }
        )

    pcs = (
        await db.execute(
            select(PendingChange)
            .where(PendingChange.credential_id == cred.id)
            .order_by(PendingChange.updated_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    for pc in pcs:
        events.append(
            {
                "at": (pc.applied_at or pc.reviewed_at or pc.created_at).isoformat()
                if (pc.applied_at or pc.reviewed_at or pc.created_at)
                else None,
                "kind": "approval",
                "action": pc.change_type,
                "category": "approvals",
                "description": f"{pc.entity_type} {pc.entity_name or pc.entity_id or ''} — {pc.status}",
                "status": pc.status,
                "entity_type": pc.entity_type,
                "entity_id": pc.entity_id,
                "details": {
                    "source": pc.source,
                    "current_value": pc.current_value,
                    "proposed_value": pc.proposed_value,
                    "review_note": pc.review_note,
                    "batch_label": pc.batch_label,
                },
            }
        )

    bid_q = (
        select(BidChange)
        .join(OptimizationRun, BidChange.optimization_run_id == OptimizationRun.id)
        .where(
            OptimizationRun.credential_id == cred.id,
            BidChange.applied == True,
        )
        .order_by(BidChange.created_at.desc())
        .limit(limit)
    )
    bids = (await db.execute(bid_q)).scalars().all()
    for b in bids:
        events.append(
            {
                "at": b.created_at.isoformat() if b.created_at else None,
                "kind": "bid_change",
                "action": "bid_applied",
                "category": "optimizer",
                "description": f"Target {b.amazon_target_id}: bid {b.previous_bid} → {b.new_bid} ({b.direction})",
                "status": "applied",
                "entity_type": "target",
                "entity_id": b.amazon_target_id,
                "details": {
                    "campaign_id": b.amazon_campaign_id,
                    "reason": b.reason,
                    "spend": b.spend,
                    "current_acos": b.current_acos,
                },
            }
        )

    events.sort(
        key=lambda e: e.get("at") or "",
        reverse=True,
    )
    return {"credential_id": str(cred.id), "events": events[:limit]}
