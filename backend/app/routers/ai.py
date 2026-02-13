"""
AI Router — AI-powered insights, chat, optimization recommendations, and campaign building.
Integrates with OpenAI for intelligent analysis of Amazon Ads data.
"""

import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import (
    Credential, Campaign, AdGroup, Target, AuditSnapshot,
    AuditIssue, AuditOpportunity,
    PendingChange, AIConversation, ActivityLog, Account,
    CampaignPerformanceDaily, AccountPerformanceDaily,
    BidRule, OptimizationRun,
    HarvestConfig, HarvestRun, HarvestedKeyword,
    AppSettings,
)
from app.config import get_settings
from app.services.ai_service import create_ai_service
from app.services.search_term_service import get_search_term_summary
from app.services.token_service import get_mcp_client_with_fresh_token
from app.routers.settings import get_effective_api_keys
from app.utils import parse_uuid, utcnow

router = APIRouter()
settings = get_settings()


# ── Request Models ────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    credential_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message: str
    model_id: Optional[str] = None  # Override default LLM for this request


class InsightsRequest(BaseModel):
    credential_id: Optional[str] = None


class OptimizeRequest(BaseModel):
    credential_id: Optional[str] = None
    target_acos: float = 30.0


class CampaignBuildRequest(BaseModel):
    credential_id: Optional[str] = None
    product_name: str
    product_asin: Optional[str] = None
    product_category: Optional[str] = None
    daily_budget: float = 50.0
    target_acos: float = 30.0
    campaign_type: str = "SPONSORED_PRODUCTS"
    targeting_type: str = "auto"
    goals: Optional[str] = None
    keywords: Optional[list[str]] = None


class CampaignPublishRequest(BaseModel):
    """Publish an AI-generated campaign plan to the approval queue."""
    credential_id: Optional[str] = None
    plan: dict  # Full plan from build-campaign
    product_asin: str  # Required for SP ads


class ApplyInlineRequest(BaseModel):
    """Apply one or more inline actions directly via MCP (approved in chat)."""
    credential_id: Optional[str] = None
    actions: list[dict]  # [{ tool, arguments, label, change_type, entity_name, ... }]


# ── Helpers ───────────────────────────────────────────────────────────

async def _get_cred(db: AsyncSession, cred_id: str = None) -> Credential:
    if cred_id:
        result = await db.execute(select(Credential).where(Credential.id == parse_uuid(cred_id)))
    else:
        result = await db.execute(select(Credential).where(Credential.is_default == True))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credential found.")
    return cred


