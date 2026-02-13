"""
Accounts Router — Discover and cache Amazon Ads accounts, campaigns, ad groups, targets via MCP.
All discovered data is persisted to PostgreSQL.
"""

import logging
import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.database import get_db
from app.models import (
    Credential, Account, Campaign, AdGroup, Target, Ad, ActivityLog,
)
from app.mcp_client import create_mcp_client
from app.services.token_service import get_mcp_client_with_fresh_token
from app.utils import parse_uuid, safe_error_detail, utcnow

router = APIRouter()


async def _get_credential(db: AsyncSession, cred_id: Optional[str] = None) -> Credential:
    """Get credential by ID or the default one."""
    if cred_id:
        result = await db.execute(select(Credential).where(Credential.id == parse_uuid(cred_id, "credential_id")))
    else:
        result = await db.execute(select(Credential).where(Credential.is_default == True))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="No credential found. Please add your API credentials first.")
    return cred


async def _make_client(cred: Credential, db: AsyncSession):
    return await get_mcp_client_with_fresh_token(cred, db)


def _extract_list(data, keys=None) -> list:
    """Extract list from various MCP response formats."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        search_keys = keys or [
            "advertiserAccounts", "accounts",
            "campaigns", "adGroups", "targets",
            "result", "results", "items",
        ]
        for key in search_keys:
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    return val
    return []


@router.get("/discover")
async def discover_accounts(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Discover all advertiser accounts and persist them to DB.
    MCP returns advertiserAccounts, each with alternateIds per marketplace.
    We flatten these into one Account row per marketplace profile.
    """
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        raw_accounts = await client.query_accounts()
        account_list = _extract_list(raw_accounts, [
            "advertiserAccounts", "accounts", "result", "results", "items",
        ])

        logger.info(f"Discovered {len(account_list)} advertiser accounts from MCP")

        # Persist each account to DB (upsert logic)
        stored_accounts = []
        for acct_data in account_list:
            # Global advertiser account ID
            global_id = (
                acct_data.get("advertiserAccountId")
                or acct_data.get("accountId")
                or acct_data.get("id")
                or str(uuid_mod.uuid4())
            )
            display_name = (
                acct_data.get("displayName")
                or acct_data.get("name")
                or acct_data.get("accountName")
                or "Unnamed Account"
            )
            is_global = acct_data.get("isGlobalAccount", False)

            # If the account has marketplace-specific profiles (alternateIds), create one row per marketplace
            alternate_ids = acct_data.get("alternateIds", [])

            if alternate_ids:
                for alt in alternate_ids:
                    country = alt.get("countryCode", "")
                    profile_id = alt.get("profileId", "")
                    entity_id = alt.get("entityId", "")
                    # Use profileId as the unique identifier per marketplace
                    unique_id = profile_id or entity_id or f"{global_id}_{country}"

                    existing = await db.execute(
                        select(Account).where(
                            Account.credential_id == cred.id,
                            Account.amazon_account_id == str(unique_id),
                        )
                    )
                    account = existing.scalar_one_or_none()

                    acct_name = f"{display_name} ({country})" if country else display_name
                    if account:
                        account.account_name = acct_name
                        account.account_type = "global" if is_global else "standalone"
                        account.marketplace = country
                        account.profile_id = profile_id
                        account.raw_data = {**acct_data, "marketplace_alt": alt}
                        account.updated_at = utcnow()
                    else:
                        account = Account(
                            credential_id=cred.id,
                            amazon_account_id=str(unique_id),
                            account_name=acct_name,
                            account_type="global" if is_global else "standalone",
                            marketplace=country,
                            profile_id=profile_id,
                            account_status="active",
                            raw_data={**acct_data, "marketplace_alt": alt},
                        )
                        db.add(account)

                    stored_accounts.append({
                        "amazon_account_id": str(unique_id),
                        "account_name": acct_name,
                        "account_type": "global" if is_global else "standalone",
                        "marketplace": country,
                        "profile_id": profile_id,
                        "status": "active",
                        "parent_account": display_name,
                    })
            else:
                # No marketplace profiles — store as a single global entry
                existing = await db.execute(
                    select(Account).where(
                        Account.credential_id == cred.id,
                        Account.amazon_account_id == str(global_id),
                    )
                )
                account = existing.scalar_one_or_none()

                if account:
                    account.account_name = display_name
                    account.account_type = "global" if is_global else "standalone"
                    account.raw_data = acct_data
                    account.updated_at = utcnow()
                else:
                    account = Account(
                        credential_id=cred.id,
                        amazon_account_id=str(global_id),
                        account_name=display_name,
                        account_type="global" if is_global else "standalone",
                        account_status="active",
                        raw_data=acct_data,
                    )
                    db.add(account)

                stored_accounts.append({
                    "amazon_account_id": str(global_id),
                    "account_name": display_name,
                    "account_type": "global" if is_global else "standalone",
                    "marketplace": None,
                    "profile_id": None,
                    "status": "active",
                })

        db.add(ActivityLog(
            credential_id=cred.id,
            action="accounts_discovered",
            category="accounts",
            description=f"Discovered {len(stored_accounts)} profiles across {len(account_list)} advertiser accounts",
            entity_type="account",
            details={"account_count": len(account_list), "profile_count": len(stored_accounts)},
        ))

        await db.flush()

        return {
            "accounts": stored_accounts,
            "credential_id": str(cred.id),
            "count": len(stored_accounts),
        }
    except Exception as e:
        logger.error(f"Account discovery failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to communicate with Amazon Ads API."))


