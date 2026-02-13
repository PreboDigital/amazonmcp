"""
Harvest Router — Automatic keyword harvesting from auto to manual campaigns.
All configs, runs, and harvested keywords stored in PostgreSQL.
Harvest runs now route through the approval queue for review before execution.
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
    Credential, Campaign, HarvestConfig, HarvestRun,
    HarvestedKeyword, ActivityLog, PendingChange,
)
from app.mcp_client import create_mcp_client
from app.services.token_service import get_mcp_client_with_fresh_token
from app.services.harvest_service import HarvestService
from app.utils import parse_uuid, safe_error_detail, utcnow

router = APIRouter()


# ── Request Models ────────────────────────────────────────────────────

class CampaignSelection(BaseModel):
    amazon_campaign_id: str
    campaign_name: Optional[str] = None
    targeting_type: Optional[str] = None
    state: Optional[str] = None
    daily_budget: Optional[float] = None


class TargetCampaignSelection(BaseModel):
    amazon_campaign_id: str
    campaign_name: Optional[str] = None


class HarvestCreateRequest(BaseModel):
    credential_id: Optional[str] = None
    name: str
    # Support both single and multi-campaign selection
    source_campaign_id: Optional[str] = None  # backward compat
    source_campaign_name: Optional[str] = None  # backward compat
    source_campaigns: Optional[list[CampaignSelection]] = None  # new multi-select
    # Target campaign: where harvested keywords go
    target_mode: str = "new"  # "new" = Amazon creates new campaign, "existing" = user selects
    target_campaign_selection: Optional[TargetCampaignSelection] = None  # when target_mode="existing"
    # Negative keyword handling
    negate_in_source: bool = True  # Negate harvested keywords in source auto campaign
    # Thresholds
    sales_threshold: float = 1.0
    acos_threshold: Optional[float] = None
    clicks_threshold: Optional[int] = None
    match_type: Optional[str] = None  # broad, phrase, exact, or null for all
    lookback_days: int = 30


class HarvestRunRequest(BaseModel):
    credential_id: Optional[str] = None
    config_id: str
    send_to_approval: bool = True  # Route through approval queue by default


class HarvestPreviewRequest(BaseModel):
    credential_id: Optional[str] = None
    config_id: str


async def _get_cred(db: AsyncSession, cred_id: str = None) -> Credential:
    if cred_id:
        result = await db.execute(select(Credential).where(Credential.id == parse_uuid(cred_id, "credential_id")))
    else:
        result = await db.execute(select(Credential).where(Credential.is_default == True))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credential found.")
    return cred


# ── Configs ────────────────────────────────────────────────────────────

@router.get("/configs")
async def list_harvest_configs(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List harvest configurations from DB, with run stats."""
    query = select(HarvestConfig).order_by(HarvestConfig.created_at.desc())
    if credential_id:
        query = query.where(HarvestConfig.credential_id == parse_uuid(credential_id, "credential_id"))

    result = await db.execute(query)
    configs = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "credential_id": str(c.credential_id),
            "name": c.name,
            "source_campaign_id": c.source_campaign_id,
            "source_campaign_name": c.source_campaign_name,
            "source_campaigns": c.source_campaigns or [],
            "target_campaign_id": c.target_campaign_id,
            "target_campaign_name": c.target_campaign_name,
            "target_mode": c.target_mode or "new",
            "target_campaign_selection": c.target_campaign_selection,
            "negate_in_source": c.negate_in_source if c.negate_in_source is not None else True,
            "sales_threshold": c.sales_threshold,
            "acos_threshold": c.acos_threshold,
            "clicks_threshold": c.clicks_threshold,
            "match_type": c.match_type,
            "lookback_days": c.lookback_days,
            "is_active": c.is_active,
            "total_keywords_harvested": c.total_keywords_harvested,
            "total_runs": c.total_runs,
            "status": c.status,
            "last_harvested_at": c.last_harvested_at.isoformat() if c.last_harvested_at else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in configs
    ]