async def _get_account_context(db: AsyncSession, cred: Credential) -> dict:
    """
    Build a comprehensive context dict from the user's account data for AI.
    Includes campaigns, ad groups, targets, audit issues, performance trends,
    pending changes, bid rules, optimization history, harvested keywords,
    and recent activity — everything the AI needs to answer any question.
    """

    # ── 1. Active profile info ────────────────────────────────────────
    active_profile = None
    if cred.profile_id:
        profile_result = await db.execute(
            select(Account).where(
                Account.credential_id == cred.id,
                Account.profile_id == cred.profile_id,
            )
        )
        active_profile = profile_result.scalar_one_or_none()

    # ── 2. All campaigns with performance ─────────────────────────────
    campaigns_result = await db.execute(
        select(Campaign).where(Campaign.credential_id == cred.id)
    )
    campaigns = campaigns_result.scalars().all()

    active = sum(1 for c in campaigns if c.state and c.state.upper() == "ENABLED")
    paused = sum(1 for c in campaigns if c.state and c.state.upper() == "PAUSED")
    total_spend = sum(c.spend or 0 for c in campaigns)
    total_sales = sum(c.sales or 0 for c in campaigns)

    # Fallback: pull from campaign_performance_daily if campaigns lack perf data
    perf_by_campaign: dict = {}
    if total_spend == 0 and campaigns:
        perf_result = await db.execute(
            select(
                CampaignPerformanceDaily.amazon_campaign_id,
                func.sum(CampaignPerformanceDaily.spend).label("spend"),
                func.sum(CampaignPerformanceDaily.sales).label("sales"),
                func.sum(CampaignPerformanceDaily.clicks).label("clicks"),
                func.sum(CampaignPerformanceDaily.impressions).label("impressions"),
                func.sum(CampaignPerformanceDaily.orders).label("orders"),
            )
            .where(CampaignPerformanceDaily.credential_id == cred.id)
            .group_by(CampaignPerformanceDaily.amazon_campaign_id)
        )
        for row in perf_result.all():
            perf_by_campaign[row.amazon_campaign_id] = {
                "spend": float(row.spend or 0),
                "sales": float(row.sales or 0),
                "clicks": int(row.clicks or 0),
                "impressions": int(row.impressions or 0),
                "orders": int(row.orders or 0),
            }
        total_spend = sum(p["spend"] for p in perf_by_campaign.values())
        total_sales = sum(p["sales"] for p in perf_by_campaign.values())

    avg_acos = (total_spend / total_sales * 100) if total_sales > 0 else 0
    total_clicks = sum((c.clicks or 0) or perf_by_campaign.get(c.amazon_campaign_id, {}).get("clicks", 0) for c in campaigns)
    total_impressions = sum((c.impressions or 0) or perf_by_campaign.get(c.amazon_campaign_id, {}).get("impressions", 0) for c in campaigns)
    total_orders = sum((c.orders or 0) or perf_by_campaign.get(c.amazon_campaign_id, {}).get("orders", 0) for c in campaigns)

    # Enriched campaign list — ALL campaigns, not just top N
    all_campaigns = []
    for c in campaigns:
        perf = perf_by_campaign.get(c.amazon_campaign_id, {})
        spend = c.spend or perf.get("spend", 0)
        sales = c.sales or perf.get("sales", 0)
        clicks = c.clicks or perf.get("clicks", 0)
        impressions = c.impressions or perf.get("impressions", 0)
        orders = c.orders or perf.get("orders", 0)
        all_campaigns.append({
            "id": c.amazon_campaign_id,
            "campaign_id": c.amazon_campaign_id,
            "name": c.campaign_name,
            "type": c.campaign_type,
            "targeting": c.targeting_type,
            "state": c.state,
            "budget": c.daily_budget,
            "start_date": c.start_date,
            "end_date": c.end_date,
            "spend": spend,
            "sales": sales,
            "acos": c.acos or (round(spend / sales * 100, 1) if sales > 0 else 0),
            "roas": c.roas or (round(sales / spend, 2) if spend > 0 else 0),
            "impressions": impressions,
            "clicks": clicks,
            "orders": orders,
            "ctr": round(clicks / impressions * 100, 2) if impressions > 0 else 0,
            "cpc": round(spend / clicks, 2) if clicks > 0 else 0,
            "cvr": round(orders / clicks * 100, 2) if clicks > 0 else 0,
        })

    # Sort by spend descending for display
    all_campaigns.sort(key=lambda c: c["spend"] or 0, reverse=True)

    # ── 3. Ad groups ──────────────────────────────────────────────────
    ad_groups_result = await db.execute(
        select(AdGroup).where(AdGroup.credential_id == cred.id)
    )
    ad_groups = ad_groups_result.scalars().all()

    # Build a campaign name lookup for ad group context
    camp_name_map = {c.amazon_campaign_id: c.campaign_name for c in campaigns}

    all_ad_groups = [
        {
            "ad_group_id": ag.amazon_ad_group_id,
            "name": ag.ad_group_name,
            "state": ag.state,
            "default_bid": ag.default_bid,
            "campaign_id": ag.amazon_campaign_id,
            "campaign_name": camp_name_map.get(ag.amazon_campaign_id, "Unknown"),
        }
        for ag in ad_groups
    ]

    # ── 4. Targets / keywords — comprehensive ────────────────────────
    targets_result = await db.execute(
        select(Target).where(Target.credential_id == cred.id)
    )
    targets = targets_result.scalars().all()
    target_count = len(targets)

    # Categorize targets
    top_targets = sorted(targets, key=lambda t: t.spend or 0, reverse=True)[:30]
    top_converters = sorted(
        [t for t in targets if (t.orders or 0) > 0],
        key=lambda t: t.orders or 0, reverse=True,
    )[:30]
    non_converting = sorted(
        [t for t in targets if (t.clicks or 0) > 0 and (t.orders or 0) == 0],
        key=lambda t: t.spend or 0, reverse=True,
    )[:50]
    high_acos_targets = sorted(
        [t for t in targets if (t.acos or 0) > 50 and (t.spend or 0) > 0],
        key=lambda t: t.spend or 0, reverse=True,
    )[:20]

    # Target type breakdown
    targets_by_type = {}
    targets_by_state = {}
    targets_by_match = {}
    for t in targets:
        tt = t.target_type or "unknown"
        ts = t.state or "unknown"
        tm = t.match_type or "unknown"
        targets_by_type[tt] = targets_by_type.get(tt, 0) + 1
        targets_by_state[ts] = targets_by_state.get(ts, 0) + 1
        targets_by_match[tm] = targets_by_match.get(tm, 0) + 1

    def _target_dict(t, include_campaign=True):
        d = {
            "target_id": t.amazon_target_id,
            "ad_group_id": t.amazon_ad_group_id,
            "keyword": t.expression_value,
            "type": t.target_type,
            "match_type": t.match_type,
            "state": t.state,
            "bid": t.bid,
            "spend": t.spend,
            "sales": t.sales,
            "acos": t.acos,
            "clicks": t.clicks,
            "impressions": t.impressions,
            "orders": t.orders,
        }
        if include_campaign:
            d["campaign_id"] = t.amazon_campaign_id
            d["campaign_name"] = camp_name_map.get(t.amazon_campaign_id, "Unknown")
        return d

    # ── 5. Latest audit — full issues and opportunities ───────────────
    audit_result = await db.execute(
        select(AuditSnapshot)
        .where(AuditSnapshot.credential_id == cred.id)
        .order_by(AuditSnapshot.created_at.desc())
        .limit(1)
    )
    latest_audit = audit_result.scalar_one_or_none()

    audit_data = None
    if latest_audit:
        # Fetch detailed issues
        issues_result = await db.execute(
            select(AuditIssue)
            .where(AuditIssue.snapshot_id == latest_audit.id)
            .order_by(AuditIssue.severity.desc())
            .limit(30)
        )
        issues = issues_result.scalars().all()

        # Fetch detailed opportunities
        opps_result = await db.execute(
            select(AuditOpportunity)
            .where(AuditOpportunity.snapshot_id == latest_audit.id)
            .limit(30)
        )
        opportunities = opps_result.scalars().all()

        audit_data = {
            "date": latest_audit.created_at.isoformat() if latest_audit.created_at else None,
            "campaigns_count": latest_audit.campaigns_count,
            "active_campaigns": latest_audit.active_campaigns,
            "total_targets": latest_audit.total_targets,
            "total_spend": latest_audit.total_spend,
            "total_sales": latest_audit.total_sales,
            "avg_acos": latest_audit.avg_acos,
            "avg_roas": latest_audit.avg_roas,
            "waste_identified": latest_audit.waste_identified,
            "issues_count": latest_audit.issues_count,
            "opportunities_count": latest_audit.opportunities_count,
            "issues": [
                {
                    "severity": iss.severity,
                    "type": iss.issue_type,
                    "message": iss.message,
                    "campaign_name": iss.campaign_name,
                }
                for iss in issues
            ],
            "opportunities": [
                {
                    "type": opp.opportunity_type,
                    "message": opp.message,
                    "impact": opp.potential_impact,
                    "campaign_name": opp.campaign_name,
                }
                for opp in opportunities
            ],
        }

    # ── 6. Historical performance trends (last 30 days) ───────────────
    thirty_days_ago = (utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    today_str = utcnow().strftime("%Y-%m-%d")

    trend_result = await db.execute(
        select(AccountPerformanceDaily)
        .where(and_(
            AccountPerformanceDaily.credential_id == cred.id,
            AccountPerformanceDaily.date >= thirty_days_ago,
            AccountPerformanceDaily.date <= today_str,
        ))
        .order_by(AccountPerformanceDaily.date.asc())
    )
    daily_trends = trend_result.scalars().all()

    performance_trend = [
        {
            "date": d.date,
            "spend": d.total_spend or 0,
            "sales": d.total_sales or 0,
            "impressions": d.total_impressions or 0,
            "clicks": d.total_clicks or 0,
            "orders": d.total_orders or 0,
            "acos": d.avg_acos or 0,
            "roas": d.avg_roas or 0,
            "ctr": d.avg_ctr or 0,
            "cpc": d.avg_cpc or 0,
        }
        for d in daily_trends
    ]

    # ── 7. Pending changes (full details) ─────────────────────────────
    pending_result = await db.execute(
        select(PendingChange)
        .where(PendingChange.credential_id == cred.id)
        .where(PendingChange.status == "pending")
        .order_by(PendingChange.created_at.desc())
        .limit(25)
    )
    pending_changes = pending_result.scalars().all()

    pending_total_result = await db.execute(
        select(func.count(PendingChange.id))
        .where(PendingChange.credential_id == cred.id)
        .where(PendingChange.status == "pending")
    )
    pending_total = pending_total_result.scalar() or 0

    pending_data = {
        "total": pending_total,
        "changes": [
            {
                "type": pc.change_type,
                "entity_type": pc.entity_type,
                "entity_name": pc.entity_name,
                "campaign_name": pc.campaign_name,
                "current_value": pc.current_value,
                "proposed_value": pc.proposed_value,
                "source": pc.source,
                "reasoning": pc.ai_reasoning[:200] if pc.ai_reasoning else None,
                "confidence": pc.confidence,
                "impact": pc.estimated_impact,
            }
            for pc in pending_changes
        ],
    }

    # ── 8. Bid rules and recent optimization runs ─────────────────────
    rules_result = await db.execute(
        select(BidRule)
        .where(BidRule.credential_id == cred.id)
        .order_by(BidRule.updated_at.desc())
        .limit(10)
    )
    bid_rules = rules_result.scalars().all()

    bid_rules_data = [
        {
            "name": r.name,
            "target_acos": r.target_acos,
            "min_bid": r.min_bid,
            "max_bid": r.max_bid,
            "bid_step": r.bid_step,
            "lookback_days": r.lookback_days,
            "min_clicks": r.min_clicks,
            "is_active": r.is_active,
            "total_runs": r.total_runs,
            "total_adjusted": r.total_targets_adjusted,
            "last_run": r.last_run_at.isoformat() if r.last_run_at else None,
        }
        for r in bid_rules
    ]

    # Latest optimization run
    latest_opt_result = await db.execute(
        select(OptimizationRun)
        .where(OptimizationRun.credential_id == cred.id)
        .order_by(OptimizationRun.started_at.desc())
        .limit(3)
    )
    recent_opt_runs = latest_opt_result.scalars().all()

    opt_history = [
        {
            "date": run.started_at.isoformat() if run.started_at else None,
            "status": run.status,
            "targets_analyzed": run.targets_analyzed,
            "targets_adjusted": run.targets_adjusted,
            "bid_increases": run.bid_increases,
            "bid_decreases": run.bid_decreases,
            "target_acos": run.target_acos,
            "dry_run": run.dry_run,
        }
        for run in recent_opt_runs
    ]

    # ── 9. Harvest configs and recent harvested keywords ──────────────
    harvest_result = await db.execute(
        select(HarvestConfig)
        .where(HarvestConfig.credential_id == cred.id)
        .order_by(HarvestConfig.updated_at.desc())
        .limit(5)
    )
    harvest_configs = harvest_result.scalars().all()

    harvest_data = []
    for hc in harvest_configs:
        # Get recently harvested keywords for this config
        hk_result = await db.execute(
            select(HarvestedKeyword)
            .join(HarvestRun, HarvestedKeyword.harvest_run_id == HarvestRun.id)
            .where(HarvestRun.config_id == hc.id)
            .order_by(HarvestedKeyword.created_at.desc())
            .limit(15)
        )
        recent_keywords = hk_result.scalars().all()

        harvest_data.append({
            "name": hc.name,
            "source_campaign": hc.source_campaign_name,
            "target_campaign": hc.target_campaign_name,
            "sales_threshold": hc.sales_threshold,
            "acos_threshold": hc.acos_threshold,
            "is_active": hc.is_active,
            "total_harvested": hc.total_keywords_harvested,
            "total_runs": hc.total_runs,
            "last_harvested": hc.last_harvested_at.isoformat() if hc.last_harvested_at else None,
            "recent_keywords": [
                {
                    "keyword": kw.keyword_text,
                    "match_type": kw.match_type,
                    "bid": kw.bid,
                    "source_clicks": kw.source_clicks,
                    "source_spend": kw.source_spend,
                    "source_sales": kw.source_sales,
                    "source_acos": kw.source_acos,
                }
                for kw in recent_keywords
            ],
        })

    # ── 10. Recent activity log ───────────────────────────────────────
    activity_result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.credential_id == cred.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(20)
    )
    recent_activity = activity_result.scalars().all()

    activity_data = [
        {
            "action": a.action,
            "category": a.category,
            "description": a.description,
            "status": a.status,
            "date": a.created_at.isoformat() if a.created_at else None,
        }
        for a in recent_activity
    ]

    # ── Build the full context ────────────────────────────────────────
    return {
        "account": {
            "name": active_profile.account_name if active_profile else cred.name,
            "region": cred.region,
            "marketplace": active_profile.marketplace if active_profile else None,
            "profile_id": cred.profile_id,
            "account_type": active_profile.account_type if active_profile else None,
        },
        "campaigns_summary": {
            "total": len(campaigns),
            "active": active,
            "paused": paused,
            "total_spend": total_spend,
            "total_sales": total_sales,
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_orders": total_orders,
            "avg_acos": avg_acos,
            "avg_ctr": round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0,
            "avg_cpc": round(total_spend / total_clicks, 2) if total_clicks > 0 else 0,
            "avg_cvr": round(total_orders / total_clicks * 100, 2) if total_clicks > 0 else 0,
        },
        "all_campaigns": all_campaigns,
        "ad_groups": {
            "total": len(all_ad_groups),
            "groups": all_ad_groups,
        },
        "targets_summary": {
            "total": target_count,
            "by_type": targets_by_type,
            "by_state": targets_by_state,
            "by_match_type": targets_by_match,
            "top_spenders": [_target_dict(t) for t in top_targets],
            "top_converters": [_target_dict(t) for t in top_converters],
            "non_converting": [_target_dict(t) for t in non_converting],
            "non_converting_total_count": len([
                t for t in targets if (t.clicks or 0) > 0 and (t.orders or 0) == 0
            ]),
            "high_acos": [_target_dict(t) for t in high_acos_targets],
        },
        "recent_audit": audit_data,
        "performance_trend": performance_trend,
        "pending_changes": pending_data,
        "bid_rules": bid_rules_data,
        "optimization_history": opt_history,
        "harvest_configs": harvest_data,
        "recent_activity": activity_data,
        "search_terms": await get_search_term_summary(db, cred.id, profile_id=cred.profile_id),
    }


