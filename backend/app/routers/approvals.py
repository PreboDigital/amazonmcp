"""
Approvals Router — Change approval queue workflow.
All changes to Amazon Ads go through this approval pipeline before being pushed.
Supports review, approve, reject, batch operations, and execution via MCP.
"""

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import Credential, PendingChange, ActivityLog
from app.mcp_client import create_mcp_client
from app.services.token_service import get_mcp_client_with_fresh_token
from app.services.harvest_service import HarvestService
from app.services.campaign_creation_service import CampaignCreationService
from app.utils import parse_uuid, utcnow

router = APIRouter()


# ── Request Models ────────────────────────────────────────────────────

class CreateChangeRequest(BaseModel):
    credential_id: Optional[str] = None
    change_type: str  # bid_update, budget_update, campaign_state, etc.
    entity_type: str  # campaign, ad_group, target, keyword
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    current_value: Optional[str] = None
    proposed_value: Optional[str] = None
    change_detail: Optional[dict] = None
    mcp_payload: dict
    source: str = "manual"
    ai_reasoning: Optional[str] = None
    batch_id: Optional[str] = None
    batch_label: Optional[str] = None


class ReviewRequest(BaseModel):
    action: str  # approve, reject
    review_note: Optional[str] = None


class BatchReviewRequest(BaseModel):
    change_ids: list[str]
    action: str  # approve, reject
    review_note: Optional[str] = None


class BatchApplyRequest(BaseModel):
    change_ids: Optional[list[str]] = None
    batch_id: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────

async def _get_cred(db: AsyncSession, cred_id: Optional[str] = None) -> Credential:
    if cred_id:
        result = await db.execute(select(Credential).where(Credential.id == parse_uuid(cred_id, "credential_id")))
    else:
        result = await db.execute(select(Credential).where(Credential.is_default == True))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credential found.")
    return cred


# ── CRUD Endpoints ────────────────────────────────────────────────────