@router.get("/stored")
async def list_stored_accounts(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List previously discovered accounts from the database."""
    query = select(Account).order_by(Account.discovered_at.desc())
    if credential_id:
        query = query.where(Account.credential_id == parse_uuid(credential_id, "credential_id"))

    result = await db.execute(query)
    accounts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "credential_id": str(a.credential_id),
            "amazon_account_id": a.amazon_account_id,
            "account_name": a.account_name,
            "account_type": a.account_type,
            "marketplace": a.marketplace,
            "profile_id": a.profile_id,
            "account_status": a.account_status,
            "discovered_at": a.discovered_at.isoformat(),
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in accounts
    ]


@router.post("/set-active/{account_id}")
async def set_active_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Set the active discovered profile. Updates the parent credential's
    profile_id so all subsequent MCP calls are scoped to this account.
    """
    result = await db.execute(
        select(Account).where(Account.id == parse_uuid(account_id, "account_id"))
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Update the parent credential's profile_id
    cred_result = await db.execute(
        select(Credential).where(Credential.id == account.credential_id)
    )
    cred = cred_result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    cred.profile_id = account.profile_id
    cred.updated_at = utcnow()

    logger.info(
        "set-active: account_id=%s profile_id=%s credential_id=%s account_name=%s",
        account_id, account.profile_id, str(cred.id), account.account_name or "(unnamed)",
    )

    db.add(ActivityLog(
        credential_id=cred.id,
        action="active_account_changed",
        category="settings",
        description=f"Switched to {account.account_name}" + (f" ({account.marketplace})" if account.marketplace else ""),
        entity_type="account",
        entity_id=str(account.id),
    ))

    await db.flush()
    await db.refresh(cred)

    return {
        "status": "ok",
        "account_id": str(account.id),
        "account_name": account.account_name,
        "profile_id": account.profile_id,
        "marketplace": account.marketplace,
        "credential_id": str(cred.id),
    }


@router.get("/campaigns")
async def list_campaigns(
    credential_id: Optional[str] = Query(None),
    sync: bool = Query(True, description="If true, fetches fresh data from MCP and caches it"),
    db: AsyncSession = Depends(get_db),
):
    """List campaigns — syncs from MCP and caches in DB."""
    cred = await _get_credential(db, credential_id)

    if sync:
        client = await _make_client(cred, db)
        try:
            # Fetch all ad product types (SP, SB, SD) for complete coverage
            raw_campaigns = await client.query_campaigns()
            campaign_list = _extract_list(raw_campaigns, ["campaigns", "result", "results", "items"])

            # Persist each campaign to DB
            for camp_data in campaign_list:
                amazon_id = (
                    camp_data.get("campaignId")
                    or camp_data.get("id")
                    or str(uuid_mod.uuid4())
                )

                profile_cond = (
                    Campaign.profile_id == cred.profile_id
                    if cred.profile_id is not None
                    else Campaign.profile_id.is_(None)
                )
                existing = await db.execute(
                    select(Campaign).where(
                        Campaign.credential_id == cred.id,
                        Campaign.amazon_campaign_id == str(amazon_id),
                        profile_cond,
                    )
                )
                campaign = existing.scalar_one_or_none()

                camp_name = camp_data.get("name") or camp_data.get("campaignName")
                camp_type = camp_data.get("adProduct") or camp_data.get("campaignType") or camp_data.get("type")
                targeting = camp_data.get("targetingType") or camp_data.get("targeting")
                # MCP uses autoCreationSettings to indicate auto vs manual
                if not targeting and camp_data.get("autoCreationSettings"):
                    auto_targets = camp_data["autoCreationSettings"].get("autoCreateTargets", False)
                    targeting = "auto" if auto_targets else "manual"
                state = camp_data.get("state") or camp_data.get("status")
                # Extract daily budget from nested budgets array
                budget = camp_data.get("dailyBudget") or camp_data.get("budget")
                if not budget and camp_data.get("budgets"):
                    for b in camp_data["budgets"]:
                        if b.get("recurrenceTimePeriod") == "DAILY":
                            mv = b.get("budgetValue", {}).get("monetaryBudgetValue", {}).get("monetaryBudget", {})
                            budget = mv.get("value")
                            break

                if campaign:
                    campaign.profile_id = cred.profile_id
                    campaign.campaign_name = camp_name or campaign.campaign_name
                    campaign.campaign_type = camp_type or campaign.campaign_type
                    campaign.targeting_type = targeting or campaign.targeting_type
                    campaign.state = state or campaign.state
                    campaign.daily_budget = float(budget) if budget else campaign.daily_budget
                    campaign.start_date = camp_data.get("startDate") or camp_data.get("startDateTime") or campaign.start_date
                    campaign.end_date = camp_data.get("endDate") or camp_data.get("endDateTime") or campaign.end_date
                    campaign.raw_data = camp_data
                    campaign.synced_at = utcnow()
                else:
                    campaign = Campaign(
                        credential_id=cred.id,
                        profile_id=cred.profile_id,
                        amazon_campaign_id=str(amazon_id),
                        campaign_name=camp_name,
                        campaign_type=camp_type,
                        targeting_type=targeting,
                        state=state,
                        daily_budget=float(budget) if budget else None,
                        start_date=camp_data.get("startDate") or camp_data.get("startDateTime"),
                        end_date=camp_data.get("endDate") or camp_data.get("endDateTime"),
                        raw_data=camp_data,
                    )
                    db.add(campaign)

            db.add(ActivityLog(
                credential_id=cred.id,
                action="campaigns_synced",
                category="accounts",
                description=f"Synced {len(campaign_list)} campaigns from MCP",
                entity_type="campaign",
                details={"count": len(campaign_list)},
            ))

            await db.flush()
        except Exception as e:
            raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to communicate with Amazon Ads API."))

    # Return from DB — filter by profile when set
    camp_query = select(Campaign).where(Campaign.credential_id == cred.id)
    if cred.profile_id is not None:
        camp_query = camp_query.where(Campaign.profile_id == cred.profile_id)
    else:
        camp_query = camp_query.where(Campaign.profile_id.is_(None))
    camp_query = camp_query.order_by(Campaign.campaign_name)
    result = await db.execute(camp_query)
    campaigns = result.scalars().all()
    return {
        "campaigns": [
            {
                "id": str(c.id),
                "amazon_campaign_id": c.amazon_campaign_id,
                "campaign_name": c.campaign_name,
                "campaign_type": c.campaign_type,
                "targeting_type": c.targeting_type,
                "state": c.state,
                "daily_budget": c.daily_budget,
                "start_date": c.start_date,
                "end_date": c.end_date,
                "spend": c.spend,
                "sales": c.sales,
                "acos": c.acos,
                "roas": c.roas,
                "synced_at": c.synced_at.isoformat() if c.synced_at else None,
            }
            for c in campaigns
        ],
        "credential_id": str(cred.id),
        "count": len(campaigns),
    }


@router.get("/ad-groups")
async def list_ad_groups(
    credential_id: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None, description="Amazon campaign ID to filter by"),
    db: AsyncSession = Depends(get_db),
):
    """Sync and return ad groups from MCP, cached in DB."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)

    try:
        raw_groups = await client.query_ad_groups(campaign_id=campaign_id)
        group_list = _extract_list(raw_groups, ["adGroups", "result", "results", "items"])

        for grp_data in group_list:
            amazon_id = grp_data.get("adGroupId") or grp_data.get("id") or str(uuid_mod.uuid4())
            amz_campaign_id = grp_data.get("campaignId")

            # Resolve local campaign FK if possible
            local_campaign = None
            if amz_campaign_id:
                camp_result = await db.execute(
                    select(Campaign).where(
                        Campaign.credential_id == cred.id,
                        Campaign.amazon_campaign_id == str(amz_campaign_id),
                    )
                )
                local_campaign = camp_result.scalar_one_or_none()

            existing = await db.execute(
                select(AdGroup).where(
                    AdGroup.credential_id == cred.id,
                    AdGroup.amazon_ad_group_id == str(amazon_id),
                )
            )
            ad_group = existing.scalar_one_or_none()

            # Extract bid value from nested MCP format
            bid_val = grp_data.get("defaultBid") or grp_data.get("bid")
            if isinstance(bid_val, dict):
                bid_val = bid_val.get("value") or bid_val.get("monetaryBid", {}).get("value")

            if ad_group:
                ad_group.ad_group_name = grp_data.get("name") or grp_data.get("adGroupName") or ad_group.ad_group_name
                ad_group.state = grp_data.get("state") or ad_group.state
                ad_group.default_bid = float(bid_val) if bid_val else ad_group.default_bid
                ad_group.amazon_campaign_id = str(amz_campaign_id) if amz_campaign_id else ad_group.amazon_campaign_id
                ad_group.campaign_id = local_campaign.id if local_campaign else ad_group.campaign_id
                ad_group.raw_data = grp_data
                ad_group.synced_at = utcnow()
            else:
                ad_group = AdGroup(
                    credential_id=cred.id,
                    campaign_id=local_campaign.id if local_campaign else None,
                    amazon_ad_group_id=str(amazon_id),
                    amazon_campaign_id=str(amz_campaign_id) if amz_campaign_id else None,
                    ad_group_name=grp_data.get("name") or grp_data.get("adGroupName"),
                    state=grp_data.get("state"),
                    default_bid=float(bid_val) if bid_val else None,
                    raw_data=grp_data,
                )
                db.add(ad_group)

        await db.flush()

        # Return from DB
        query = select(AdGroup).where(AdGroup.credential_id == cred.id)
        if campaign_id:
            query = query.where(AdGroup.amazon_campaign_id == campaign_id)

        result = await db.execute(query.order_by(AdGroup.ad_group_name))
        ad_groups = result.scalars().all()

        return {
            "ad_groups": [
                {
                    "id": str(g.id),
                    "amazon_ad_group_id": g.amazon_ad_group_id,
                    "amazon_campaign_id": g.amazon_campaign_id,
                    "ad_group_name": g.ad_group_name,
                    "state": g.state,
                    "default_bid": g.default_bid,
                    "synced_at": g.synced_at.isoformat() if g.synced_at else None,
                }
                for g in ad_groups
            ],
            "credential_id": str(cred.id),
            "count": len(ad_groups),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to communicate with Amazon Ads API."))


@router.get("/targets")
async def list_targets(
    credential_id: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None, description="Amazon campaign ID to filter by"),
    db: AsyncSession = Depends(get_db),
):
    """Sync and return targets/keywords from MCP, cached in DB."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)

    try:
        raw_targets = await client.query_targets(campaign_id=campaign_id)
        target_list = _extract_list(raw_targets, ["targets", "result", "results", "items"])

        for tgt_data in target_list:
            amazon_id = tgt_data.get("targetId") or tgt_data.get("id") or str(uuid_mod.uuid4())
            amz_ag_id = tgt_data.get("adGroupId")
            amz_camp_id = tgt_data.get("campaignId")

            # Resolve local ad group FK
            local_ag = None
            if amz_ag_id:
                ag_result = await db.execute(
                    select(AdGroup).where(
                        AdGroup.credential_id == cred.id,
                        AdGroup.amazon_ad_group_id == str(amz_ag_id),
                    )
                )
                local_ag = ag_result.scalar_one_or_none()

            existing = await db.execute(
                select(Target).where(
                    Target.credential_id == cred.id,
                    Target.amazon_target_id == str(amazon_id),
                )
            )
            target = existing.scalar_one_or_none()

            # Extract bid from nested MCP format (bid can be dict or scalar)
            bid_val = tgt_data.get("bid") or tgt_data.get("defaultBid")
            if isinstance(bid_val, dict):
                bid_val = bid_val.get("value") or bid_val.get("monetaryBid", {}).get("value")

            # Extract expression/keyword — may come from targetDetails or flat fields
            target_details = tgt_data.get("targetDetails", {})
            expression = (
                tgt_data.get("expression")
                or tgt_data.get("keyword")
                or target_details.get("expression")
                or target_details.get("keyword")
            )
            if isinstance(expression, list) and expression:
                expression = str(expression[0]) if len(expression) == 1 else str(expression)

            # Target type may come from targetType or targetDetails
            tgt_type = (
                tgt_data.get("targetType")
                or tgt_data.get("type")
                or target_details.get("targetType")
            )

            # Match type may come from targetDetails
            match_type = (
                tgt_data.get("matchType")
                or target_details.get("matchType")
            )

            # Note: MCP query responses do NOT include performance metrics
            # (clicks, impressions, spend, sales). Those come from reports.
            # We only update performance fields if they are actually present
            # in the response (i.e., from a cached/enriched source).

            if target:
                target.target_type = tgt_type or target.target_type
                target.expression_type = tgt_data.get("expressionType") or target_details.get("expressionType") or target.expression_type
                target.expression_value = str(expression) if expression else target.expression_value
                target.match_type = match_type or target.match_type
                target.state = tgt_data.get("state") or target.state
                target.bid = float(bid_val) if bid_val else target.bid
                # Only update perf metrics if present in response (not from MCP query)
                if tgt_data.get("clicks") is not None:
                    target.clicks = tgt_data["clicks"]
                if tgt_data.get("impressions") is not None:
                    target.impressions = tgt_data["impressions"]
                if tgt_data.get("spend") is not None or tgt_data.get("cost") is not None:
                    target.spend = tgt_data.get("spend") or tgt_data.get("cost")
                if tgt_data.get("sales") is not None or tgt_data.get("attributedSales") is not None:
                    target.sales = tgt_data.get("sales") or tgt_data.get("attributedSales")
                target.amazon_campaign_id = str(amz_camp_id) if amz_camp_id else target.amazon_campaign_id
                target.amazon_ad_group_id = str(amz_ag_id) if amz_ag_id else target.amazon_ad_group_id
                target.ad_group_id = local_ag.id if local_ag else target.ad_group_id
                target.raw_data = tgt_data
                target.synced_at = utcnow()
            else:
                target = Target(
                    credential_id=cred.id,
                    ad_group_id=local_ag.id if local_ag else None,
                    amazon_target_id=str(amazon_id),
                    amazon_ad_group_id=str(amz_ag_id) if amz_ag_id else None,
                    amazon_campaign_id=str(amz_camp_id) if amz_camp_id else None,
                    target_type=tgt_type,
                    expression_type=tgt_data.get("expressionType") or target_details.get("expressionType"),
                    expression_value=str(expression) if expression else None,
                    match_type=match_type,
                    state=tgt_data.get("state"),
                    bid=float(bid_val) if bid_val else None,
                    # Don't set perf metrics from query responses — they aren't there
                    raw_data=tgt_data,
                )
                db.add(target)

        await db.flush()

        # Return from DB
        query = select(Target).where(Target.credential_id == cred.id)
        if campaign_id:
            query = query.where(Target.amazon_campaign_id == campaign_id)

        result = await db.execute(query.order_by(Target.expression_value))
        targets = result.scalars().all()

        return {
            "targets": [
                {
                    "id": str(t.id),
                    "amazon_target_id": t.amazon_target_id,
                    "amazon_campaign_id": t.amazon_campaign_id,
                    "amazon_ad_group_id": t.amazon_ad_group_id,
                    "target_type": t.target_type,
                    "expression_value": t.expression_value,
                    "match_type": t.match_type,
                    "state": t.state,
                    "bid": t.bid,
                    "clicks": t.clicks,
                    "impressions": t.impressions,
                    "spend": t.spend,
                    "sales": t.sales,
                    "acos": t.acos,
                    "synced_at": t.synced_at.isoformat() if t.synced_at else None,
                }
                for t in targets
            ],
            "credential_id": str(cred.id),
            "count": len(targets),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to communicate with Amazon Ads API."))


@router.get("/products")
async def list_products(
    credential_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    List products (ASINs) from existing ads in the account.
    Used for campaign creation to select relevant products.
    """
    cred = await _get_credential(db, credential_id)
    result = await db.execute(
        select(Ad.asin, Ad.ad_name, Ad.amazon_campaign_id)
        .where(Ad.credential_id == cred.id, Ad.asin.isnot(None), Ad.asin != "")
        .distinct()
        .limit(limit)
    )
    rows = result.all()
    # Group by ASIN with campaign context
    by_asin = {}
    for asin, ad_name, camp_id in rows:
        if asin not in by_asin:
            by_asin[asin] = {"asin": asin, "ad_name": ad_name, "campaigns": []}
        if camp_id and camp_id not in by_asin[asin]["campaigns"]:
            by_asin[asin]["campaigns"].append(camp_id)
    return {
        "products": list(by_asin.values()),
        "count": len(by_asin),
    }


@router.get("/links")
async def list_account_links(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Query account links. Manager Account users see linked advertiser accounts;
    advertiser account users see linked Manager Accounts.
    """
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.query_account_links()
        links = _extract_list(result, ["accountLinks", "links", "result", "results", "items"])
        return {"links": links, "count": len(links)}
    except Exception as e:
        logger.error(f"Account links failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to fetch account links."))


class AccountSettingsUpdate(BaseModel):
    display_name: Optional[str] = None
    currency_code: Optional[str] = None
    timezone: Optional[str] = None


@router.put("/settings")
async def update_account_settings(
    payload: AccountSettingsUpdate,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Update the active account's display name, currency, or timezone.
    Requires an active profile to be set.
    """
    cred = await _get_credential(db, credential_id)
    if not cred.profile_id:
        raise HTTPException(
            status_code=400,
            detail="No active account selected. Discover accounts and set an active profile first.",
        )
    result = await db.execute(
        select(Account).where(
            Account.credential_id == cred.id,
            Account.profile_id == cred.profile_id,
        )
    )
    active_account = result.scalar_one_or_none()
    if not active_account or not active_account.raw_data:
        raise HTTPException(status_code=404, detail="Active account not found or missing advertiser ID.")
    adv_id = active_account.raw_data.get("advertiserAccountId")
    if not adv_id:
        raise HTTPException(status_code=400, detail="Advertiser account ID not available.")
    client = await _make_client(cred, db)
    base = {"advertiserAccountId": adv_id}
    try:
        if payload.display_name is not None:
            await client.update_account_name([{**base, "displayName": payload.display_name}])
            active_account.account_name = payload.display_name
        if payload.currency_code is not None:
            await client.update_account_currency([{**base, "currencyCode": payload.currency_code}])
        if payload.timezone is not None:
            await client.update_account_timezone([{**base, "timezone": payload.timezone}])
        active_account.updated_at = utcnow()
        db.add(active_account)
        await db.flush()
        return {"status": "updated", "display_name": payload.display_name, "currency_code": payload.currency_code, "timezone": payload.timezone}
    except Exception as e:
        logger.error(f"Account settings update failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to update account settings."))


# ── Terms Token ────────────────────────────────────────────────────────


class CreateTermsTokenRequest(BaseModel):
    terms_type: str = "ADSP"


@router.post("/terms-token")
async def create_terms_token(
    payload: CreateTermsTokenRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a new terms token for advertising terms acceptance (e.g. ADSP)."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.create_terms_token(terms_type=payload.terms_type)
        return result
    except Exception as e:
        logger.error(f"Create terms token failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to create terms token."))


class GetTermsTokenRequest(BaseModel):
    terms_token: str


@router.post("/terms-token/status")
async def get_terms_token_status(
    payload: GetTermsTokenRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get the status of a terms token."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.get_terms_token(terms_token=payload.terms_token)
        return result
    except Exception as e:
        logger.error(f"Get terms token failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to get terms token status."))


# ── Update Advertiser Account ──────────────────────────────────────────


class UpdateAdvertiserRequest(BaseModel):
    advertiser_account_id: str
    display_name: Optional[str] = None
    currency_code: Optional[str] = None
    timezone: Optional[str] = None


@router.put("/advertiser")
async def update_advertiser_account(
    payload: UpdateAdvertiserRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Update advertiser account (display name, currency, timezone)."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    acct = {"advertiserAccountId": payload.advertiser_account_id}
    if payload.display_name is not None:
        acct["displayName"] = payload.display_name
    if payload.currency_code is not None:
        acct["currencyCode"] = payload.currency_code
    if payload.timezone is not None:
        acct["timezone"] = payload.timezone
    try:
        result = await client.update_advertiser_account([acct])
        return {"status": "updated", "result": result}
    except Exception as e:
        logger.error(f"Update advertiser failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to update advertiser account."))


# ── User Invitations ───────────────────────────────────────────────────


@router.get("/invitations")
async def list_user_invitations(
    credential_id: Optional[str] = Query(None),
    max_results: int = Query(50, ge=1, le=100),
    next_token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List user invitations for the advertising account."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.list_user_invitations(max_results=max_results, next_token=next_token)
        invitations = _extract_list(result, ["userInvitations", "invitations", "result", "results", "items"])
        return {"invitations": invitations, "count": len(invitations), "next_token": result.get("nextToken")}
    except Exception as e:
        logger.error(f"List invitations failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to list user invitations."))


class CreateInvitationRequest(BaseModel):
    email: str
    role: Optional[str] = None
    permissions: Optional[list[str]] = None


@router.post("/invitations")
async def create_user_invitation(
    payload: CreateInvitationRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a user invitation for the advertising account."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    req = {"email": payload.email}
    if payload.role:
        req["role"] = payload.role
    if payload.permissions:
        req["permissions"] = payload.permissions
    try:
        result = await client.create_user_invitations(user_invitation_requests=[req])
        return result
    except Exception as e:
        logger.error(f"Create invitation failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to create user invitation."))


@router.get("/invitations/{invitation_id}")
async def get_user_invitation(
    invitation_id: str,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get details of a specific user invitation."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.get_user_invitation(invitation_id=invitation_id)
        return result
    except Exception as e:
        logger.error(f"Get invitation failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to get user invitation."))


class RedeemInvitationRequest(BaseModel):
    invitation_id: str


@router.post("/invitations/{invitation_id}/redeem")
async def redeem_user_invitation(
    invitation_id: str,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Redeem a user invitation to gain access to the advertising account."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.redeem_user_invitation(invitation_id=invitation_id)
        return result
    except Exception as e:
        logger.error(f"Redeem invitation failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to redeem user invitation."))


class UpdateInvitationRequest(BaseModel):
    action: str  # e.g. "REVOKE", "RESEND"


@router.put("/invitations/{invitation_id}")
async def update_user_invitation(
    invitation_id: str,
    payload: UpdateInvitationRequest,
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a user invitation (revoke, resend)."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    updates = [{"invitationId": invitation_id, "action": payload.action}]
    try:
        result = await client.update_user_invitations(updates=updates)
        return result
    except Exception as e:
        logger.error(f"Update invitation failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to update user invitation."))


# ── Stream Subscriptions (ADSP) ────────────────────────────────────────


@router.get("/stream-subscriptions")
async def list_stream_subscriptions(
    credential_id: Optional[str] = Query(None),
    max_results: int = Query(50, ge=1, le=100),
    next_token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List ADSP stream subscriptions (purchase/traffic overview)."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.list_stream_subscriptions(
            max_results=max_results, next_token=next_token
        )
        subs = _extract_list(result, ["streamSubscriptions", "subscriptions", "result", "results", "items"])
        return {"subscriptions": subs, "count": len(subs), "next_token": result.get("nextToken")}
    except Exception as e:
        logger.error(f"List stream subscriptions failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=safe_error_detail(e, "Failed to list stream subscriptions. ADSP may not be enabled."),
        )


# ── Invoices ────────────────────────────────────────────────────────────


@router.get("/invoices")
async def list_invoices(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List billing invoices for the advertising account."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        result = await client.list_invoices()
        invoices = _extract_list(result, ["invoices", "result", "results", "items"])
        return {"invoices": invoices, "count": len(invoices)}
    except Exception as e:
        logger.error(f"Invoices failed: {e}")
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to fetch invoices."))


@router.get("/tools")
async def list_available_tools(
    credential_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List all available MCP tools for the selected credentials."""
    cred = await _get_credential(db, credential_id)
    client = await _make_client(cred, db)
    try:
        tools = await client.list_tools()

        # Update tools count on credential
        cred.tools_available = len(tools)
        cred.updated_at = utcnow()

        db.add(ActivityLog(
            credential_id=cred.id,
            action="tools_listed",
            category="accounts",
            description=f"Listed {len(tools)} MCP tools",
            details={"tool_names": [t["name"] for t in tools[:20]]},
        ))

        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=safe_error_detail(e, "Failed to communicate with Amazon Ads API."))
