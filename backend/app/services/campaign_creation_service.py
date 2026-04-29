"""
Campaign Creation Service — Executes full campaign creation via MCP.

Takes an AI-generated campaign plan and creates campaign → ad group → ad
→ targets in sequence, passing IDs between steps.

Rollback contract
-----------------

Amazon's MCP API has no transactional create_campaign endpoint. A plan
spans 4 sequential calls and any one of them can fail mid-flight,
leaving an orphan campaign with no ad group, or an ad group with no ad
or targets. Phase 5 hardening introduces a *compensating-delete* path:

1. Each successful create appends to ``rollback_steps`` so we know
   exactly which IDs need cleanup if a later step fails.
2. ``execute_plan`` accepts ``rollback_on_failure`` (default ``True``)
   — when a non-recoverable error happens after the campaign was
   created, we walk ``rollback_steps`` in reverse and call the
   matching ``delete_*`` MCP tool. Best-effort: rollback failures are
   logged + recorded under ``results['rollback_errors']`` but never
   raise (we already know the user has an inconsistent state).
3. Target / ad failures inside an ad group are left as soft errors
   (the rest of the plan continues) — partial-target plans are
   recoverable from the UI, partial-campaign plans are not.

The caller can opt out via ``rollback_on_failure=False`` when running a
manual triage / dry-run.
"""

import logging
from typing import Any, Optional
from app.mcp_client import AmazonAdsMCP

logger = logging.getLogger(__name__)


def _extract_id(result: dict, keys: list[str]) -> Optional[str]:
    """Extract ID from MCP response. Amazon returns various structures."""
    if not isinstance(result, dict):
        return None
    for key in keys:
        val = result.get(key)
        if isinstance(val, list) and val:
            item = val[0]
            if isinstance(item, dict):
                return item.get("campaignId") or item.get("adGroupId") or item.get("adId") or item.get("targetId") or item.get("id")
        if isinstance(val, str):
            return val
    # Nested: success[0].campaignId etc
    for succ in result.get("success", []) or []:
        if isinstance(succ, dict):
            for k in ("campaignId", "adGroupId", "adId", "targetId", "id"):
                if succ.get(k):
                    return succ[k]
    return None