@router.post("/configs")
async def create_harvest_config(payload: HarvestCreateRequest, db: AsyncSession = Depends(get_db)):
    """Create a new keyword harvesting configuration with multi-campaign support."""
    cred = await _get_cred(db, payload.credential_id)

    # Determine source campaigns
    source_campaigns_data = []
    primary_campaign_id = payload.source_campaign_id or ""
    primary_campaign_name = payload.source_campaign_name

    if payload.source_campaigns and len(payload.source_campaigns) > 0:
        # Multi-campaign mode
        source_campaigns_data = [c.model_dump() for c in payload.source_campaigns]
        primary_campaign_id = payload.source_campaigns[0].amazon_campaign_id
        primary_campaign_name = payload.source_campaigns[0].campaign_name
    elif payload.source_campaign_id:
        # Legacy single-campaign mode
        source_campaigns_data = [{
            "amazon_campaign_id": payload.source_campaign_id,
            "campaign_name": payload.source_campaign_name,
        }]

    if not primary_campaign_id and not source_campaigns_data:
        raise HTTPException(status_code=400, detail="At least one source campaign is required")

    config = HarvestConfig(
        credential_id=cred.id,
        name=payload.name,
        source_campaign_id=primary_campaign_id,
        source_campaign_name=primary_campaign_name,
        source_campaigns=source_campaigns_data,
        target_mode=payload.target_mode,
        target_campaign_selection=payload.target_campaign_selection.model_dump() if payload.target_campaign_selection else None,
        target_campaign_id=payload.target_campaign_selection.amazon_campaign_id if payload.target_campaign_selection else None,
        target_campaign_name=payload.target_campaign_selection.campaign_name if payload.target_campaign_selection else None,
        negate_in_source=payload.negate_in_source,
        sales_threshold=payload.sales_threshold,
        acos_threshold=payload.acos_threshold,
        clicks_threshold=payload.clicks_threshold,
        match_type=payload.match_type,
        lookback_days=payload.lookback_days,
    )
    db.add(config)
    await db.flush()

    campaign_names = [c.get("campaign_name", "Unknown") for c in source_campaigns_data]
    desc = f"Created harvest config: {payload.name} ({len(source_campaigns_data)} source campaigns: {', '.join(campaign_names[:3])})"

    db.add(ActivityLog(
        credential_id=cred.id,
        action="harvest_config_created",
        category="harvest",
        description=desc,
        entity_type="harvest_config",
        entity_id=str(config.id),
    ))

    await db.flush()
    await db.refresh(config)
    return {
        "id": str(config.id),
        "name": config.name,
        "source_campaigns_count": len(source_campaigns_data),
        "status": "created",
    }


# ── Run / Preview ─────────────────────────────────────────────────────