async def _get_default_model_id(db: AsyncSession) -> Optional[str]:
    """Get the default LLM model ID from app settings. Fallback to first configured provider."""
    result = await db.execute(select(AppSettings).limit(1))
    row = result.scalar_one_or_none()
    if row and row.default_llm_id:
        return row.default_llm_id
    # Fallback: use first configured provider (env or Settings)
    openai_key, anthropic_key = await get_effective_api_keys(db)
    if openai_key:
        return f"openai:{settings.openai_model}"
    if anthropic_key:
        return "anthropic:claude-sonnet-4-20250514"
    return None


async def _has_ai_config(db: AsyncSession) -> bool:
    """Check if at least one AI provider is configured (env or Settings)."""
    openai_key, anthropic_key = await get_effective_api_keys(db)
    return bool(openai_key or anthropic_key)


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/chat")
async def ai_chat(payload: ChatRequest, db: AsyncSession = Depends(get_db)):
    """
    Chat with the AI assistant. Maintains conversation history.
    AI has full context of the user's Amazon Ads account.
    Uses default LLM from Settings unless model_id is provided.
    """
    if not await _has_ai_config(db):
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Add OPENAI_API_KEY and/or ANTHROPIC_API_KEY in Settings.",
        )

    cred = await _get_cred(db, payload.credential_id)
    openai_key, anthropic_key = await get_effective_api_keys(db)
    model_id = payload.model_id or await _get_default_model_id(db)
    ai = create_ai_service(model_id=model_id, openai_api_key=openai_key, anthropic_api_key=anthropic_key)

    # Get or create conversation
    conversation = None
    if payload.conversation_id:
        conv_result = await db.execute(
            select(AIConversation).where(AIConversation.id == parse_uuid(payload.conversation_id))
        )
        conversation = conv_result.scalar_one_or_none()

    if not conversation:
        conversation = AIConversation(
            credential_id=cred.id,
            title=payload.message[:100],
            messages=[],
        )
        db.add(conversation)
        await db.flush()

    # Build account context
    account_context = await _get_account_context(db, cred)
    conversation.context_data = account_context

    # Get AI response
    history = conversation.messages or []
    result = await ai.chat(
        user_message=payload.message,
        conversation_history=history,
        account_context=account_context,
    )

    actions = result.get("actions") or []
    inline_actions = [a for a in actions if a.get("scope") == "inline"]
    queue_actions = [a for a in actions if a.get("scope") == "queue"]
    queued_count = 0

    # Create PendingChanges for queue-scope actions; notify user
    if queue_actions:
        batch_id = str(uuid.uuid4())
        for act in queue_actions:
            tool = act.get("tool", "")
            args = act.get("arguments", {})
            if not tool or tool == "unknown":
                continue
            mcp_payload = {"tool": tool, "arguments": args}
            change = PendingChange(
                credential_id=cred.id,
                profile_id=cred.profile_id,
                change_type=act.get("change_type", "bid_update"),
                entity_type=act.get("entity_type", "target"),
                entity_id=act.get("entity_id"),
                entity_name=act.get("entity_name"),
                proposed_value=act.get("proposed_value"),
                change_detail=act,
                mcp_payload=mcp_payload,
                source="ai_chat",
                ai_reasoning=act.get("label"),
                batch_id=batch_id,
                batch_label=f"AI Chat — {len(queue_actions)} changes",
            )
            db.add(change)
            queued_count += 1

    # Update conversation (store actions for UI display)
    now = utcnow().isoformat()
    updated_messages = list(history)
    updated_messages.append({"role": "user", "content": payload.message, "timestamp": now})
    assistant_msg = {"role": "assistant", "content": result["message"], "timestamp": now}
    if actions:
        assistant_msg["actions"] = actions
    updated_messages.append(assistant_msg)
    conversation.messages = updated_messages
    conversation.updated_at = utcnow()

    db.add(ActivityLog(
        credential_id=cred.id,
        action="ai_chat",
        category="ai",
        description=f"AI chat: {payload.message[:80]}",
        entity_type="ai_conversation",
        entity_id=str(conversation.id),
        details={"inline_actions": len(inline_actions), "queued_actions": queued_count} if actions else None,
    ))

    await db.flush()

    response = {
        "conversation_id": str(conversation.id),
        "message": result["message"],
        "tokens_used": result.get("tokens_used", 0),
        "actions": inline_actions,
    }
    if queued_count > 0:
        response["queued_count"] = queued_count
        response["queued_message"] = f"{queued_count} change(s) sent to Approval Queue. Review and approve when ready."
    return response