class CampaignCreationService:
    """Executes full campaign creation in sequence via MCP."""

    def __init__(self, client: AmazonAdsMCP):
        self.client = client

    async def execute_plan(
        self,
        plan: dict,
        *,
        rollback_on_failure: bool = True,
    ) -> dict:
        """
        Execute a full campaign creation plan.
        plan: {
            "campaign": { name, adProduct, targetingType, state, dailyBudget, ... },
            "ad_groups": [{ name, defaultBid, targetingStrategy, keywords: [...] }],
            "ad": { asin, name? }  # required for SP ads
        }
        Returns created IDs and any errors. When ``rollback_on_failure``
        is True (default) and the campaign-level create succeeds but a
        downstream step fatally fails, all created resources are deleted
        in reverse order before returning.
        """
        results: dict[str, Any] = {
            "campaign_id": None,
            "ad_group_ids": [],
            "ad_ids": [],
            "target_ids": [],
            "errors": [],
            "rollback_performed": False,
            "rollback_errors": [],
        }
        # Each entry: ("campaign"|"ad_group"|"ad"|"target", id_str)
        rollback_steps: list[tuple[str, str]] = []

        campaign = plan.get("campaign", {})
        if not campaign:
            results["errors"].append("Campaign data is required")
            return results

        # 1. Create campaign — fatal if this fails
        campaign_payload = self._build_campaign_payload(campaign)
        try:
            camp_result = await self.client.create_campaign([campaign_payload])
            campaign_id = _extract_id(camp_result, ["campaigns", "campaignId", "success"])
            if not campaign_id:
                campaign_id = campaign_payload.get("campaignId")
            if campaign_id:
                results["campaign_id"] = str(campaign_id)
                rollback_steps.append(("campaign", str(campaign_id)))
                logger.info(f"Created campaign: {results['campaign_id']}")
            else:
                results["errors"].append(f"Campaign created but no ID returned: {camp_result}")
                return results
        except Exception as e:
            results["errors"].append(f"Campaign creation failed: {str(e)}")
            logger.error(f"Campaign creation failed: {e}")
            return results

        # 2. Create ad groups
        ad_groups = plan.get("ad_groups", [])
        if not ad_groups:
            ad_groups = [{"name": "Default Ad Group", "defaultBid": campaign.get("defaultBid", 0.5), "keywords": []}]

        ad_groups_created = 0
        for ag in ad_groups:
            ag_payload = {
                "campaignId": results["campaign_id"],
                "name": ag.get("name", "Ad Group"),
                "state": "enabled",
            }
            bid = ag.get("defaultBid") or ag.get("default_bid")
            if bid is not None:
                ag_payload["defaultBid"] = float(bid) if isinstance(bid, (int, float)) else bid

            try:
                ag_result = await self.client.create_ad_group([ag_payload])
                ad_group_id = _extract_id(ag_result, ["adGroups", "adGroupId", "success"])
                if not ad_group_id:
                    results["errors"].append(f"Ad group created but no ID returned: {ag_result}")
                    continue
                results["ad_group_ids"].append(str(ad_group_id))
                rollback_steps.append(("ad_group", str(ad_group_id)))
                ad_groups_created += 1
                logger.info(f"Created ad group: {ad_group_id}")

                # 3. Create ad (product ad with ASIN)
                ad_data = plan.get("ad", {})
                asin = ad_data.get("asin") or campaign.get("asin")
                if asin:
                    ad_payload = {
                        "adGroupId": ad_group_id,
                        "asin": asin,
                        "state": "enabled",
                    }
                    if ad_data.get("name"):
                        ad_payload["name"] = ad_data["name"]
                    try:
                        ad_result = await self.client.create_ad([ad_payload])
                        ad_id = _extract_id(ad_result, ["ads", "adId", "success"])
                        if ad_id:
                            results["ad_ids"].append(str(ad_id))
                            rollback_steps.append(("ad", str(ad_id)))
                    except Exception as e:
                        results["errors"].append(f"Ad creation failed: {str(e)}")

                # 4. Create targets (keywords) — soft errors only
                keywords = ag.get("keywords", [])
                if keywords:
                    target_payloads = []
                    for kw in keywords:
                        text = kw.get("text") or kw.get("keyword")
                        if not text:
                            continue
                        match = (kw.get("match_type") or kw.get("matchType") or "broad").lower()
                        bid_val = kw.get("suggested_bid") or kw.get("suggestedBid") or kw.get("bid") or bid
                        target_payloads.append({
                            "adGroupId": ad_group_id,
                            "expression": text,
                            "expressionType": "keyword",
                            "matchType": match if match in ("exact", "phrase", "broad") else "broad",
                            "bid": float(bid_val) if bid_val else 0.5,
                        })
                    if target_payloads:
                        try:
                            tgt_result = await self.client.create_target(target_payloads)
                            ids = tgt_result.get("targets") or tgt_result.get("success", [])
                            if isinstance(ids, list):
                                for t in ids:
                                    if isinstance(t, dict) and t.get("targetId"):
                                        tid = str(t["targetId"])
                                        results["target_ids"].append(tid)
                                        rollback_steps.append(("target", tid))
                            logger.info(f"Created {len(target_payloads)} targets")
                        except Exception as e:
                            results["errors"].append(f"Target creation failed: {str(e)}")
            except Exception as e:
                results["errors"].append(f"Ad group creation failed: {str(e)}")
                logger.error(f"Ad group creation failed: {e}")

        # Rollback — only when *no* ad group was created (campaign is an
        # empty husk). Partial plans (some ad groups created) keep what
        # they have so the user can fix forward in the UI.
        plan_failed = ad_groups_created == 0 and bool(results["errors"])
        if plan_failed and rollback_on_failure:
            await self._rollback(rollback_steps, results)
        return results

    async def _rollback(
        self,
        steps: list[tuple[str, str]],
        results: dict,
    ) -> None:
        """Compensating-delete path. Best-effort, never raises."""
        if not steps:
            return
        logger.warning(
            "Rolling back %d created resources after partial campaign-create failure",
            len(steps),
        )
        results["rollback_performed"] = True
        # Reverse order so children are deleted before parents.
        for kind, resource_id in reversed(steps):
            try:
                if kind == "target":
                    await self.client.delete_target([resource_id])
                elif kind == "ad":
                    await self.client.delete_ad([resource_id])
                elif kind == "ad_group":
                    await self.client.delete_ad_group([resource_id])
                elif kind == "campaign":
                    await self.client.delete_campaign([resource_id])
                else:  # pragma: no cover — defensive
                    continue
                logger.info("Rollback deleted %s %s", kind, resource_id)
            except Exception as exc:
                err = f"rollback failed for {kind} {resource_id}: {exc}"
                logger.error(err)
                results["rollback_errors"].append(err)

    def _build_campaign_payload(self, campaign: dict) -> dict:
        """Build MCP campaign payload from plan."""
        ad_product = campaign.get("adProduct") or campaign.get("ad_product") or campaign.get("type") or "SPONSORED_PRODUCTS"
        targeting = campaign.get("targetingType") or campaign.get("targeting_type") or "manual"
        return {
            "name": campaign.get("name", "New Campaign"),
            "adProduct": ad_product,
            "targetingType": targeting,
            "state": campaign.get("state", "enabled"),
            "dailyBudget": float(campaign.get("dailyBudget") or campaign.get("daily_budget") or 50),
        }