@router.post("/preview")
async def preview_harvest(payload: HarvestPreviewRequest, db: AsyncSession = Depends(get_db)):
    """
    Preview what a harvest run would do without actually executing.
    Fetches keyword candidates from the source campaign(s).
    """
    cred = await _get_cred(db, payload.credential_id)
    result = await db.execute(
        select(HarvestConfig).where(HarvestConfig.id == parse_uuid(payload.config_id, "config_id"))
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Harvest config not found")
    if config.credential_id != cred.id:
        raise HTTPException(
            status_code=403,
            detail="Harvest config belongs to a different account. Switch to the correct account to preview.",
        )
    if not cred.profile_id:
        raise HTTPException(
            status_code=400,
            detail="No account profile selected. Use the account dropdown to select an account, or run Discover Accounts first.",
        )

    client = await get_mcp_client_with_fresh_token(cred, db)
    service = HarvestService(client)

    all_candidates = []
    source_campaigns = config.source_campaigns or [{"amazon_campaign_id": config.source_campaign_id}]

    for camp in source_campaigns:
        campaign_id = camp.get("amazon_campaign_id", "")
        try:
            candidates = await service.get_harvest_candidates(campaign_id)
            all_candidates.append({
                "campaign_id": campaign_id,
                "campaign_name": camp.get("campaign_name", "Unknown"),
                "targets": candidates.get("targets", []),
            })
        except Exception as e:
            all_candidates.append({
                "campaign_id": campaign_id,
                "campaign_name": camp.get("campaign_name", "Unknown"),
                "error": str(e),
            })

    return {
        "config_id": str(config.id),
        "config_name": config.name,
        "source_campaigns": all_candidates,
        "thresholds": {
            "sales": config.sales_threshold,
            "acos": config.acos_threshold,
            "clicks": config.clicks_threshold,
        },
    }


@router.post("/run")
async def run_harvest(payload: HarvestRunRequest, db: AsyncSession = Depends(get_db)):
    """
    Execute keyword harvesting. When send_to_approval=True (default),
    creates PendingChange entries in the approval queue instead of executing directly.
    When send_to_approval=False, executes immediately.
    """
    cred = await _get_cred(db, payload.credential_id)
    result = await db.execute(
        select(HarvestConfig).where(HarvestConfig.id == parse_uuid(payload.config_id, "config_id"))
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Harvest config not found")
    if config.credential_id != cred.id:
        raise HTTPException(
            status_code=403,
            detail="Harvest config belongs to a different account. Switch to the correct account to run this config.",
        )
    if not cred.profile_id:
        raise HTTPException(
            status_code=400,
            detail="No account profile selected. Use the account dropdown to select an account, or run Discover Accounts first.",
        )

    source_campaigns = config.source_campaigns or [
        {"amazon_campaign_id": config.source_campaign_id, "campaign_name": config.source_campaign_name}
    ]

    target_mode = config.target_mode or "new"
    target_selection = config.target_campaign_selection or {}
    negate_in_source = config.negate_in_source if config.negate_in_source is not None else True

    # Build target campaign description for clarity
    if target_mode == "existing" and target_selection:
        target_desc = f"→ {target_selection.get('campaign_name', target_selection.get('amazon_campaign_id', 'unknown'))}"
    else:
        target_desc = "→ New manual campaign (auto-created by Amazon)"

    # ── Route through approval queue ──
    if payload.send_to_approval:
        batch_id = f"harvest-{config.id}-{utcnow().strftime('%Y%m%d%H%M%S')}"
        changes_created = []

        for camp in source_campaigns:
            campaign_id = camp.get("amazon_campaign_id", "")
            campaign_name = camp.get("campaign_name", "Unknown")

            harvest_request = {
                "sourceCampaignId": campaign_id,
                "salesThreshold": config.sales_threshold,
            }
            if config.acos_threshold is not None:
                harvest_request["acosThreshold"] = config.acos_threshold

            # Build proposed value with clear target info
            proposed_parts = [f"Harvest keywords (sales >= {config.sales_threshold}"]
            if config.acos_threshold:
                proposed_parts.append(f", ACOS <= {config.acos_threshold}%")
            proposed_parts.append(f") {target_desc}")
            if negate_in_source:
                proposed_parts.append(" + negate in source")
            proposed_value = "".join(proposed_parts)

            change = PendingChange(
                credential_id=cred.id,
                profile_id=cred.profile_id,
                change_type="harvest",
                entity_type="campaign",
                entity_id=campaign_id,
                entity_name=campaign_name,
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                current_value="auto campaign",
                proposed_value=proposed_value,
                change_detail={
                    "config_id": str(config.id),
                    "config_name": config.name,
                    "harvest_request": harvest_request,
                    "target_mode": target_mode,
                    "target_campaign": target_selection if target_mode == "existing" else "Amazon will create new manual campaign",
                    "negate_in_source": negate_in_source,
                    "match_type": config.match_type,
                    "lookback_days": config.lookback_days,
                    "clicks_threshold": config.clicks_threshold,
                    "source_campaigns_count": len(source_campaigns),
                },
                mcp_payload={
                    "tool": "campaign_management-create_campaign_harvest_targets",
                    "arguments": {
                        "body": {
                            "harvestRequests": [harvest_request],
                        }
                    },
                } if target_mode == "new" else {
                    "tool": "_harvest_execute",
                    "arguments": {
                        "config_id": str(config.id),
                        "source_campaign_id": campaign_id,
                        "target_campaign_id": target_selection.get("amazon_campaign_id") if target_selection else None,
                        "sales_threshold": config.sales_threshold,
                        "acos_threshold": config.acos_threshold,
                        "match_type": config.match_type,
                        "negate_in_source": negate_in_source,
                        "target_mode": target_mode,
                    },
                },
                source="harvester",
                ai_reasoning=(
                    f"Harvest high-performing keywords from '{campaign_name}' auto campaign "
                    f"{target_desc}. "
                    f"Thresholds: sales >= {config.sales_threshold}"
                    + (f", ACOS <= {config.acos_threshold}%" if config.acos_threshold else "")
                    + (". Harvested keywords will be negated in the source auto campaign to prevent cannibalization." if negate_in_source else "")
                ),
                batch_id=batch_id,
                batch_label=f"Harvest: {config.name}",
            )
            db.add(change)
            changes_created.append(campaign_name)

        db.add(ActivityLog(
            credential_id=cred.id,
            action="harvest_queued_for_approval",
            category="harvest",
            description=f"Queued harvest '{config.name}' for approval ({len(source_campaigns)} campaigns) {target_desc}",
            entity_type="harvest_config",
            entity_id=str(config.id),
            details={
                "batch_id": batch_id,
                "campaigns": changes_created,
                "target_mode": target_mode,
                "negate_in_source": negate_in_source,
            },
        ))

        config.status = "pending_approval"
        await db.flush()

        return {
            "status": "queued_for_approval",
            "batch_id": batch_id,
            "changes_created": len(changes_created),
            "config_id": str(config.id),
            "target_mode": target_mode,
            "target_description": target_desc,
            "negate_in_source": negate_in_source,
            "message": f"Harvest queued for review. {len(changes_created)} change(s) sent to the Approval Queue. Target: {target_desc}",
        }

    # ── Direct execution (legacy / override) ──
    harvest_run = HarvestRun(
        config_id=config.id,
        credential_id=cred.id,
        source_campaign_id=config.source_campaign_id,
        status="running",
    )
    db.add(harvest_run)
    await db.flush()

    client = await get_mcp_client_with_fresh_token(cred, db)
    service = HarvestService(client)

    try:
        # Execute for each source campaign
        all_keywords = 0
        combined_result = {}

        for camp in source_campaigns:
            campaign_id = camp.get("amazon_campaign_id", config.source_campaign_id)
            harvest_result = await service.execute_harvest(
                source_campaign_id=campaign_id,
                sales_threshold=config.sales_threshold,
                acos_threshold=config.acos_threshold,
                target_mode=target_mode,
                target_campaign_id=target_selection.get("amazon_campaign_id") if target_selection else None,
                match_type=config.match_type,
                negate_in_source=negate_in_source,
            )

            kw_count = harvest_result.get("keywords_harvested", 0)
            all_keywords += kw_count

            # Store individual harvested keywords
            keywords = harvest_result.get("keywords", [])
            for kw_data in keywords:
                keyword = HarvestedKeyword(
                    harvest_run_id=harvest_run.id,
                    keyword_text=kw_data.get("keyword") or kw_data.get("text", "unknown"),
                    match_type=kw_data.get("matchType"),
                    bid=kw_data.get("bid"),
                    source_clicks=kw_data.get("clicks"),
                    source_spend=kw_data.get("spend"),
                    source_sales=kw_data.get("sales"),
                    source_acos=kw_data.get("acos"),
                )
                db.add(keyword)

            combined_result = harvest_result  # keep last result for metadata

        harvest_run.status = "completed"
        harvest_run.target_campaign_id = combined_result.get("target_campaign_id")
        harvest_run.keywords_harvested = all_keywords
        harvest_run.raw_result = combined_result
        harvest_run.completed_at = utcnow()

        config.status = harvest_run.status
        config.last_harvested_at = utcnow()
        config.target_campaign_id = combined_result.get("target_campaign_id") or config.target_campaign_id
        config.total_keywords_harvested = (config.total_keywords_harvested or 0) + all_keywords
        config.total_runs = (config.total_runs or 0) + 1
        config.config_data = combined_result

        db.add(ActivityLog(
            credential_id=cred.id,
            action="harvest_executed",
            category="harvest",
            description=f"Harvest run for '{config.name}': {all_keywords} keywords harvested",
            entity_type="harvest_run",
            entity_id=str(harvest_run.id),
            details={
                "run_id": str(harvest_run.id),
                "config_id": str(config.id),
                "keywords_harvested": all_keywords,
            },
        ))

        await db.flush()
        return {
            **combined_result,
            "run_id": str(harvest_run.id),
            "config_id": str(config.id),
            "keywords_harvested": all_keywords,
        }

    except Exception as e:
        harvest_run.status = "failed"
        harvest_run.error_message = str(e)
        harvest_run.completed_at = utcnow()
        config.status = "failed"

        db.add(ActivityLog(
            credential_id=cred.id,
            action="harvest_failed",
            category="harvest",
            description=str(e),
            status="error",
            entity_type="harvest_run",
            entity_id=str(harvest_run.id),
        ))

        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Harvest operation failed. Please try again."))


# ── History ────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_harvest_runs(
    config_id: Optional[str] = Query(None),
    credential_id: Optional[str] = Query(None),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
):
    """List harvest run history from DB."""
    query = select(HarvestRun).order_by(HarvestRun.started_at.desc()).limit(limit)
    if config_id:
        query = query.where(HarvestRun.config_id == parse_uuid(config_id, "config_id"))
    if credential_id:
        query = query.where(HarvestRun.credential_id == parse_uuid(credential_id, "credential_id"))

    result = await db.execute(query)
    runs = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "config_id": str(r.config_id),
            "credential_id": str(r.credential_id),
            "status": r.status,
            "source_campaign_id": r.source_campaign_id,
            "target_campaign_id": r.target_campaign_id,
            "keywords_harvested": r.keywords_harvested,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}/keywords")