@router.post("/apply-inline")
async def apply_inline(payload: ApplyInlineRequest, db: AsyncSession = Depends(get_db)):
    """
    Apply inline actions directly via MCP. Used when user approves small changes in chat.
    Supports: bid updates, budget changes, campaign/ad group renames, keyword add/update/delete.
    """
    if not payload.actions:
        raise HTTPException(status_code=400, detail="No actions to apply")

    cred = await _get_cred(db, payload.credential_id)
    client = await get_mcp_client_with_fresh_token(cred, db)

    applied = 0
    failed = 0
    results = []

    for act in payload.actions:
        tool = act.get("tool", "")
        arguments = act.get("arguments", {})
        label = act.get("label", tool)

        if not tool or tool == "unknown":
            results.append({"label": label, "status": "skipped", "error": "Unknown tool"})
            failed += 1
            continue

        try:
            mcp_result = await client.call_tool(tool, arguments)
            applied += 1
            results.append({"label": label, "status": "applied", "result": mcp_result})

            db.add(ActivityLog(
                credential_id=cred.id,
                action="ai_inline_applied",
                category="ai",
                description=f"Inline: {label}",
                entity_type="mcp_tool",
                entity_id=tool,
                details={"tool": tool, "label": label},
                status="success",
            ))
        except Exception as e:
            failed += 1
            results.append({"label": label, "status": "failed", "error": str(e)})
            db.add(ActivityLog(
                credential_id=cred.id,
                action="ai_inline_failed",
                category="ai",
                description=f"Inline failed: {label}",
                details={"tool": tool, "error": str(e)},
                status="error",
            ))

    await db.flush()

    return {
        "applied": applied,
        "failed": failed,
        "total": len(payload.actions),
        "results": results,
    }


