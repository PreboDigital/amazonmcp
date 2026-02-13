"""
Campaign Creation Service — Executes full campaign creation via MCP.
Takes an AI-generated campaign plan and creates campaign → ad group → ad → targets
in sequence, passing IDs between steps.
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

    async def execute_plan(self, plan: dict) -> dict:
        """
        Execute a full campaign creation plan.
        plan: {
            "campaign": { name, adProduct, targetingType, state, dailyBudget, ... },
            "ad_groups": [{ name, defaultBid, targetingStrategy, keywords: [...] }],
            "ad": { asin, name? }  # required for SP ads
        }
        Returns created IDs and any errors.
        """
        results = {"campaign_id": None, "ad_group_ids": [], "ad_ids": [], "target_ids": [], "errors": []}

        campaign = plan.get("campaign", {})
        if not campaign:
            results["errors"].append("Campaign data is required")
            return results

        # 1. Create campaign
        campaign_payload = self._build_campaign_payload(campaign)
        try:
            camp_result = await self.client.create_campaign([campaign_payload])
            campaign_id = _extract_id(camp_result, ["campaigns", "campaignId", "success"])
            if not campaign_id:
                campaign_id = campaign_payload.get("campaignId")  # In case it was returned
            if campaign_id:
                results["campaign_id"] = str(campaign_id)
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
                if ad_group_id:
                    results["ad_group_ids"].append(str(ad_group_id))
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
                        except Exception as e:
                            results["errors"].append(f"Ad creation failed: {str(e)}")

                    # 4. Create targets (keywords)
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
                                            results["target_ids"].append(str(t["targetId"]))
                                logger.info(f"Created {len(target_payloads)} targets")
                            except Exception as e:
                                results["errors"].append(f"Target creation failed: {str(e)}")
                else:
                    results["errors"].append(f"Ad group created but no ID returned: {ag_result}")
            except Exception as e:
                results["errors"].append(f"Ad group creation failed: {str(e)}")
                logger.error(f"Ad group creation failed: {e}")

        return results

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