async def list_harvested_keywords(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List individual keywords harvested in a specific run."""
    result = await db.execute(
        select(HarvestedKeyword)
        .where(HarvestedKeyword.harvest_run_id == parse_uuid(run_id, "run_id"))
        .order_by(HarvestedKeyword.created_at)
    )
    keywords = result.scalars().all()
    return [
        {
            "id": str(k.id),
            "keyword_text": k.keyword_text,
            "match_type": k.match_type,
            "bid": k.bid,
            "source_clicks": k.source_clicks,
            "source_spend": k.source_spend,
            "source_sales": k.source_sales,
            "source_acos": k.source_acos,
            "created_at": k.created_at.isoformat(),
        }
        for k in keywords
    ]


# ── Config management ──────────────────────────────────────────────────

@router.delete("/configs/{config_id}")
async def delete_harvest_config(config_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(HarvestConfig).where(HarvestConfig.id == parse_uuid(config_id, "config_id"))
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    db.add(ActivityLog(
        credential_id=config.credential_id,
        action="harvest_config_deleted",
        category="harvest",
        description=f"Deleted harvest config: {config.name}",
        entity_type="harvest_config",
        entity_id=config_id,
    ))

    await db.delete(config)
    return {"status": "deleted"}


@router.put("/configs/{config_id}")
async def update_harvest_config(config_id: str, payload: HarvestCreateRequest, db: AsyncSession = Depends(get_db)):
    """Update an existing harvest configuration."""
    result = await db.execute(
        select(HarvestConfig).where(HarvestConfig.id == parse_uuid(config_id, "config_id"))
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    config.name = payload.name
    config.sales_threshold = payload.sales_threshold
    config.acos_threshold = payload.acos_threshold
    config.clicks_threshold = payload.clicks_threshold
    config.match_type = payload.match_type
    config.lookback_days = payload.lookback_days
    config.target_mode = payload.target_mode
    config.negate_in_source = payload.negate_in_source
    config.target_campaign_selection = payload.target_campaign_selection.model_dump() if payload.target_campaign_selection else None
    if payload.target_campaign_selection:
        config.target_campaign_id = payload.target_campaign_selection.amazon_campaign_id
        config.target_campaign_name = payload.target_campaign_selection.campaign_name

    if payload.source_campaigns:
        config.source_campaigns = [c.model_dump() for c in payload.source_campaigns]
        config.source_campaign_id = payload.source_campaigns[0].amazon_campaign_id
        config.source_campaign_name = payload.source_campaigns[0].campaign_name
    elif payload.source_campaign_id:
        config.source_campaign_id = payload.source_campaign_id
        config.source_campaign_name = payload.source_campaign_name

    db.add(ActivityLog(
        credential_id=config.credential_id,
        action="harvest_config_updated",
        category="harvest",
        description=f"Updated harvest config: {config.name}",
        entity_type="harvest_config",
        entity_id=config_id,
    ))

    await db.flush()
    await db.refresh(config)
    return {"id": str(config.id), "name": config.name, "status": "updated"}


# ── Campaign listing ───────────────────────────────────────────────────

@router.get("/campaigns")
async def list_auto_campaigns(
    credential_id: Optional[str] = Query(None),
    targeting_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List campaigns (from DB cache or MCP sync). Can filter by targeting_type."""
    cred = await _get_cred(db, credential_id)
    if not cred.profile_id:
        raise HTTPException(
            status_code=400,
            detail="No account profile selected. Use the account dropdown to select an account, or run Discover Accounts first.",
        )

    # Try to return from DB cache first
    query = select(Campaign).where(
        Campaign.credential_id == cred.id,
    ).order_by(Campaign.campaign_name)

    result = await db.execute(query)
    campaigns = result.scalars().all()

    # If no cached campaigns, fetch from MCP and store
    if not campaigns:
        client = await get_mcp_client_with_fresh_token(cred, db)
        try:
            raw_campaigns = await client.query_campaigns()
            campaign_list = raw_campaigns if isinstance(raw_campaigns, list) else []
            if isinstance(raw_campaigns, dict):
                for key in ["campaigns", "result", "results", "items"]:
                    if key in raw_campaigns and isinstance(raw_campaigns[key], list):
                        campaign_list = raw_campaigns[key]
                        break

            for camp_data in campaign_list:
                amazon_id = camp_data.get("campaignId") or camp_data.get("id") or str(uuid.uuid4())

                camp_type = (
                    camp_data.get("adProduct")
                    or camp_data.get("campaignType")
                    or camp_data.get("type")
                )

                targeting = camp_data.get("targetingType") or camp_data.get("targeting")
                if not targeting and camp_data.get("autoCreationSettings"):
                    auto_targets = camp_data["autoCreationSettings"].get("autoCreateTargets", False)
                    targeting = "auto" if auto_targets else "manual"

                budget = camp_data.get("dailyBudget") or camp_data.get("budget")
                if not budget and camp_data.get("budgets"):
                    for b in camp_data["budgets"]:
                        if b.get("recurrenceTimePeriod") == "DAILY":
                            mv = (
                                b.get("budgetValue", {})
                                .get("monetaryBudgetValue", {})
                                .get("monetaryBudget", {})
                            )
                            budget = mv.get("value")
                            break

                campaign = Campaign(
                    credential_id=cred.id,
                    amazon_campaign_id=str(amazon_id),
                    campaign_name=camp_data.get("name") or camp_data.get("campaignName"),
                    campaign_type=camp_type,
                    targeting_type=targeting,
                    state=camp_data.get("state") or camp_data.get("status"),
                    daily_budget=float(budget) if budget else None,
                    start_date=camp_data.get("startDate") or camp_data.get("startDateTime"),
                    end_date=camp_data.get("endDate") or camp_data.get("endDateTime"),
                    raw_data=camp_data,
                )
                db.add(campaign)

            await db.flush()

            result = await db.execute(
                select(Campaign).where(
                    Campaign.credential_id == cred.id,
                ).order_by(Campaign.campaign_name)
            )
            campaigns = result.scalars().all()
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Harvest operation failed. Please try again."))

    # Filter by targeting type if specified
    if targeting_type:
        campaigns = [c for c in campaigns if c.targeting_type and c.targeting_type.lower() == targeting_type.lower()]

    return [
        {
            "id": str(c.id),
            "amazon_campaign_id": c.amazon_campaign_id,
            "campaign_name": c.campaign_name,
            "campaign_type": c.campaign_type,
            "targeting_type": c.targeting_type,
            "state": c.state,
            "daily_budget": c.daily_budget,
        }
        for c in campaigns
    ]
