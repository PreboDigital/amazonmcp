"""
Approvals Router — Change approval queue workflow.
All changes to Amazon Ads go through this approval pipeline before being pushed.
Supports review, approve, reject, batch operations, and execution via MCP.
"""

import asyncio
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
from app.services.ai_action_validator import validate_ai_action
from app.services.mutation_aftercare import (
    build_aftercare,
    verify_mutation,
    verify_harvest_execution,
    verify_harvest_create_campaign_result,
)
from app.utils import (
    parse_uuid,
    utcnow,
    normalize_mcp_call,
    extract_mcp_error,
    build_mcp_fallback_call,
)

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


_TRANSIENT_ERROR_TOKENS = (
    "throttl", "rate limit", "rate-limit", "rate_limit",
    "timeout", "timed out", "temporar", "try again",
    "quota", "503", "504",
)


def _looks_transient(message: str | None) -> bool:
    if not message:
        return False
    low = message.lower()
    return any(tok in low for tok in _TRANSIENT_ERROR_TOKENS)


async def _retry_with_backoff(client, tool_name: str, arguments: dict, *, attempts: int = 2):
    """One-shot transient retry with linear backoff. Returns ``(result, error)``.

    On every attempt the parsed-error / raised exception is treated
    uniformly: transient-looking errors trigger a retry up to
    ``attempts``; anything else returns immediately so the caller can
    decide whether to fall back to a generic tool or surface the error.
    """
    last_error: str | None = None
    for attempt in range(max(1, attempts)):
        try:
            result = await client.call_tool(tool_name, arguments)
        except Exception as exc:
            last_error = str(exc)
            if not _looks_transient(last_error) or attempt + 1 >= attempts:
                return None, last_error
            await asyncio.sleep(0.5 * (attempt + 1))
            continue
        parsed_error = extract_mcp_error(result)
        if not parsed_error:
            return result, None
        last_error = parsed_error
        if not _looks_transient(parsed_error) or attempt + 1 >= attempts:
            return None, last_error
        await asyncio.sleep(0.5 * (attempt + 1))
    return None, last_error


async def _call_tool_with_resilience(client, tool_name: str, arguments: dict):
    """
    Execute MCP call with transient-error retry, then a final fallback to
    the generic ``update_*`` tool for known specialised endpoints.

    Layered behaviour:

    1. Call ``tool_name`` with one transient retry (throttle / 5xx /
       timeout) — covers Amazon's aggressive report-poll throttling and
       short-lived MCP session blips.
    2. If the call still returns a non-transient error, attempt the
       fallback mapping in :func:`build_mcp_fallback_call`
       (``update_target_bid → update_target`` etc).
    3. The fallback also retries once on transient errors so we don't
       give up on a generic tool over a single 503.
    """
    result, first_error = await _retry_with_backoff(client, tool_name, arguments)
    if result is not None:
        return result

    fallback = build_mcp_fallback_call(tool_name, arguments)
    if not fallback:
        raise RuntimeError(first_error or "MCP call failed")

    fb_tool, fb_args = fallback
    fb_result, fb_error = await _retry_with_backoff(client, fb_tool, fb_args)
    if fb_result is None:
        raise RuntimeError(
            f"{first_error or 'MCP call failed'} | fallback {fb_tool} failed: "
            f"{fb_error or 'unknown error'}"
        )
    return fb_result


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

    applied = 0
    failed = 0
    results = []
    cred_cache: dict = {}
    group_stats: dict[tuple[str, Optional[str]], dict] = {}
    grouped: dict[tuple[str, Optional[str]], list[PendingChange]] = {}
    for change in changes:
        key = (str(change.credential_id), change.profile_id)
        grouped.setdefault(key, []).append(change)
        group_stats.setdefault(key, {"applied": 0, "failed": 0, "results": []})

    for (cred_id_str, profile_id), group_changes in grouped.items():
        stats = group_stats[(cred_id_str, profile_id)]
        cred_id = parse_uuid(cred_id_str, "credential_id")
        cred = cred_cache.get(cred_id_str)
        if cred is None:
            cred_result = await db.execute(select(Credential).where(Credential.id == cred_id))
            cred = cred_result.scalar_one_or_none()
            cred_cache[cred_id_str] = cred

        if not cred:
            for change in group_changes:
                change.status = "failed"
                change.error_message = "Credential not found"
                failed += 1
                stats["failed"] += 1
                entry = {"id": str(change.id), "status": "failed", "error": "Credential not found"}
                results.append(entry)
                stats["results"].append(entry)
            continue

        client = await get_mcp_client_with_fresh_token(
            cred, db, profile_id_override=profile_id or cred.profile_id
        )

        for change in group_changes:
            try:
                mcp_payload = change.mcp_payload or {}
                raw_tool_name = mcp_payload.get("tool", "")
                raw_arguments = mcp_payload.get("arguments", {})

                if not raw_tool_name or raw_tool_name == "unknown":
                    change.status = "failed"
                    change.error_message = "Unknown MCP tool in payload"
                    failed += 1
                    stats["failed"] += 1
                    entry = {"id": str(change.id), "status": "failed", "error": "Unknown tool"}
                    results.append(entry)
                    stats["results"].append(entry)
                    continue

                # Re-validate every change immediately *before* shipping to
                # Amazon. Queue-time validation can go stale: targets get
                # deleted, ad groups archived, the user re-syncs and IDs
                # change, etc. Without this re-check the apply path used
                # to forward a stale ``targetId`` straight to MCP and
                # surface Amazon's generic 400 instead of a clear
                # validator error. ``allow_queue_only_tools=True`` so
                # ``_harvest_execute`` / ``_ai_campaign_create`` /
                # ``_request_sync`` aren't rejected here.
                preflight_action = {
                    "tool": raw_tool_name,
                    "arguments": raw_arguments or {},
                    "label": change.entity_name or change.proposed_value,
                }
                preflight = await validate_ai_action(
                    preflight_action,
                    db,
                    cred,
                    profile_id=profile_id or cred.profile_id,
                    allow_queue_only_tools=True,
                )
                if not preflight.ok:
                    change.status = "failed"
                    change.error_message = (
                        f"Pre-flight validation failed: {preflight.error}"
                    )
                    failed += 1
                    stats["failed"] += 1
                    entry = {
                        "id": str(change.id),
                        "status": "failed",
                        "error": preflight.error or "validation failed",
                        "stage": "preflight",
                    }
                    results.append(entry)
                    stats["results"].append(entry)
                    continue

                # Use the validator's normalised tool / arguments — this is
                # already through ``normalize_mcp_call`` for native tools and
                # leaves synthetic ``_*`` tools intact.
                tool_name = preflight.tool or raw_tool_name
                arguments = preflight.arguments or raw_arguments or {}

                # Handle harvest "existing" mode: requires multi-step service execution
                if tool_name == "_harvest_execute":
                    harvest_service = HarvestService(client)
                    mcp_result = await harvest_service.execute_harvest(
                        source_campaign_id=arguments.get("source_campaign_id", ""),
                        sales_threshold=arguments.get("sales_threshold", 1.0),
                        acos_threshold=arguments.get("acos_threshold"),
                        target_mode=arguments.get("target_mode", "existing"),
                        target_campaign_id=arguments.get("target_campaign_id"),
                        target_ad_group_id=arguments.get("target_ad_group_id"),
                        match_type=arguments.get("match_type"),
                        negate_in_source=arguments.get("negate_in_source", True),
                        clicks_threshold=arguments.get("clicks_threshold"),
                        lookback_days=int(arguments.get("lookback_days") or 30),
                    )
                elif tool_name == "_ai_campaign_create":
                    # Full campaign creation: campaign → ad group → ad → targets
                    campaign_service = CampaignCreationService(client)
                    plan = arguments.get("plan", {})
                    mcp_result = await campaign_service.execute_plan(plan)
                else:
                    mcp_result = await _call_tool_with_resilience(client, tool_name, arguments)

                # Phase 5.3: best-effort read-back so the UI can show
                # whether Amazon actually applied what we asked for
                # (vs. silently clamping or queueing).
                aftercare: Optional[dict] = None
                verification: dict
                try:
                    if tool_name == "_harvest_execute" and isinstance(mcp_result, dict):
                        verification = await verify_harvest_execution(client, arguments, mcp_result)
                        aftercare = build_aftercare(tool_name, arguments, mcp_result, verification)
                    elif (
                        tool_name == "campaign_management-create_campaign_harvest_targets"
                        and isinstance(mcp_result, dict)
                    ):
                        verification = await verify_harvest_create_campaign_result(
                            client, arguments, mcp_result
                        )
                        aftercare = build_aftercare(tool_name, arguments, mcp_result, verification)
                    elif isinstance(tool_name, str) and tool_name.startswith("_"):
                        verification = {"ok": True, "skipped": True, "reason": "synthetic tool"}
                        aftercare = None
                    else:
                        verification = await verify_mutation(client, tool_name, arguments)
                        aftercare = build_aftercare(tool_name, arguments, mcp_result, verification)
                except Exception as ver_exc:
                    verification = {"ok": False, "error": str(ver_exc)[:300]}
                    aftercare = build_aftercare(tool_name, arguments, mcp_result, verification)

                change.status = "applied"
                change.applied_at = utcnow()
                # Phase 5.3: preserve the legacy ``apply_result`` shape
                # (= raw MCP response) so existing UIs keep working, and
                # tuck the aftercare report under a sibling ``_aftercare``
                # key when the MCP result is dict-shaped. Non-dict results
                # (legacy harvest / campaign-create services return rich
                # dicts already) get the {mcp_result, _aftercare} wrapper.
                if aftercare is None:
                    apply_payload = mcp_result
                elif isinstance(mcp_result, dict):
                    apply_payload = {**mcp_result, "_aftercare": aftercare}
                else:
                    apply_payload = {
                        "mcp_result": mcp_result,
                        "_aftercare": aftercare,
                    }
                change.apply_result = apply_payload
                applied += 1
                stats["applied"] += 1
                entry = {"id": str(change.id), "status": "applied"}
                if aftercare is not None:
                    entry["aftercare"] = {
                        "headline": aftercare.get("headline"),
                        "drift_count": len(aftercare.get("verification", {}).get("drift") or []),
                    }
                results.append(entry)
                stats["results"].append(entry)

            except Exception as e:
                change.status = "failed"
                change.error_message = str(e)
                failed += 1
                stats["failed"] += 1
                entry = {"id": str(change.id), "status": "failed", "error": str(e)}
                results.append(entry)
                stats["results"].append(entry)

    # Activity log per (credential, profile) for accurate per-account audit trail.
    for (cred_id_str, profile_id), stats in group_stats.items():
        cred = cred_cache.get(cred_id_str)
        if not cred:
            continue
        scope_label = f"profile {profile_id}" if profile_id else "default profile"
        db.add(ActivityLog(
            credential_id=cred.id,
            action="changes_applied",
            category="approvals",
            description=(
                f"Applied {stats['applied']} changes to Amazon Ads "
                f"({stats['failed']} failed) on {scope_label}"
            ),
            details={
                "profile_id": profile_id,
                "applied": stats["applied"],
                "failed": stats["failed"],
                "results": stats["results"],
            },
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