@router.post("/insights")
async def generate_insights(payload: InsightsRequest, db: AsyncSession = Depends(get_db)):
    """
    Generate AI-powered insights from current campaign data.
    Analyzes performance, identifies waste, finds opportunities.
    """
    if not await _has_ai_config(db):
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Add OPENAI_API_KEY and/or ANTHROPIC_API_KEY in Settings.",
        )

    cred = await _get_cred(db, payload.credential_id)
    openai_key, anthropic_key = await get_effective_api_keys(db)
    model_id = await _get_default_model_id(db)
    ai = create_ai_service(model_id=model_id, openai_api_key=openai_key, anthropic_api_key=anthropic_key)

    # Gather campaign data
    campaigns_result = await db.execute(
        select(Campaign).where(Campaign.credential_id == cred.id)
    )
    campaigns = campaigns_result.scalars().all()

    targets_result = await db.execute(
        select(Target).where(Target.credential_id == cred.id)
    )
    targets = targets_result.scalars().all()

    # Build data for AI
    campaign_data = {
        "campaigns": [
            {
                "id": str(c.amazon_campaign_id),
                "name": c.campaign_name,
                "type": c.campaign_type,
                "targeting": c.targeting_type,
                "state": c.state,
                "budget": c.daily_budget,
                "spend": c.spend,
                "sales": c.sales,
                "impressions": c.impressions,
                "clicks": c.clicks,
                "orders": c.orders,
                "acos": c.acos,
                "roas": c.roas,
            }
            for c in campaigns
        ],
        "targets_summary": {
            "total": len(targets),
            "by_type": {},
            "by_state": {},
        },
        "top_spending_targets": sorted(
            [
                {
                    "id": str(t.amazon_target_id),
                    "keyword": t.expression_value,
                    "match_type": t.match_type,
                    "bid": t.bid,
                    "spend": t.spend,
                    "sales": t.sales,
                    "clicks": t.clicks,
                    "acos": t.acos,
                }
                for t in targets if t.spend and t.spend > 0
            ],
            key=lambda x: x["spend"],
            reverse=True,
        )[:50],
    }

    # Count target types and states
    for t in targets:
        tt = t.target_type or "unknown"
        ts = t.state or "unknown"
        campaign_data["targets_summary"]["by_type"][tt] = campaign_data["targets_summary"]["by_type"].get(tt, 0) + 1
        campaign_data["targets_summary"]["by_state"][ts] = campaign_data["targets_summary"]["by_state"].get(ts, 0) + 1

    account_context = await _get_account_context(db, cred)
    insights = await ai.generate_insights(campaign_data, account_context)

    db.add(ActivityLog(
        credential_id=cred.id,
        action="ai_insights_generated",
        category="ai",
        description=f"AI insights generated: {insights.get('summary', '')[:100]}",
        details={"health_score": insights.get("health_score"), "insights_count": len(insights.get("insights", []))},
    ))

    return insights


@router.post("/optimize")
async def ai_optimize(payload: OptimizeRequest, db: AsyncSession = Depends(get_db)):
    """
    AI-powered optimization recommendations.
    Analyzes all campaigns/targets and recommends specific changes.
    All recommendations go to the approval queue — nothing is applied directly.
    """
    if not await _has_ai_config(db):
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Add OPENAI_API_KEY and/or ANTHROPIC_API_KEY in Settings.",
        )

    cred = await _get_cred(db, payload.credential_id)
    openai_key, anthropic_key = await get_effective_api_keys(db)
    model_id = await _get_default_model_id(db)
    ai = create_ai_service(model_id=model_id, openai_api_key=openai_key, anthropic_api_key=anthropic_key)

    # Gather data
    campaigns_result = await db.execute(
        select(Campaign).where(Campaign.credential_id == cred.id)
    )
    campaigns = campaigns_result.scalars().all()

    targets_result = await db.execute(
        select(Target).where(Target.credential_id == cred.id)
    )
    targets = targets_result.scalars().all()

    campaign_dicts = [
        {
            "id": c.amazon_campaign_id,
            "name": c.campaign_name,
            "type": c.campaign_type,
            "targeting": c.targeting_type,
            "state": c.state,
            "budget": c.daily_budget,
            "spend": c.spend,
            "sales": c.sales,
            "clicks": c.clicks,
            "acos": c.acos,
        }
        for c in campaigns
    ]

    target_dicts = [
        {
            "id": t.amazon_target_id,
            "campaign_id": t.amazon_campaign_id,
            "keyword": t.expression_value,
            "match_type": t.match_type,
            "type": t.target_type,
            "state": t.state,
            "bid": t.bid,
            "spend": t.spend,
            "sales": t.sales,
            "clicks": t.clicks,
            "orders": t.orders,
            "acos": t.acos,
        }
        for t in targets
    ]

    recommendations = await ai.recommend_optimizations(
        campaigns=campaign_dicts,
        targets=target_dicts,
        target_acos=payload.target_acos,
    )

    # Create pending changes from recommendations
    batch_id = str(uuid.uuid4())
    changes_created = 0
    for rec in recommendations.get("recommended_changes", []):
        # Build MCP payload based on change type
        mcp_payload = _build_mcp_payload(rec)

        pending = PendingChange(
            credential_id=cred.id,
            profile_id=cred.profile_id,
            change_type=rec.get("change_type", "bid_update"),
            entity_type=rec.get("entity_type", "target"),
            entity_id=rec.get("entity_id"),
            entity_name=rec.get("entity_name"),
            campaign_id=rec.get("campaign_id"),
            campaign_name=rec.get("campaign_name"),
            current_value=rec.get("current_value"),
            proposed_value=rec.get("proposed_value"),
            change_detail=rec,
            mcp_payload=mcp_payload,
            source="ai_optimizer",
            ai_reasoning=rec.get("reasoning"),
            confidence=rec.get("confidence"),
            estimated_impact=rec.get("estimated_impact"),
            batch_id=batch_id,
            batch_label=f"AI Optimization — Target ACOS {payload.target_acos}%",
        )
        db.add(pending)
        changes_created += 1

    db.add(ActivityLog(
        credential_id=cred.id,
        action="ai_optimization_recommended",
        category="ai",
        description=f"AI recommended {changes_created} changes (target ACOS: {payload.target_acos}%)",
        details={
            "batch_id": batch_id,
            "changes_count": changes_created,
            "summary": recommendations.get("analysis_summary"),
        },
    ))

    await db.flush()

    return {
        "batch_id": batch_id,
        "analysis_summary": recommendations.get("analysis_summary"),
        "changes_created": changes_created,
        "total_estimated_savings": recommendations.get("total_estimated_savings"),
        "total_estimated_revenue_gain": recommendations.get("total_estimated_revenue_gain"),
        "recommendations": recommendations.get("recommended_changes", []),
    }


@router.post("/build-campaign")
async def ai_build_campaign(payload: CampaignBuildRequest, db: AsyncSession = Depends(get_db)):
    """
    AI-assisted campaign building from a product brief.
    Returns a complete campaign plan ready for review.
    """
    if not await _has_ai_config(db):
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Add OPENAI_API_KEY and/or ANTHROPIC_API_KEY in Settings.",
        )

    cred = await _get_cred(db, payload.credential_id)
    openai_key, anthropic_key = await get_effective_api_keys(db)
    model_id = await _get_default_model_id(db)
    ai = create_ai_service(model_id=model_id, openai_api_key=openai_key, anthropic_api_key=anthropic_key)

    brief = {
        "product_name": payload.product_name,
        "product_asin": payload.product_asin,
        "product_category": payload.product_category,
        "daily_budget": payload.daily_budget,
        "target_acos": payload.target_acos,
        "campaign_type": payload.campaign_type,
        "targeting_type": payload.targeting_type,
        "goals": payload.goals,
        "seed_keywords": payload.keywords,
    }

    plan = await ai.build_campaign(brief)

    db.add(ActivityLog(
        credential_id=cred.id,
        action="ai_campaign_built",
        category="ai",
        description=f"AI campaign plan for: {payload.product_name}",
        details={"plan_summary": plan.get("campaign_plan", {}).get("name")},
    ))

    return plan


