"""
Audit Service — Pulls campaign data, generates performance analysis, identifies waste.
"""

import logging
from typing import Optional
from app.mcp_client import AmazonAdsMCP
from app.services.reporting_service import ReportingService

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, client: AmazonAdsMCP, advertiser_account_id: Optional[str] = None):
        self.client = client
        self.advertiser_account_id = advertiser_account_id

    async def run_full_audit(self) -> dict:
        """
        Execute a comprehensive campaign audit:
        1. Pull all campaigns
        2. Pull ad groups
        3. Pull targets/keywords
        4. Generate performance report
        5. Analyze and identify waste
        """
        # Step 1: Query all campaigns
        logger.info("Audit: Querying campaigns...")
        campaigns_data = await self.client.query_campaigns()

        # Step 2: Query ad groups
        logger.info("Audit: Querying ad groups...")
        ad_groups_data = await self.client.query_ad_groups()

        # Step 3: Query targets
        logger.info("Audit: Querying targets...")
        targets_data = await self.client.query_targets()

        # Step 4: Generate campaign report and download performance data
        logger.info("Audit: Creating campaign report...")
        report_campaigns = []
        report_data = {}
        try:
            report_svc = ReportingService(
                self.client, advertiser_account_id=self.advertiser_account_id
            )
            from datetime import date, timedelta
            end_date = date.today()
            start_date = end_date - timedelta(days=30)
            end = end_date.isoformat()
            start = start_date.isoformat()

            # Audits can afford to wait longer — reports typically take ~110s
            mcp_result = await report_svc.generate_mcp_report(start, end, max_wait=180)

            if mcp_result.get("_pending_report_id"):
                logger.warning(f"Audit: Report still pending after 180s — ID: {mcp_result['_pending_report_id']}")
                report_data = {"note": "Report still processing", "pending_id": mcp_result["_pending_report_id"]}
            else:
                report_campaigns = report_svc.parse_report_campaigns(mcp_result)
                report_data = mcp_result
                logger.info(f"Audit: Got {len(report_campaigns)} campaigns from report")
        except Exception as e:
            logger.warning(f"Report creation skipped: {e}")
            report_data = {"note": "Report generation failed or timed out"}

        # Step 5: Analyze results — merge report performance data with query data
        analysis = self._analyze_campaigns(
            campaigns_data, ad_groups_data, targets_data, report_campaigns
        )

        return {
            "campaigns": campaigns_data,
            "ad_groups": ad_groups_data,
            "targets": targets_data,
            "report": report_data,
            "report_campaigns": report_campaigns,
            "analysis": analysis,
            "summary": analysis.get("summary", {}),
            "date_range": {
                "start_date": start,
                "end_date": end,
                "label": "Last 30 Days",
            },
        }

    async def create_and_retrieve_report(
        self, report_type: str = "campaign", date_range: Optional[dict] = None
    ) -> dict:
        """Create a specific report, poll for completion, and return results."""
        from datetime import date, timedelta

        # Build proper date range if not provided
        if not date_range:
            end = date.today().isoformat()
            start = (date.today() - timedelta(days=30)).isoformat()
            date_range = {"startDate": start, "endDate": end}

        report_config = {
            "reports": [{
                "format": "GZIP_JSON",
                "periods": [{"datePeriod": date_range}],
            }],
        }

        # Route through the mcp_client convenience method so normalization is applied
        if report_type == "campaign":
            create_result = await self.client.create_campaign_report(
                report_config,
                advertiser_account_id=self.advertiser_account_id,
            )
        else:
            if report_type == "product":
                create_result = await self.client.create_product_report(
                    report_config, advertiser_account_id=self.advertiser_account_id
                )
            elif report_type == "inventory":
                create_result = await self.client.create_inventory_report(
                    report_config, advertiser_account_id=self.advertiser_account_id
                )
            else:
                create_result = await self.client.create_report(
                    report_config, advertiser_account_id=self.advertiser_account_id
                )

        # Extract report IDs and poll for completion
        report_ids = self._extract_report_ids(create_result)
        if report_ids:
            return await self.client.poll_report(report_ids, max_wait=120, interval=10)
        return create_result

    @staticmethod
    def _extract_report_ids(result: dict) -> list:
        """Extract report IDs from the MCP response."""
        if not isinstance(result, dict):
            return []
        # Format: {"success": [{"report": {"reportId": "..."}}]}
        for entry in result.get("success", []):
            if isinstance(entry, dict):
                report = entry.get("report", {})
                if isinstance(report, dict) and "reportId" in report:
                    return [report["reportId"]]
        # Fallback formats
        if "reportIds" in result:
            return result["reportIds"]
        if "reportId" in result:
            return [result["reportId"]]
        return []

    def _analyze_campaigns(
        self,
        campaigns: dict,
        ad_groups: dict,
        targets: dict,
        report_campaigns: list = None,
    ) -> dict:
        """
        Analyze campaign data to identify waste and opportunities.
        Merges query data (metadata/state) with report data (performance metrics).
        """
        summary = {
            "total_campaigns": 0,
            "active_campaigns": 0,
            "paused_campaigns": 0,
            "total_ad_groups": 0,
            "total_targets": 0,
            "total_spend": 0,
            "total_sales": 0,
            "avg_acos": 0,
            "avg_roas": 0,
            "waste_identified": 0,
        }

        issues = []
        opportunities = []

        # Parse campaigns from query (metadata: state, budget)
        campaign_list = self._extract_list(campaigns)
        summary["total_campaigns"] = len(campaign_list)

        total_budget = 0
        for campaign in campaign_list:
            state = self._get_nested(campaign, "state", "")
            if state.upper() == "ENABLED":
                summary["active_campaigns"] += 1
            elif state.upper() == "PAUSED":
                summary["paused_campaigns"] += 1

            # Extract budget from MCP nested format
            budgets = campaign.get("budgets", [])
            for b in budgets:
                if b.get("recurrenceTimePeriod") == "DAILY":
                    mv = b.get("budgetValue", {}).get("monetaryBudgetValue", {}).get("monetaryBudget", {})
                    total_budget += float(mv.get("value", 0))

        summary["total_daily_budget"] = round(total_budget, 2)

        # Parse ad groups
        ag_list = self._extract_list(ad_groups)
        summary["total_ad_groups"] = len(ag_list)

        # Parse targets
        target_list = self._extract_list(targets)
        summary["total_targets"] = len(target_list)

        # Merge performance data from report (spend, sales, clicks, etc.)
        report_campaigns = report_campaigns or []
        total_spend = sum(float(c.get("spend", 0)) for c in report_campaigns)
        total_sales = sum(float(c.get("sales", 0)) for c in report_campaigns)
        total_clicks = sum(int(c.get("clicks", 0)) for c in report_campaigns)
        total_impressions = sum(int(c.get("impressions", 0)) for c in report_campaigns)
        total_orders = sum(int(c.get("orders", 0)) for c in report_campaigns)

        summary["total_spend"] = round(total_spend, 2)
        summary["total_sales"] = round(total_sales, 2)
        summary["avg_acos"] = round(total_spend / total_sales * 100, 2) if total_sales > 0 else 0
        summary["avg_roas"] = round(total_sales / total_spend, 2) if total_spend > 0 else 0
        summary["total_clicks"] = total_clicks
        summary["total_impressions"] = total_impressions
        summary["total_orders"] = total_orders

        # ── Per-campaign analysis (issues + opportunities with specific names) ──

        waste = 0
        for c in report_campaigns:
            cname = c.get("campaign_name") or "Unknown"
            cid = c.get("campaign_id")
            spend = float(c.get("spend", 0))
            sales = float(c.get("sales", 0))
            acos = float(c.get("acos", 0))
            impressions = int(c.get("impressions", 0))
            clicks = int(c.get("clicks", 0))
            orders = int(c.get("orders", 0))
            roas = float(c.get("roas", 0))

            # ISSUE: High ACOS with meaningful spend
            if spend > 5 and acos > 40:
                waste += spend * (acos - 25) / 100
                issues.append({
                    "severity": "high" if acos > 60 else "medium",
                    "type": "high_acos",
                    "message": f"'{cname}' — {acos:.1f}% ACOS on ${spend:.2f} spend (${sales:.2f} sales). Target under 25%.",
                    "campaign_id": cid,
                    "campaign_name": cname,
                })

            # ISSUE: Spending with zero sales
            if spend > 10 and sales == 0:
                issues.append({
                    "severity": "high",
                    "type": "zero_sales",
                    "message": f"'{cname}' — spent ${spend:.2f} with zero sales and {clicks} clicks. Consider pausing or reviewing targeting.",
                    "campaign_id": cid,
                    "campaign_name": cname,
                })

            # ISSUE: High spend, low clicks (poor ad relevance or low bids)
            if spend > 20 and clicks < 10 and impressions > 500:
                ctr = round(clicks / impressions * 100, 2) if impressions > 0 else 0
                issues.append({
                    "severity": "medium",
                    "type": "low_ctr",
                    "message": f"'{cname}' — only {ctr}% CTR ({clicks} clicks from {impressions:,} impressions). Ad copy or targeting may need improvement.",
                    "campaign_id": cid,
                    "campaign_name": cname,
                })

            # OPPORTUNITY: Low impressions — increase bids / broaden targeting
            if impressions < 100 and spend > 0:
                opportunities.append({
                    "type": "increase_visibility",
                    "message": f"'{cname}' — only {impressions} impressions. Increase bids or broaden targeting to improve visibility.",
                    "potential_impact": "medium",
                    "campaign_id": cid,
                    "campaign_name": cname,
                })

            # OPPORTUNITY: Good ROAS — scale up
            if roas > 5 and spend > 5:
                opportunities.append({
                    "type": "scale_winner",
                    "message": f"'{cname}' — strong {roas:.1f}x ROAS on ${spend:.2f} spend. Consider increasing budget to scale.",
                    "potential_impact": "high",
                    "campaign_id": cid,
                    "campaign_name": cname,
                })

            # OPPORTUNITY: Decent clicks but no orders — conversion issue
            if clicks > 50 and orders == 0 and spend > 5:
                opportunities.append({
                    "type": "conversion_optimization",
                    "message": f"'{cname}' — {clicks} clicks but 0 orders. Review product listing, pricing, or landing page.",
                    "potential_impact": "high",
                    "campaign_id": cid,
                    "campaign_name": cname,
                })

            # OPPORTUNITY: High impressions but low clicks — improve ad copy
            if impressions > 5000 and clicks < 20:
                ctr = round(clicks / impressions * 100, 2) if impressions > 0 else 0
                opportunities.append({
                    "type": "improve_ad_copy",
                    "message": f"'{cname}' — {impressions:,} impressions but only {ctr}% CTR. Improve title, images, or targeting relevance.",
                    "potential_impact": "medium",
                    "campaign_id": cid,
                    "campaign_name": cname,
                })

        summary["waste_identified"] = round(waste, 2)

        # ── Structural issues (account-level) ──

        if summary["active_campaigns"] == 0 and summary["total_campaigns"] > 0:
            issues.append({
                "severity": "high",
                "type": "no_active_campaigns",
                "message": "No active campaigns found. All campaigns are paused or archived.",
            })

        if summary["total_targets"] == 0 and summary["active_campaigns"] > 0:
            issues.append({
                "severity": "medium",
                "type": "no_targets",
                "message": "Active campaigns found but no targeting configured.",
            })

        # ── Account-level opportunities (only if specific campaign opps don't cover it) ──

        # Auto campaigns that could benefit from keyword harvesting
        auto_campaigns = [
            c for c in report_campaigns
            if (c.get("targeting_type") or "").lower() == "auto"
            and float(c.get("sales", 0)) > 0
        ]
        for c in auto_campaigns:
            cname = c.get("campaign_name") or "Unknown"
            opportunities.append({
                "type": "keyword_harvest",
                "message": f"'{cname}' — auto campaign with ${float(c.get('sales', 0)):.2f} sales. Harvest converting search terms into manual campaigns for better control.",
                "potential_impact": "high",
                "campaign_id": c.get("campaign_id"),
                "campaign_name": cname,
            })

        summary["issues_count"] = len(issues)
        summary["opportunities_count"] = len(opportunities)

        return {
            "summary": summary,
            "issues": issues,
            "opportunities": opportunities,
        }

    @staticmethod
    def _extract_list(data: dict) -> list:
        """Extract list from various MCP response formats."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ["campaigns", "adGroups", "targets", "ads", "result", "results", "items"]:
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        return val
            if "result" in data and isinstance(data["result"], list):
                return data["result"]
        return []

    @staticmethod
    def _get_nested(obj: dict, key: str, default=None):
        """Safely get a nested key from a dict."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return default