@router.get("")
async def list_pending_changes(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    change_type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    batch_id: Optional[str] = Query(None),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    """List pending changes with filtering. Default shows all statuses."""
    query = select(PendingChange).order_by(PendingChange.created_at.desc()).limit(limit)

    if credential_id:
        query = query.where(PendingChange.credential_id == parse_uuid(credential_id, "credential_id"))
    if profile_id:
        # Include changes with profile_id=null (legacy) or matching profile
        query = query.where(or_(PendingChange.profile_id.is_(None), PendingChange.profile_id == profile_id))
    if status:
        query = query.where(PendingChange.status == status)
    if change_type:
        query = query.where(PendingChange.change_type == change_type)
    if source:
        query = query.where(PendingChange.source == source)
    if batch_id:
        query = query.where(PendingChange.batch_id == batch_id)

    result = await db.execute(query)
    changes = result.scalars().all()

    return [_serialize_change(c) for c in changes]


@router.get("/summary")
async def changes_summary(
    credential_id: Optional[str] = Query(None),
    profile_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get summary counts of pending changes by status and type."""
    base_filter = []
    if credential_id:
        base_filter.append(PendingChange.credential_id == parse_uuid(credential_id, "credential_id"))
    if profile_id:
        base_filter.append(or_(PendingChange.profile_id.is_(None), PendingChange.profile_id == profile_id))

    # Count by status
    status_query = (
        select(PendingChange.status, func.count(PendingChange.id))
        .group_by(PendingChange.status)
    )
    if base_filter:
        status_query = status_query.where(and_(*base_filter))
    status_result = await db.execute(status_query)
    status_counts = dict(status_result.all())

    # Count by type (pending only)
    type_query = (
        select(PendingChange.change_type, func.count(PendingChange.id))
        .where(PendingChange.status == "pending")
        .group_by(PendingChange.change_type)
    )
    if base_filter:
        type_query = type_query.where(and_(*base_filter))
    type_result = await db.execute(type_query)
    type_counts = dict(type_result.all())

    # Count by source (pending only)
    source_query = (
        select(PendingChange.source, func.count(PendingChange.id))
        .where(PendingChange.status == "pending")
        .group_by(PendingChange.source)
    )
    if base_filter:
        source_query = source_query.where(and_(*base_filter))
    source_result = await db.execute(source_query)
    source_counts = dict(source_result.all())

    return {
        "by_status": status_counts,
        "by_type": type_counts,
        "by_source": source_counts,
        "total_pending": status_counts.get("pending", 0),
        "total_approved": status_counts.get("approved", 0),
        "total_rejected": status_counts.get("rejected", 0),
        "total_applied": status_counts.get("applied", 0),
    }


@router.post("")
async def create_pending_change(
    payload: CreateChangeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Manually create a pending change for the approval queue."""
    cred = await _get_cred(db, payload.credential_id)

    change = PendingChange(
        credential_id=cred.id,
        profile_id=cred.profile_id,
        change_type=payload.change_type,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        entity_name=payload.entity_name,
        campaign_id=payload.campaign_id,
        campaign_name=payload.campaign_name,
        current_value=payload.current_value,
        proposed_value=payload.proposed_value,
        change_detail=payload.change_detail,
        mcp_payload=payload.mcp_payload,
        source=payload.source,
        ai_reasoning=payload.ai_reasoning,
        batch_id=payload.batch_id,
        batch_label=payload.batch_label,
    )
    db.add(change)
    await db.flush()

    db.add(ActivityLog(
        credential_id=cred.id,
        action="change_queued",
        category="approvals",
        description=f"Queued {payload.change_type} for {payload.entity_type} {payload.entity_name or payload.entity_id}",
        entity_type="pending_change",
        entity_id=str(change.id),
    ))

    return {"id": str(change.id), "status": "pending"}


@router.get("/{change_id}")
async def get_change_detail(change_id: str, db: AsyncSession = Depends(get_db)):
    """Get detailed information about a specific pending change."""
    result = await db.execute(
        select(PendingChange).where(PendingChange.id == parse_uuid(change_id, "change_id"))
    )
    change = result.scalar_one_or_none()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")
    return _serialize_change(change)


@router.post("/{change_id}/review")
async def review_change(
    change_id: str,
    payload: ReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a single pending change."""
    result = await db.execute(
        select(PendingChange).where(PendingChange.id == parse_uuid(change_id, "change_id"))
    )
    change = result.scalar_one_or_none()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")

    if change.status != "pending":
        raise HTTPException(status_code=400, detail=f"Change is already {change.status}")

    if payload.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")

    change.status = "approved" if payload.action == "approve" else "rejected"
    change.reviewed_at = utcnow()
    change.review_note = payload.review_note

    db.add(ActivityLog(
        credential_id=change.credential_id,
        action=f"change_{payload.action}d",
        category="approvals",
        description=f"{payload.action.title()}d {change.change_type} for {change.entity_name or change.entity_id}",
        entity_type="pending_change",
        entity_id=str(change.id),
        details={"review_note": payload.review_note},
    ))

    return {"id": str(change.id), "status": change.status}


@router.post("/batch-review")
async def batch_review(payload: BatchReviewRequest, db: AsyncSession = Depends(get_db)):
    """Approve or reject multiple changes at once."""
    if payload.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")

    change_ids = [parse_uuid(cid, "change_id") for cid in payload.change_ids]
    result = await db.execute(
        select(PendingChange).where(
            PendingChange.id.in_(change_ids),
            PendingChange.status == "pending",
        )
    )
    changes = result.scalars().all()

    if not changes:
        raise HTTPException(status_code=404, detail="No pending changes found")

    new_status = "approved" if payload.action == "approve" else "rejected"
    now = utcnow()

    for change in changes:
        change.status = new_status
        change.reviewed_at = now
        change.review_note = payload.review_note

    # Single activity log for batch
    db.add(ActivityLog(
        credential_id=changes[0].credential_id,
        action=f"batch_change_{payload.action}d",
        category="approvals",
        description=f"Batch {payload.action}d {len(changes)} changes",
        details={"change_ids": [str(c.id) for c in changes]},
    ))

    return {"reviewed": len(changes), "status": new_status}


@router.post("/apply")
async def apply_approved_changes(
    payload: BatchApplyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Execute approved changes via MCP, pushing them to Amazon Ads Manager.
    Only applies changes that have been approved.
    """
    # Build query for approved changes
    query = select(PendingChange).where(PendingChange.status == "approved")

    if payload.change_ids:
        change_ids = [parse_uuid(cid, "change_id") for cid in payload.change_ids]
        query = query.where(PendingChange.id.in_(change_ids))
    elif payload.batch_id:
        query = query.where(PendingChange.batch_id == payload.batch_id)
    else:
        raise HTTPException(status_code=400, detail="Provide change_ids or batch_id")

    result = await db.execute(query)
    changes = result.scalars().all()

    if not changes:
        raise HTTPException(status_code=404, detail="No approved changes found to apply")

    # Get credential for MCP client
    cred_id = changes[0].credential_id
    cred_result = await db.execute(select(Credential).where(Credential.id == cred_id))
    cred = cred_result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Use profile_id from first change if set (ensures apply targets correct account)
    profile_id = changes[0].profile_id or cred.profile_id
    client = await get_mcp_client_with_fresh_token(cred, db, profile_id_override=profile_id)

    # Apply each change via MCP
    applied = 0
    failed = 0
    results = []

    for change in changes:
        try:
            mcp_payload = change.mcp_payload
            tool_name = mcp_payload.get("tool", "")
            arguments = mcp_payload.get("arguments", {})

            if not tool_name or tool_name == "unknown":
                change.status = "failed"
                change.error_message = "Unknown MCP tool in payload"
                failed += 1
                results.append({"id": str(change.id), "status": "failed", "error": "Unknown tool"})
                continue

            # Handle harvest "existing" mode: requires multi-step service execution
            if tool_name == "_harvest_execute":
                harvest_service = HarvestService(client)
                mcp_result = await harvest_service.execute_harvest(
                    source_campaign_id=arguments.get("source_campaign_id", ""),
                    sales_threshold=arguments.get("sales_threshold", 1.0),
                    acos_threshold=arguments.get("acos_threshold"),
                    target_mode=arguments.get("target_mode", "existing"),
                    target_campaign_id=arguments.get("target_campaign_id"),
                    match_type=arguments.get("match_type"),
                    negate_in_source=arguments.get("negate_in_source", True),
                )
            elif tool_name == "_ai_campaign_create":
                # Full campaign creation: campaign → ad group → ad → targets
                campaign_service = CampaignCreationService(client)
                plan = arguments.get("plan", {})
                mcp_result = await campaign_service.execute_plan(plan)
            else:
                mcp_result = await client.call_tool(tool_name, arguments)

            change.status = "applied"
            change.applied_at = utcnow()
            change.apply_result = mcp_result
            applied += 1
            results.append({"id": str(change.id), "status": "applied"})

        except Exception as e:
            change.status = "failed"
            change.error_message = str(e)
            failed += 1
            results.append({"id": str(change.id), "status": "failed", "error": str(e)})

    db.add(ActivityLog(
        credential_id=cred.id,
        action="changes_applied",
        category="approvals",
        description=f"Applied {applied} changes to Amazon Ads ({failed} failed)",
        details={"applied": applied, "failed": failed, "results": results},
    ))

    return {
        "applied": applied,
        "failed": failed,
        "total": len(changes),
        "results": results,
    }


@router.delete("/{change_id}")
async def delete_change(change_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a pending change (only if still pending)."""
    result = await db.execute(
        select(PendingChange).where(PendingChange.id == parse_uuid(change_id, "change_id"))
    )
    change = result.scalar_one_or_none()
    if not change:
        raise HTTPException(status_code=404, detail="Change not found")
    if change.status not in ("pending", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot delete a {change.status} change")

    await db.delete(change)
    return {"status": "deleted"}


# ── Serializer ────────────────────────────────────────────────────────

def _serialize_change(c: PendingChange) -> dict:
    return {
        "id": str(c.id),
        "credential_id": str(c.credential_id),
        "profile_id": c.profile_id,
        "change_type": c.change_type,
        "entity_type": c.entity_type,
        "entity_id": c.entity_id,
        "entity_name": c.entity_name,
        "campaign_id": c.campaign_id,
        "campaign_name": c.campaign_name,
        "current_value": c.current_value,
        "proposed_value": c.proposed_value,
        "change_detail": c.change_detail,
        "source": c.source,
        "ai_reasoning": c.ai_reasoning,
        "confidence": c.confidence,
        "estimated_impact": c.estimated_impact,
        "status": c.status,
        "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "review_note": c.review_note,
        "applied_at": c.applied_at.isoformat() if c.applied_at else None,
        "apply_result": c.apply_result,
        "error_message": c.error_message,
        "batch_id": c.batch_id,
        "batch_label": c.batch_label,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }
