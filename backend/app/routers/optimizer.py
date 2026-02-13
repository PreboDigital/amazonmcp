"""
Optimizer Router — Bid optimization based on ACOS/ROAS targets.
All rules, runs, and individual bid changes stored in PostgreSQL.
"""

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import (
    Credential, BidRule, OptimizationRun, BidChange, ActivityLog, Target, Account,
)
from app.mcp_client import create_mcp_client
from app.services.token_service import get_mcp_client_with_fresh_token
from app.services.optimizer_service import OptimizerService
from app.utils import parse_uuid, safe_error_detail, utcnow

router = APIRouter()


class BidRuleCreate(BaseModel):
    credential_id: Optional[str] = None
    name: str
    campaign_ids: Optional[list[str]] = None
    target_acos: float
    min_bid: float = 0.02
    max_bid: float = 100.0
    bid_step: float = 0.10
    lookback_days: int = 14
    min_clicks: int = 10


class BidRuleUpdate(BaseModel):
    name: Optional[str] = None
    target_acos: Optional[float] = None
    min_bid: Optional[float] = None
    max_bid: Optional[float] = None
    bid_step: Optional[float] = None
    lookback_days: Optional[int] = None
    min_clicks: Optional[int] = None
    is_active: Optional[bool] = None


class OptimizeRunRequest(BaseModel):
    credential_id: Optional[str] = None
    rule_id: str
    dry_run: bool = True  # Preview changes without applying


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


@router.get("/rules")
async def list_bid_rules(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List bid optimization rules from DB, with run stats."""
    query = select(BidRule).order_by(BidRule.created_at.desc())
    if credential_id:
        query = query.where(BidRule.credential_id == parse_uuid(credential_id))

    result = await db.execute(query)
    rules = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "credential_id": str(r.credential_id),
            "name": r.name,
            "campaign_ids": r.campaign_ids,
            "target_acos": r.target_acos,
            "min_bid": r.min_bid,
            "max_bid": r.max_bid,
            "bid_step": r.bid_step,
            "lookback_days": r.lookback_days,
            "min_clicks": r.min_clicks,
            "is_active": r.is_active,
            "total_targets_adjusted": r.total_targets_adjusted,
            "total_runs": r.total_runs,
            "status": r.status,
            "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rules
    ]


@router.post("/rules")
async def create_bid_rule(payload: BidRuleCreate, db: AsyncSession = Depends(get_db)):
    """Create a new bid optimization rule and store in DB."""
    cred = await _get_cred(db, payload.credential_id)

    rule = BidRule(
        credential_id=cred.id,
        name=payload.name,
        campaign_ids=payload.campaign_ids,
        target_acos=payload.target_acos,
        min_bid=payload.min_bid,
        max_bid=payload.max_bid,
        bid_step=payload.bid_step,
        lookback_days=payload.lookback_days,
        min_clicks=payload.min_clicks,
    )
    db.add(rule)
    await db.flush()

    db.add(ActivityLog(
        credential_id=cred.id,
        action="bid_rule_created",
        category="optimizer",
        description=f"Created bid rule: {payload.name} (target ACOS: {payload.target_acos}%)",
        entity_type="bid_rule",
        entity_id=str(rule.id),
    ))

    await db.flush()
    await db.refresh(rule)
    return {"id": str(rule.id), "name": rule.name, "status": "created"}


@router.put("/rules/{rule_id}")
async def update_bid_rule(rule_id: str, payload: BidRuleUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BidRule).where(BidRule.id == parse_uuid(rule_id)))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    update_data = payload.model_dump(exclude_none=True)
    for key, value in update_data.items():
        setattr(rule, key, value)
    rule.updated_at = utcnow()

    db.add(ActivityLog(
        credential_id=rule.credential_id,
        action="bid_rule_updated",
        category="optimizer",
        description=f"Updated bid rule: {rule.name}",
        entity_type="bid_rule",
        entity_id=rule_id,
        details={"updated_fields": list(update_data.keys())},
    ))

    await db.flush()
    return {"id": str(rule.id), "status": "updated"}


@router.delete("/rules/{rule_id}")
async def delete_bid_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BidRule).where(BidRule.id == parse_uuid(rule_id)))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.add(ActivityLog(
        credential_id=rule.credential_id,
        action="bid_rule_deleted",
        category="optimizer",
        description=f"Deleted bid rule: {rule.name}",
        entity_type="bid_rule",
        entity_id=rule_id,
    ))

    await db.delete(rule)
    return {"status": "deleted"}


@router.post("/run")
async def run_optimization(payload: OptimizeRunRequest, db: AsyncSession = Depends(get_db)):
    """
    Run bid optimization:
    1. Create OptimizationRun record
    2. Query targets and performance
    3. Calculate optimal bids
    4. Store all bid changes in BidChange table
    5. Preview or apply changes
    """
    cred = await _get_cred(db, payload.credential_id)
    result = await db.execute(select(BidRule).where(BidRule.id == parse_uuid(payload.rule_id)))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    if rule.credential_id != cred.id:
        raise HTTPException(
            status_code=403,
            detail="Rule belongs to a different account. Switch to the correct account to run this rule.",
        )

    # Require profile_id for MCP scoping and report creation
    adv_account_id = await _resolve_advertiser_account_id(db, cred)
    if not cred.profile_id:
        raise HTTPException(
            status_code=400,
            detail="No account profile selected. Use the account dropdown to select an account, or run Discover Accounts first.",
        )

    # Create optimization run record
    opt_run = OptimizationRun(
        rule_id=rule.id,
        credential_id=cred.id,
        dry_run=payload.dry_run,
        target_acos=rule.target_acos,
        status="running",
    )
    db.add(opt_run)
    await db.flush()

    client = await get_mcp_client_with_fresh_token(cred, db)

    service = OptimizerService(client, advertiser_account_id=adv_account_id)

    try:
        opt_result = await service.optimize_bids(
            campaign_ids=rule.campaign_ids,
            target_acos=rule.target_acos,
            min_bid=rule.min_bid,
            max_bid=rule.max_bid,
            bid_step=rule.bid_step,
            min_clicks=rule.min_clicks,
            dry_run=payload.dry_run,
        )

        summary = opt_result.get("summary", {})

        # Cache raw target data from MCP into DB
        raw_targets = opt_result.pop("_raw_targets", [])
        for group in raw_targets:
            target_list = group.get("targets", {})
            if isinstance(target_list, dict):
                for key in ["targets", "result", "results", "items"]:
                    if key in target_list and isinstance(target_list[key], list):
                        target_list = target_list[key]
                        break
                else:
                    target_list = []
            if isinstance(target_list, list):
                for t_data in target_list:
                    t_id = str(t_data.get("targetId") or t_data.get("id") or "")
                    if not t_id:
                        continue
                    existing = await db.execute(
                        select(Target).where(
                            Target.credential_id == cred.id,
                            Target.amazon_target_id == t_id,
                        )
                    )
                    target_obj = existing.scalar_one_or_none()
                    if target_obj:
                        # Update performance metrics from MCP
                        if t_data.get("bid") is not None:
                            target_obj.bid = float(t_data["bid"])
                        if t_data.get("spend") is not None:
                            target_obj.spend = float(t_data["spend"])
                        if t_data.get("sales") is not None:
                            target_obj.sales = float(t_data["sales"])
                        if t_data.get("clicks") is not None:
                            target_obj.clicks = int(t_data["clicks"])
                        if t_data.get("orders") is not None:
                            target_obj.orders = int(t_data["orders"])
                        target_obj.updated_at = utcnow()

        # Update optimization run record
        opt_run.status = "completed"
        opt_run.targets_analyzed = opt_result.get("targets_analyzed", 0)
        opt_run.targets_adjusted = opt_result.get("targets_adjusted", 0)
        opt_run.bid_increases = summary.get("increases", 0)
        opt_run.bid_decreases = summary.get("decreases", 0)
        opt_run.unchanged = summary.get("unchanged", 0)
        opt_run.summary_data = summary
        opt_run.completed_at = utcnow()

        # Store individual bid changes in DB
        changes = opt_result.get("changes", [])
        for change_data in changes:
            bid_change = BidChange(
                optimization_run_id=opt_run.id,
                amazon_target_id=change_data.get("target_id", "unknown"),
                amazon_campaign_id=change_data.get("campaign_id"),
                previous_bid=change_data.get("current_bid", 0),
                new_bid=change_data.get("new_bid", 0),
                bid_change=change_data.get("change", 0),
                direction=change_data.get("direction", "decrease"),
                reason=change_data.get("reason"),
                current_acos=change_data.get("current_acos"),
                clicks=change_data.get("clicks"),
                spend=change_data.get("spend"),
                sales=change_data.get("sales"),
                applied=not payload.dry_run,
            )
            db.add(bid_change)

        # Update rule aggregates
        rule.last_run_at = utcnow()
        rule.status = "completed"
        rule.total_runs = (rule.total_runs or 0) + 1
        if not payload.dry_run:
            rule.total_targets_adjusted = (rule.total_targets_adjusted or 0) + opt_run.targets_adjusted

        db.add(ActivityLog(
            credential_id=cred.id,
            action="optimization_run" if not payload.dry_run else "optimization_preview",
            category="optimizer",
            description=(
                f"{'Applied' if not payload.dry_run else 'Previewed'} optimization for rule '{rule.name}': "
                f"{opt_run.targets_adjusted} targets adjusted ({opt_run.bid_increases} up, {opt_run.bid_decreases} down)"
            ),
            entity_type="optimization_run",
            entity_id=str(opt_run.id),
            details={
                "run_id": str(opt_run.id),
                "rule_id": str(rule.id),
                "dry_run": payload.dry_run,
                "targets_analyzed": opt_run.targets_analyzed,
                "targets_adjusted": opt_run.targets_adjusted,
                "summary": summary,
            },
        ))

        await db.flush()

        return {
            **opt_result,
            "run_id": str(opt_run.id),
            "rule_id": str(rule.id),
        }
    except Exception as e:
        opt_run.status = "failed"
        opt_run.error_message = str(e)
        opt_run.completed_at = utcnow()
        rule.status = "failed"

        db.add(ActivityLog(
            credential_id=cred.id,
            action="optimization_failed",
            category="optimizer",
            description=str(e),
            status="error",
            entity_type="optimization_run",
            entity_id=str(opt_run.id),
        ))

        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Optimization failed. Please try again."))


@router.get("/runs")
async def list_optimization_runs(
    rule_id: Optional[str] = Query(None),
    credential_id: Optional[str] = Query(None),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
):
    """List optimization run history from DB."""
    query = select(OptimizationRun).order_by(OptimizationRun.started_at.desc()).limit(limit)
    if rule_id:
        query = query.where(OptimizationRun.rule_id == parse_uuid(rule_id))
    if credential_id:
        query = query.where(OptimizationRun.credential_id == parse_uuid(credential_id))

    result = await db.execute(query)
    runs = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "rule_id": str(r.rule_id),
            "credential_id": str(r.credential_id),
            "dry_run": r.dry_run,
            "status": r.status,
            "targets_analyzed": r.targets_analyzed,
            "targets_adjusted": r.targets_adjusted,
            "bid_increases": r.bid_increases,
            "bid_decreases": r.bid_decreases,
            "unchanged": r.unchanged,
            "target_acos": r.target_acos,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}/changes")
async def list_bid_changes(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List individual bid changes from a specific optimization run."""
    result = await db.execute(
        select(BidChange)
        .where(BidChange.optimization_run_id == parse_uuid(run_id))
        .order_by(BidChange.bid_change.desc())
    )
    changes = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "amazon_target_id": c.amazon_target_id,
            "amazon_campaign_id": c.amazon_campaign_id,
            "previous_bid": c.previous_bid,
            "new_bid": c.new_bid,
            "bid_change": c.bid_change,
            "direction": c.direction,
            "reason": c.reason,
            "current_acos": c.current_acos,
            "clicks": c.clicks,
            "spend": c.spend,
            "sales": c.sales,
            "applied": c.applied,
            "created_at": c.created_at.isoformat(),
        }
        for c in changes
    ]


@router.get("/activity")
async def get_activity_log(
    credential_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Get recent activity from DB, with optional filtering."""
    query = select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit)
    if credential_id:
        query = query.where(ActivityLog.credential_id == parse_uuid(credential_id))
    if category:
        query = query.where(ActivityLog.category == category)

    result = await db.execute(query)
    logs = result.scalars().all()
    return [
        {
            "id": str(log.id),
            "credential_id": str(log.credential_id) if log.credential_id else None,
            "action": log.action,
            "category": log.category,
            "description": log.description,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "status": log.status,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
