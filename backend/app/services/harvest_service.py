"""
Harvest Service — Keyword harvesting from auto campaigns to manual campaigns.
Supports two modes:
  1. Amazon Auto-Create: Uses create_campaign_harvest_targets (creates new manual campaign)
  2. Add to Existing: Manually adds keywords as targets to an existing manual campaign

Also handles negative keyword creation in source auto campaigns to prevent
cannibalization between auto and manual campaigns.
"""

import logging
from typing import Optional
from app.mcp_client import AmazonAdsMCP

logger = logging.getLogger(__name__)


class HarvestService:
    def __init__(self, client: AmazonAdsMCP):
        self.client = client

    async def execute_harvest(
        self,
        source_campaign_id: str,
        sales_threshold: float = 1.0,
        acos_threshold: Optional[float] = None,
        target_mode: str = "new",
        target_campaign_id: Optional[str] = None,
        target_ad_group_id: Optional[str] = None,
        match_type: Optional[str] = None,
        negate_in_source: bool = True,
    ) -> dict:
        """
        Execute keyword harvesting.

        target_mode="new": Amazon creates a new manual campaign via
            create_campaign_harvest_targets. The target campaign ID, name, etc.
            are returned by Amazon and stored.

        target_mode="existing": Keywords are added as targets to the specified
            existing manual campaign via create_target.

        When negate_in_source=True, harvested keywords are also added as
        negative keywords in the source auto campaign to prevent cannibalization.
        """
        logger.info(f"Starting harvest from campaign: {source_campaign_id}, mode: {target_mode}")

        if target_mode == "existing" and target_campaign_id:
            return await self._harvest_to_existing(
                source_campaign_id=source_campaign_id,
                target_campaign_id=target_campaign_id,
                target_ad_group_id=target_ad_group_id,
                sales_threshold=sales_threshold,
                acos_threshold=acos_threshold,
                match_type=match_type,
                negate_in_source=negate_in_source,
            )
        else:
            return await self._harvest_create_new(
                source_campaign_id=source_campaign_id,
                sales_threshold=sales_threshold,
                acos_threshold=acos_threshold,
                negate_in_source=negate_in_source,
            )

    async def _harvest_create_new(
        self,
        source_campaign_id: str,
        sales_threshold: float,
        acos_threshold: Optional[float],
        negate_in_source: bool,
    ) -> dict:
        """
        Uses Amazon's create_campaign_harvest_targets tool.
        Amazon creates a new manual campaign and sets up continuous monitoring.
        """
        harvest_request = {
            "sourceCampaignId": source_campaign_id,
            "salesThreshold": sales_threshold,
        }
        if acos_threshold is not None:
            harvest_request["acosThreshold"] = acos_threshold

        try:
            result = await self.client.create_harvest(
                harvest_requests=[harvest_request]
            )
            logger.info(f"Harvest result: {result}")

            target_id = self._extract_target_id(result)
            keywords_count = self._extract_keyword_count(result)
            keywords = self._extract_keywords(result)

            # Negate harvested keywords in the source auto campaign
            negated_count = 0
            if negate_in_source and keywords:
                negated_count = await self._negate_keywords_in_source(
                    source_campaign_id=source_campaign_id,
                    keywords=keywords,
                )

            return {
                "status": "success",
                "mode": "new_campaign",
                "source_campaign_id": source_campaign_id,
                "target_campaign_id": target_id,
                "keywords_harvested": keywords_count,
                "keywords_negated_in_source": negated_count,
                "keywords": keywords,
                "raw_result": result,
            }
        except Exception as e:
            logger.error(f"Harvest failed: {e}")
            return {
                "status": "error",
                "mode": "new_campaign",
                "source_campaign_id": source_campaign_id,
                "error": str(e),
            }

    async def _harvest_to_existing(
        self,
        source_campaign_id: str,
        target_campaign_id: str,
        target_ad_group_id: Optional[str],
        sales_threshold: float,
        acos_threshold: Optional[float],
        match_type: Optional[str],
        negate_in_source: bool,
    ) -> dict:
        """
        Manual harvest: get candidates from source, add as targets to existing campaign.
        """
        try:
            # Step 1: Get keyword candidates from source auto campaign
            candidates = await self.get_harvest_candidates(source_campaign_id)
            targets = candidates.get("targets", {})

            # Extract target list from response
            target_list = []
            if isinstance(targets, dict):
                for key in ["targets", "result", "results", "items"]:
                    if key in targets and isinstance(targets[key], list):
                        target_list = targets[key]
                        break
            elif isinstance(targets, list):
                target_list = targets

            # Step 2: Filter by thresholds
            qualified_keywords = []
            for t in target_list:
                kw_text = t.get("keyword") or t.get("keywordText") or t.get("expression") or t.get("text")
                if not kw_text:
                    continue

                sales = t.get("sales") or t.get("attributedSales7d") or 0
                acos = t.get("acos") or t.get("acos7d") or 0
                clicks = t.get("clicks") or 0

                # Apply thresholds
                try:
                    if float(sales) < sales_threshold:
                        continue
                    if acos_threshold and float(acos) > acos_threshold:
                        continue
                except (ValueError, TypeError):
                    continue

                qualified_keywords.append({
                    "keyword": kw_text,
                    "matchType": match_type or t.get("matchType", "BROAD"),
                    "bid": t.get("bid"),
                    "clicks": clicks,
                    "sales": sales,
                    "spend": t.get("spend") or t.get("cost"),
                    "acos": acos,
                })

            if not qualified_keywords:
                return {
                    "status": "success",
                    "mode": "existing_campaign",
                    "source_campaign_id": source_campaign_id,
                    "target_campaign_id": target_campaign_id,
                    "keywords_harvested": 0,
                    "keywords_negated_in_source": 0,
                    "keywords": [],
                    "message": "No keywords met the harvest thresholds.",
                }

            # Step 3: If no ad group specified, find first ad group in target campaign
            if not target_ad_group_id:
                ad_groups_result = await self.client.query_ad_groups(campaign_id=target_campaign_id)
                ad_group_list = []
                if isinstance(ad_groups_result, dict):
                    for key in ["adGroups", "result", "results", "items"]:
                        if key in ad_groups_result and isinstance(ad_groups_result[key], list):
                            ad_group_list = ad_groups_result[key]
                            break
                if ad_group_list:
                    target_ad_group_id = (
                        ad_group_list[0].get("adGroupId")
                        or ad_group_list[0].get("id")
                    )

            if not target_ad_group_id:
                return {
                    "status": "error",
                    "mode": "existing_campaign",
                    "source_campaign_id": source_campaign_id,
                    "target_campaign_id": target_campaign_id,
                    "error": "No ad group found in the target manual campaign. Create an ad group first.",
                }

            # Step 4: Add keywords as targets to the existing manual campaign
            create_targets = []
            for kw in qualified_keywords:
                target_entry = {
                    "adGroupId": target_ad_group_id,
                    "campaignId": target_campaign_id,
                    "keyword": kw["keyword"],
                    "matchType": kw["matchType"].upper(),
                    "state": "ENABLED",
                    "adProduct": "SPONSORED_PRODUCTS",
                }
                if kw.get("bid"):
                    target_entry["bid"] = kw["bid"]
                create_targets.append(target_entry)

            create_result = await self.client.call_tool(
                "campaign_management-create_target",
                {"body": {"targets": create_targets}},
            )
            logger.info(f"Created {len(create_targets)} targets in campaign {target_campaign_id}")

            # Step 5: Negate harvested keywords in the source auto campaign
            negated_count = 0
            if negate_in_source:
                negated_count = await self._negate_keywords_in_source(
                    source_campaign_id=source_campaign_id,
                    keywords=qualified_keywords,
                )

            return {
                "status": "success",
                "mode": "existing_campaign",
                "source_campaign_id": source_campaign_id,
                "target_campaign_id": target_campaign_id,
                "target_ad_group_id": target_ad_group_id,
                "keywords_harvested": len(qualified_keywords),
                "keywords_negated_in_source": negated_count,
                "keywords": qualified_keywords,
                "create_result": create_result,
            }

        except Exception as e:
            logger.error(f"Harvest to existing campaign failed: {e}")
            return {
                "status": "error",
                "mode": "existing_campaign",
                "source_campaign_id": source_campaign_id,
                "target_campaign_id": target_campaign_id,
                "error": str(e),
            }

    async def _negate_keywords_in_source(
        self,
        source_campaign_id: str,
        keywords: list[dict],
    ) -> int:
        """
        Add harvested keywords as negative exact targets in the source auto campaign.
        This prevents the auto campaign from bidding on keywords now handled by
        the manual campaign, avoiding cannibalization and wasted spend.
        """
        if not keywords:
            return 0

        try:
            # Get ad groups in the source campaign for negation
            ad_groups_result = await self.client.query_ad_groups(campaign_id=source_campaign_id)
            ad_group_list = []
            if isinstance(ad_groups_result, dict):
                for key in ["adGroups", "result", "results", "items"]:
                    if key in ad_groups_result and isinstance(ad_groups_result[key], list):
                        ad_group_list = ad_groups_result[key]
                        break

            if not ad_group_list:
                logger.warning(f"No ad groups found in source campaign {source_campaign_id} for negation")
                return 0

            source_ad_group_id = (
                ad_group_list[0].get("adGroupId")
                or ad_group_list[0].get("id")
            )

            # Create negative keyword targets
            negative_targets = []
            for kw in keywords:
                kw_text = kw.get("keyword") or kw.get("text", "")
                if not kw_text:
                    continue
                negative_targets.append({
                    "adGroupId": source_ad_group_id,
                    "campaignId": source_campaign_id,
                    "keyword": kw_text,
                    "matchType": "NEGATIVE_EXACT",
                    "state": "ENABLED",
                    "adProduct": "SPONSORED_PRODUCTS",
                })

            if negative_targets:
                await self.client.call_tool(
                    "campaign_management-create_target",
                    {"body": {"targets": negative_targets}},
                )
                logger.info(f"Created {len(negative_targets)} negative keywords in source campaign {source_campaign_id}")

            return len(negative_targets)

        except Exception as e:
            logger.warning(f"Failed to negate keywords in source campaign: {e}")
            return 0

    async def get_harvest_candidates(self, source_campaign_id: str) -> dict:
        """
        Preview keywords from an auto campaign that would be good harvest candidates.
        Queries the source campaign's targets and their performance.
        """
        logger.info(f"Fetching harvest candidates from: {source_campaign_id}")
        targets = await self.client.query_targets(campaign_id=source_campaign_id)
        return {
            "source_campaign_id": source_campaign_id,
            "targets": targets,
        }

    @staticmethod
    def _extract_target_id(result: dict) -> Optional[str]:
        """Extract the created manual campaign ID from the harvest result."""
        if isinstance(result, dict):
            for key in ["targetCampaignId", "campaignId", "manualCampaignId"]:
                if key in result:
                    return result[key]
            if "result" in result and isinstance(result["result"], dict):
                for key in ["targetCampaignId", "campaignId", "manualCampaignId"]:
                    if key in result["result"]:
                        return result["result"][key]
        return None

    @staticmethod
    def _extract_keyword_count(result: dict) -> int:
        """Extract the count of harvested keywords from the result."""
        if isinstance(result, dict):
            for key in ["keywordsHarvested", "keywords_harvested", "count"]:
                if key in result:
                    try:
                        return int(result[key])
                    except (ValueError, TypeError):
                        pass
        return 0

    @staticmethod
    def _extract_keywords(result: dict) -> list[dict]:
        """Extract the list of harvested keywords from the result."""
        if isinstance(result, dict):
            for key in ["keywords", "harvestedKeywords", "targets"]:
                if key in result and isinstance(result[key], list):
                    return result[key]
            if "result" in result and isinstance(result["result"], dict):
                for key in ["keywords", "harvestedKeywords", "targets"]:
                    if key in result["result"] and isinstance(result["result"][key], list):
                        return result["result"][key]
        return []