@router.post("/publish-campaign")
async def ai_publish_campaign(payload: CampaignPublishRequest, db: AsyncSession = Depends(get_db)):
    """
    Publish an AI-generated campaign plan to the approval queue.
    Creates a single PendingChange that, when approved and applied, will create
    the full campaign (campaign → ad groups → ads → targets) via MCP.
    """
    if not payload.product_asin or not payload.product_asin.strip():
        raise HTTPException(status_code=400, detail="product_asin is required for Sponsored Products campaigns")

    cred = await _get_cred(db, payload.credential_id)

    # Convert AI plan format to CampaignCreationService format
    plan = payload.plan
    campaign_plan = plan.get("campaign_plan", plan.get("campaign", {}))
    ad_groups_plan = plan.get("ad_groups", [])

    exec_plan = {
        "campaign": {
            "name": campaign_plan.get("name", "AI Campaign"),
            "adProduct": campaign_plan.get("type", "SPONSORED_PRODUCTS"),
            "targetingType": campaign_plan.get("targeting_type", "manual"),
            "state": "enabled",
            "dailyBudget": float(campaign_plan.get("daily_budget", 50)),
            "asin": payload.product_asin,
        },
        "ad_groups": [
            {
                "name": ag.get("name", "Ad Group"),
                "defaultBid": ag.get("default_bid", 0.5),
                "keywords": [
                    {
                        "text": kw.get("text", kw.get("keyword")),
                        "match_type": kw.get("match_type", "broad"),
                        "suggested_bid": kw.get("suggested_bid", kw.get("bid", 0.5)),
                    }
                    for kw in ag.get("keywords", [])
                    if kw.get("text") or kw.get("keyword")
                ],
            }
            for ag in ad_groups_plan
        ],
        "ad": {"asin": payload.product_asin},
    }

    # Ensure at least one ad group
    if not exec_plan["ad_groups"]:
        exec_plan["ad_groups"] = [{"name": "Default Ad Group", "defaultBid": 0.5, "keywords": []}]

    batch_id = str(uuid.uuid4())
    change = PendingChange(
        credential_id=cred.id,
        profile_id=cred.profile_id,
        change_type="campaign_bundle",
        entity_type="campaign",
        entity_name=exec_plan["campaign"]["name"],
        proposed_value=f"Campaign + {len(exec_plan['ad_groups'])} ad groups + targets",
        change_detail=exec_plan,
        mcp_payload={
            "tool": "_ai_campaign_create",
            "arguments": {"plan": exec_plan},
        },
        source="ai_assistant",
        ai_reasoning=f"AI-generated campaign for product ASIN {payload.product_asin}",
        batch_id=batch_id,
        batch_label=f"AI Campaign: {exec_plan['campaign']['name']}",
    )
    db.add(change)

    db.add(ActivityLog(
        credential_id=cred.id,
        action="ai_campaign_published",
        category="ai",
        description=f"Campaign '{exec_plan['campaign']['name']}' sent to approval queue",
        details={"change_id": str(change.id), "batch_id": batch_id},
    ))

    await db.flush()

    return {
        "status": "pending_approval",
        "change_id": str(change.id),
        "batch_id": batch_id,
        "message": "Campaign sent to approval queue. Review and approve, then push to Amazon Ads.",
    }


@router.get("/conversations")
async def list_conversations(
    credential_id: Optional[str] = Query(None),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
):
    """List AI conversations."""
    query = select(AIConversation).order_by(AIConversation.updated_at.desc()).limit(limit)
    if credential_id:
        query = query.where(AIConversation.credential_id == parse_uuid(credential_id))

    result = await db.execute(query)
    convos = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "credential_id": str(c.credential_id),
            "title": c.title,
            "message_count": len(c.messages) if c.messages else 0,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }
        for c in convos
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific conversation with full message history."""
    result = await db.execute(
        select(AIConversation).where(AIConversation.id == parse_uuid(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "id": str(conv.id),
        "credential_id": str(conv.credential_id),
        "title": conv.title,
        "messages": conv.messages or [],
        "context_data": conv.context_data,
        "is_active": conv.is_active,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat(),
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    """Delete an AI conversation."""
    result = await db.execute(
        select(AIConversation).where(AIConversation.id == parse_uuid(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    return {"status": "deleted"}


# ── Helper ────────────────────────────────────────────────────────────

def _build_mcp_payload(recommendation: dict) -> dict:
    """Build an MCP-ready payload from an AI recommendation."""
    change_type = recommendation.get("change_type", "bid_update")
    entity_id = recommendation.get("entity_id", "")
    proposed = recommendation.get("proposed_value", "")

    if change_type == "bid_update":
        try:
            bid_val = float(proposed.replace("$", "").strip())
        except (ValueError, AttributeError):
            bid_val = 0
        return {
            "tool": "campaign_management-update_target_bid",
            "arguments": {
                "body": {
                    "targets": [{"targetId": entity_id, "bid": bid_val}]
                }
            },
        }
    elif change_type == "budget_update":
        try:
            budget_val = float(proposed.replace("$", "").strip())
        except (ValueError, AttributeError):
            budget_val = 0
        return {
            "tool": "campaign_management-update_campaign_budget",
            "arguments": {
                "body": {
                    "campaigns": [{"campaignId": entity_id, "dailyBudget": budget_val}]
                }
            },
        }
    elif change_type == "campaign_state":
        return {
            "tool": "campaign_management-update_campaign_state",
            "arguments": {
                "body": {
                    "campaigns": [{"campaignId": entity_id, "state": proposed}]
                }
            },
        }
    elif change_type == "target_state":
        return {
            "tool": "campaign_management-update_target",
            "arguments": {
                "body": {
                    "targets": [{"targetId": entity_id, "state": proposed}]
                }
            },
        }
    else:
        return {"tool": "unknown", "arguments": recommendation}
