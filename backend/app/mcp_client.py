"""
Amazon Ads MCP Client
Connects to the official Amazon Ads MCP Server via Streamable HTTP transport.
Handles tool calls for campaign management, reporting, billing, and more.
"""

import logging
from typing import Any, Optional
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

logger = logging.getLogger(__name__)

# ── Region URL Mapping ────────────────────────────────────────────────
REGION_URLS = {
    "na": "https://advertising-ai.amazon.com/mcp",
    "eu": "https://advertising-ai-eu.amazon.com/mcp",
    "fe": "https://advertising-ai-fe.amazon.com/mcp",
}


class AmazonAdsMCP:
    """
    Wrapper around the Amazon Ads MCP Server.
    Each instance is configured with credentials and can call any MCP tool.
    """

    def __init__(
        self,
        client_id: str,
        access_token: str,
        region: str = "na",
        profile_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.region = region.lower()
        self.profile_id = profile_id
        self.account_id = account_id

    @property
    def url(self) -> str:
        url = REGION_URLS.get(self.region)
        if not url:
            raise ValueError(f"Unsupported region: {self.region}. Use na, eu, or fe.")
        return url

    @property
    def headers(self) -> dict[str, str]:
        h = {
            "Amazon-Ads-ClientId": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json, text/event-stream",
        }
        has_fixed = False
        if self.profile_id:
            h["Amazon-Advertising-API-Scope"] = self.profile_id
            has_fixed = True
        if self.account_id:
            h["Amazon-Ads-AccountID"] = self.account_id
            has_fixed = True
        if has_fixed:
            h["Amazon-Ads-AI-Account-Selection-Mode"] = "FIXED"
        return h

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] = None) -> dict:
        """Call a single MCP tool and return the result."""
        if arguments is None:
            arguments = {}

        logger.info(f"MCP call: {tool_name} with args keys: {list(arguments.keys())}")

        try:
            async with streamablehttp_client(url=self.url, headers=self.headers) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return self._parse_result(result)
        except Exception as e:
            logger.error(f"MCP tool call failed: {tool_name} - {str(e)}")
            raise MCPError(f"Failed to call {tool_name}: {str(e)}")

    async def call_tools_sequential(self, calls: list[tuple[str, dict]]) -> list[dict]:
        """Call multiple MCP tools in sequence within a single session."""
        results = []
        try:
            async with streamablehttp_client(url=self.url, headers=self.headers) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    for tool_name, arguments in calls:
                        logger.info(f"MCP sequential call: {tool_name}")
                        result = await session.call_tool(tool_name, arguments or {})
                        results.append(self._parse_result(result))
        except Exception as e:
            logger.error(f"MCP sequential calls failed: {str(e)}")
            raise MCPError(f"Sequential tool calls failed: {str(e)}")
        return results

    async def list_tools(self, include_schema: bool = False) -> list[dict]:
        """List all available MCP tools. Optionally include inputSchema."""
        try:
            async with streamablehttp_client(url=self.url, headers=self.headers) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    tools = []
                    for t in result.tools:
                        tool_info = {"name": t.name, "description": t.description}
                        if include_schema and hasattr(t, "inputSchema"):
                            tool_info["inputSchema"] = t.inputSchema
                        tools.append(tool_info)
                    return tools
        except Exception as e:
            logger.error(f"MCP list_tools failed: {str(e)}")
            raise MCPError(f"Failed to list tools: {str(e)}")

    async def test_connection(self) -> dict:
        """Test the MCP connection by listing tools."""
        try:
            tools = await self.list_tools()
            return {
                "status": "connected",
                "tools_available": len(tools),
                "region": self.region,
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "region": self.region,
            }

    # ── Paginated Query Helper ────────────────────────────────────────

    async def _paginated_query(
        self,
        tool_name: str,
        body: dict,
        result_key: str,
        max_pages: int = 20,
    ) -> list:
        """
        Follow nextToken pagination to fetch ALL results from an MCP query.
        The Amazon Ads MCP API returns max 1000 items per page with a nextToken
        for subsequent pages. This method keeps fetching until no nextToken or
        max_pages is reached.
        """
        all_items = []
        page = 0
        next_token = None

        while page < max_pages:
            page_body = dict(body)
            if next_token:
                page_body["nextToken"] = next_token

            result = await self.call_tool(tool_name, {"body": page_body})

            # Extract items from response
            items = []
            if isinstance(result, dict):
                for key in (result_key, "result", "results", "items"):
                    if key in result and isinstance(result[key], list):
                        items = result[key]
                        break
                next_token = result.get("nextToken")
            elif isinstance(result, list):
                items = result
                next_token = None

            all_items.extend(items)
            page += 1
            logger.info(f"_paginated_query({tool_name}) page {page}: {len(items)} items (total so far: {len(all_items)})")

            if not next_token:
                break

        logger.info(f"_paginated_query({tool_name}) complete: {len(all_items)} total items in {page} page(s)")
        return all_items

    # ── Convenience Methods ──────────────────────────────────────────

    async def query_accounts(self) -> dict:
        return await self.call_tool("account_management-query_advertiser_account", {
            "body": {}
        })

    async def query_campaigns(
        self,
        filters: dict = None,
        ad_product: str = None,
        all_products: bool = True,
    ) -> dict:
        """
        Query campaigns. By default fetches all three ad product types
        (SP, SB, SD) via sequential calls and merges results.
        Pass ad_product="SPONSORED_PRODUCTS" to fetch just one type.
        The Amazon Ads MCP API only accepts one ad product per request.
        All queries follow nextToken pagination to get ALL results.
        """
        if ad_product:
            body = dict(filters or {})
            body["adProductFilter"] = {"include": [ad_product]}
            items = await self._paginated_query(
                "campaign_management-query_campaign", body, "campaigns"
            )
            return {"campaigns": items}

        if all_products:
            return await self._query_all_campaigns(filters)

        body = dict(filters or {})
        body["adProductFilter"] = {"include": ["SPONSORED_PRODUCTS"]}
        items = await self._paginated_query(
            "campaign_management-query_campaign", body, "campaigns"
        )
        return {"campaigns": items}

    async def _query_all_campaigns(self, filters: dict = None) -> dict:
        """
        Query campaigns across all three ad product types (SP, SB, SD)
        and merge results. Amazon MCP only allows one ad product per request.
        """
        ad_products = ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]
        all_campaigns = []

        for ap in ad_products:
            try:
                body = dict(filters or {})
                body["adProductFilter"] = {"include": [ap]}
                campaigns = await self._paginated_query(
                    "campaign_management-query_campaign", body, "campaigns"
                )
                logger.info(f"query_campaigns({ap}): {len(campaigns)} campaigns")
                all_campaigns.extend(campaigns)
            except Exception as e:
                logger.warning(f"query_campaigns({ap}) failed: {e}")

        logger.info(f"Total campaigns across all ad products: {len(all_campaigns)}")
        return {"campaigns": all_campaigns}

    async def query_ad_groups(
        self,
        campaign_id: str = None,
        ad_product: str = "SPONSORED_PRODUCTS",
        all_products: bool = False,
    ) -> dict:
        """Query ad groups. Set all_products=True to fetch SP, SB, and SD."""
        if all_products:
            return await self._query_all_ad_groups(campaign_id)
        body = {"adProductFilter": {"include": [ad_product]}}
        if campaign_id:
            body["campaignIdFilter"] = {"include": [campaign_id]}
        items = await self._paginated_query(
            "campaign_management-query_ad_group", body, "adGroups"
        )
        return {"adGroups": items}

    async def _query_all_ad_groups(self, campaign_id: str = None) -> dict:
        """Query ad groups across SP, SB, and SD."""
        ad_products = ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]
        all_groups = []
        for ap in ad_products:
            try:
                result = await self.query_ad_groups(campaign_id=campaign_id, ad_product=ap)
                groups = result.get("adGroups") or []
                logger.info(f"query_ad_groups({ap}): {len(groups)} ad groups")
                all_groups.extend(groups)
            except Exception as e:
                logger.warning(f"query_ad_groups({ap}) failed: {e}")
        logger.info(f"Total ad groups across all ad products: {len(all_groups)}")
        return {"adGroups": all_groups}

    async def query_targets(
        self,
        campaign_id: str = None,
        ad_group_id: str = None,
        ad_product: str = "SPONSORED_PRODUCTS",
        all_products: bool = False,
    ) -> dict:
        """Query targets (keywords/product targets). Set all_products=True to fetch SP, SB, and SD."""
        if all_products:
            return await self._query_all_targets(campaign_id, ad_group_id)
        body = {"adProductFilter": {"include": [ad_product]}}
        if campaign_id:
            body["campaignIdFilter"] = {"include": [campaign_id]}
        if ad_group_id:
            body["adGroupIdFilter"] = {"include": [ad_group_id]}
        items = await self._paginated_query(
            "campaign_management-query_target", body, "targets"
        )
        return {"targets": items}

    async def _query_all_targets(
        self, campaign_id: str = None, ad_group_id: str = None
    ) -> dict:
        """Query targets across SP, SB, and SD."""
        ad_products = ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]
        all_targets = []
        for ap in ad_products:
            try:
                result = await self.query_targets(
                    campaign_id=campaign_id, ad_group_id=ad_group_id, ad_product=ap
                )
                targets = result.get("targets") or []
                logger.info(f"query_targets({ap}): {len(targets)} targets")
                all_targets.extend(targets)
            except Exception as e:
                logger.warning(f"query_targets({ap}) failed: {e}")
        logger.info(f"Total targets across all ad products: {len(all_targets)}")
        return {"targets": all_targets}

    async def query_ads(
        self,
        campaign_id: str = None,
        ad_group_id: str = None,
        ad_product: str = "SPONSORED_PRODUCTS",
        all_products: bool = False,
    ) -> dict:
        """Query ads. Set all_products=True to fetch SP, SB, and SD."""
        if all_products:
            return await self._query_all_ads(campaign_id, ad_group_id)
        body = {"adProductFilter": {"include": [ad_product]}}
        if campaign_id:
            body["campaignIdFilter"] = {"include": [campaign_id]}
        if ad_group_id:
            body["adGroupIdFilter"] = {"include": [ad_group_id]}
        items = await self._paginated_query(
            "campaign_management-query_ad", body, "ads"
        )
        return {"ads": items}

    async def _query_all_ads(
        self, campaign_id: str = None, ad_group_id: str = None
    ) -> dict:
        """Query ads across SP, SB, and SD."""
        ad_products = ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]
        all_ads = []
        for ap in ad_products:
            try:
                result = await self.query_ads(
                    campaign_id=campaign_id, ad_group_id=ad_group_id, ad_product=ap
                )
                ads = result.get("ads") or []
                logger.info(f"query_ads({ap}): {len(ads)} ads")
                all_ads.extend(ads)
            except Exception as e:
                logger.warning(f"query_ads({ap}) failed: {e}")
        logger.info(f"Total ads across all ad products: {len(all_ads)}")
        return {"ads": all_ads}

    async def create_ad(self, ads: list[dict], account: dict = None) -> dict:
        body = {"ads": ads}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-create_ad", {"body": body})

    async def update_ad(self, ads: list[dict], account: dict = None) -> dict:
        body = {"ads": ads}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-update_ad", {"body": body})

    async def delete_ad(self, ad_ids: list[str], account: dict = None) -> dict:
        body = {"adIds": ad_ids}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-delete_ad", {"body": body})

    # ── Ad Association Methods ────────────────────────────────────────

    async def query_ad_associations(self, ad_group_id: str = None, ad_id: str = None) -> dict:
        body = {}
        if ad_group_id:
            body["adGroupIdFilter"] = {"include": [ad_group_id]}
        if ad_id:
            body["adIdFilter"] = {"include": [ad_id]}
        return await self.call_tool("campaign_management-query_ad_association", {"body": body})

    async def create_ad_association(self, associations: list[dict], account: dict = None) -> dict:
        body = {"adAssociations": associations}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-create_ad_association", {"body": body})

    async def update_ad_association(self, associations: list[dict], account: dict = None) -> dict:
        body = {"adAssociations": associations}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-update_ad_association", {"body": body})

    async def delete_ad_association(self, association_ids: list[str], account: dict = None) -> dict:
        body = {"adAssociationIds": association_ids}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-delete_ad_association", {"body": body})

    # ── Ad Group CRUD Methods ─────────────────────────────────────────

    async def create_ad_group(self, ad_groups: list[dict], account: dict = None) -> dict:
        body = {"adGroups": ad_groups}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-create_ad_group", {"body": body})

    async def update_ad_group(self, ad_groups: list[dict], account: dict = None) -> dict:
        body = {"adGroups": ad_groups}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-update_ad_group", {"body": body})

    async def delete_ad_group(self, ad_group_ids: list[str], account: dict = None) -> dict:
        body = {"adGroupIds": ad_group_ids}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-delete_ad_group", {"body": body})

    # ── Campaign CRUD Methods ─────────────────────────────────────────

    async def create_campaign(self, campaigns: list[dict], account: dict = None) -> dict:
        body = {"campaigns": campaigns}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-create_campaign", {"body": body})

    async def update_campaign(self, campaigns: list[dict], account: dict = None) -> dict:
        body = {"campaigns": campaigns}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-update_campaign", {"body": body})

    async def delete_campaign(self, campaign_ids: list[str], account: dict = None) -> dict:
        body = {"campaignIds": campaign_ids}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-delete_campaign", {"body": body})

    async def add_country_campaign(self, campaigns: list[dict], account: dict = None) -> dict:
        """Add countries to existing SP Manual campaigns with country-specific budget caps."""
        body = {"campaigns": campaigns}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-add_country_campaign", {"body": body})

    # ── Target CRUD Methods ───────────────────────────────────────────

    async def create_target(self, targets: list[dict], account: dict = None) -> dict:
        body = {"targets": targets}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-create_target", {"body": body})

    async def update_target(self, targets: list[dict], account: dict = None) -> dict:
        body = {"targets": targets}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-update_target", {"body": body})

    async def delete_target(self, target_ids: list[str], account: dict = None) -> dict:
        body = {"targetIds": target_ids}
        if account:
            body["accessRequestedAccount"] = account
        return await self.call_tool("campaign_management-delete_target", {"body": body})

    async def create_campaign_report(
        self,
        report_config: dict,
        advertiser_account_id: Optional[str] = None,
    ) -> dict:
        """
        Create a campaign report.
        Ensures the required 'format', 'periods', and 'accessRequestedAccounts' fields.
        The MCP API requires accessRequestedAccounts with a real advertiserAccountId;
        an empty array causes a server-side serialization error.
        """
        # Normalise legacy configs that used 'adProduct' instead of 'format'/'periods'
        if "reports" in report_config:
            for r in report_config["reports"]:
                if "format" not in r:
                    r["format"] = "GZIP_JSON"
                if "periods" not in r and "dateRange" in r:
                    dr = r.pop("dateRange")
                    r["periods"] = [{"datePeriod": dr}]
                elif "periods" not in r:
                    # Default to last 30 days
                    from datetime import date, timedelta
                    end = date.today().isoformat()
                    start = (date.today() - timedelta(days=30)).isoformat()
                    r["periods"] = [{"datePeriod": {"startDate": start, "endDate": end}}]
                # Remove unsupported 'adProduct' key if present
                r.pop("adProduct", None)

        # Ensure accessRequestedAccounts has a real advertiser account ID.
        # An empty array causes "Start of structure or map found where not expected."
        if advertiser_account_id:
            report_config["accessRequestedAccounts"] = [
                {"advertiserAccountId": advertiser_account_id}
            ]
        elif not report_config.get("accessRequestedAccounts"):
            logger.warning(
                "create_campaign_report called without advertiser_account_id — "
                "report creation may fail"
            )
            report_config.setdefault("accessRequestedAccounts", [])

        return await self.call_tool("reporting-create_campaign_report", {"body": report_config})

    async def retrieve_report(self, report_ids: list[str]) -> dict:
        return await self.call_tool("reporting-retrieve_report", {"body": {"reportIds": report_ids}})

    async def retrieve_report_v3(self, report_id: str) -> dict:
        """Retrieve report status via the v3 Reporting API (direct HTTP call)."""
        import httpx

        api_base_urls = {
            "na": "https://advertising-api.amazon.com",
            "eu": "https://advertising-api-eu.amazon.com",
            "fe": "https://advertising-api-fe.amazon.com",
        }
        base_url = api_base_urls.get(self.region, api_base_urls["na"])

        headers = {
            "Amazon-Advertising-API-ClientId": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/vnd.createasyncreportrequest.v3+json",
        }
        if self.profile_id:
            headers["Amazon-Advertising-API-Scope"] = self.profile_id

        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(
                f"{base_url}/reporting/reports/{report_id}",
                headers=headers,
            )

        if resp.status_code == 200:
            data = resp.json()
            # Normalize to the same format as MCP retrieve_report
            return {"success": [{"report": data}]}
        else:
            logger.warning(f"v3 report retrieve failed: {resp.status_code} - {resp.text[:200]}")
            return {"success": [{"report": {"reportId": report_id, "status": "UNKNOWN"}}]}

    async def poll_report(self, report_ids: list[str], max_wait: int = 120, interval: int = 10) -> dict:
        """
        Poll for report completion. Amazon Ads reports are async and can take
        30-120+ seconds to complete.
        Returns the completed report data, or the last status if timed out.
        """
        import asyncio
        elapsed = 0
        last_result = {}

        while elapsed < max_wait:
            await asyncio.sleep(interval)
            elapsed += interval

            result = await self.retrieve_report(report_ids)
            last_result = result
            logger.info(f"Report poll ({elapsed}s): {self._summarize_report_status(result)}")

            # Check if report is complete
            status = self._get_report_status(result)
            if status == "COMPLETED":
                return result
            elif status in ("FAILED", "CANCELLED"):
                logger.warning(f"Report ended with status: {status}")
                return result

        logger.warning(f"Report polling timed out after {max_wait}s")
        return last_result

    @staticmethod
    def _get_report_status(result: dict) -> str:
        """Extract report status from retrieve_report response."""
        if isinstance(result, dict):
            # Format: {"success": [{"report": {"status": "COMPLETED"}}]}
            for entry in result.get("success", []):
                if isinstance(entry, dict):
                    report = entry.get("report", {})
                    if isinstance(report, dict) and "status" in report:
                        return report["status"]
        return "UNKNOWN"

    @staticmethod
    def _summarize_report_status(result: dict) -> str:
        """Short summary of report status for logging."""
        if isinstance(result, dict):
            for entry in result.get("success", []):
                if isinstance(entry, dict):
                    report = entry.get("report", {})
                    status = report.get("status", "?")
                    parts = report.get("completedReportParts")
                    return f"status={status}, parts={'yes' if parts else 'no'}"
        return str(result)[:100]

    async def create_harvest(self, harvest_requests: list[dict]) -> dict:
        return await self.call_tool("campaign_management-create_campaign_harvest_targets", {
            "body": {"harvestRequests": harvest_requests}
        })

    async def update_target_bids(self, targets: list[dict]) -> dict:
        return await self.call_tool("campaign_management-update_target_bid", {
            "body": {"targets": targets}
        })

    async def update_campaign_budget(self, campaigns: list[dict]) -> dict:
        return await self.call_tool("campaign_management-update_campaign_budget", {
            "body": {"campaigns": campaigns}
        })

    async def update_campaign_state(self, campaigns: list[dict]) -> dict:
        return await self.call_tool("campaign_management-update_campaign_state", {
            "body": {"campaigns": campaigns}
        })

    async def create_singleshot_campaign(self, campaign_data: list[dict]) -> dict:
        return await self.call_tool("campaign_management-create_singleshot_sp_campaign", {
            "body": {"oneshotCampaigns": campaign_data}
        })

    async def create_report(
        self,
        report_config: dict,
        advertiser_account_id: Optional[str] = None,
    ) -> dict:
        """
        Create a generic report using the reporting-create_report MCP tool.
        Supports all report types: spSearchTerm, sbSearchTerm, spTargeting,
        spCampaigns, spAdvertisedProduct, etc.
        """
        if advertiser_account_id:
            report_config["accessRequestedAccounts"] = [
                {"advertiserAccountId": advertiser_account_id}
            ]
        return await self.call_tool("reporting-create_report", {"body": report_config})

    async def create_search_term_report(
        self,
        start_date: str,
        end_date: str,
        ad_product: str = "SPONSORED_PRODUCTS",
        advertiser_account_id: Optional[str] = None,
        time_unit: str = "SUMMARY",
        columns: list[str] = None,
    ) -> dict:
        """
        Create a search term report for the given date range via the
        Amazon Ads v3 Reporting API (direct HTTP call, not MCP).
        
        The MCP generic reporting-create_report tool does NOT support search term
        dimensions. Search term reports must use the v3 API endpoint directly.

        Max date range: 31 days. Data retention: 95 days (SP) / 60 days (SB).
        """
        import httpx

        report_type_map = {
            "SPONSORED_PRODUCTS": "spSearchTerm",
            "SPONSORED_BRANDS": "sbSearchTerm",
        }
        report_type_id = report_type_map.get(ad_product, "spSearchTerm")

        default_columns = [
            "searchTerm",
            "impressions",
            "clicks",
            "cost",
            "purchases7d",
            "sales7d",
            "unitsSoldClicks7d",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "keywordId",
            "keyword",
            "keywordType",
            "matchType",
            "targeting",
        ]
        if time_unit == "DAILY":
            default_columns.append("date")

        body = {
            "startDate": start_date,
            "endDate": end_date,
            "configuration": {
                "adProduct": ad_product,
                "reportTypeId": report_type_id,
                "groupBy": ["searchTerm"],
                "columns": columns or default_columns,
                "timeUnit": time_unit,
                "format": "GZIP_JSON",
            },
        }

        # Region-specific API base URLs
        api_base_urls = {
            "na": "https://advertising-api.amazon.com",
            "eu": "https://advertising-api-eu.amazon.com",
            "fe": "https://advertising-api-fe.amazon.com",
        }
        base_url = api_base_urls.get(self.region, api_base_urls["na"])

        headers = {
            "Content-Type": "application/vnd.createasyncreportrequest.v3+json",
            "Amazon-Advertising-API-ClientId": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }
        if self.profile_id:
            headers["Amazon-Advertising-API-Scope"] = self.profile_id

        logger.info(f"Creating search term report via v3 API: {base_url}/reporting/reports")
        logger.info(f"Body: adProduct={ad_product}, reportTypeId={report_type_id}, "
                     f"dates={start_date} to {end_date}, columns={len(columns or default_columns)}")

        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                f"{base_url}/reporting/reports",
                json=body,
                headers=headers,
            )

        logger.info(f"Search term report API response: {resp.status_code}")

        if resp.status_code in (200, 202):
            data = resp.json()
            logger.info(f"Report created: {data}")
            # v3 API returns {"reportId": "xxx", "status": "PENDING", ...}
            return {"success": [{"report": data}]}
        else:
            error_text = resp.text[:500]
            logger.error(f"Search term report creation failed: {resp.status_code} - {error_text}")
            raise MCPError(f"Search term report API error ({resp.status_code}): {error_text}")

    async def list_invoices(self, params: dict = None) -> dict:
        body = params or {}
        return await self.call_tool("billing-list_invoices", {"body": body})

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_result(result) -> dict:
        """Parse MCP tool result into a clean dict."""
        if hasattr(result, "content"):
            content_parts = []
            for part in result.content:
                if hasattr(part, "text"):
                    content_parts.append(part.text)
                elif hasattr(part, "data"):
                    content_parts.append(part.data)
            if len(content_parts) == 1:
                # Try to parse as JSON
                import json
                try:
                    parsed = json.loads(content_parts[0])
                    # Debug: log the top-level structure of the response
                    if isinstance(parsed, dict):
                        keys = list(parsed.keys())
                        sample = {k: type(v).__name__ + (f"[{len(v)}]" if isinstance(v, list) else "") for k, v in parsed.items()}
                        logger.info(f"MCP response keys: {keys}, structure: {sample}")
                        # Log first item of any list values for structure insight
                        for k, v in parsed.items():
                            if isinstance(v, list) and v:
                                logger.info(f"MCP response['{k}'][0] keys: {list(v[0].keys()) if isinstance(v[0], dict) else type(v[0]).__name__}")
                    elif isinstance(parsed, list):
                        logger.info(f"MCP response is a list with {len(parsed)} items")
                    return parsed
                except (json.JSONDecodeError, TypeError):
                    text = content_parts[0]
                    logger.warning(f"MCP response not valid JSON: {text[:500]}")
                    # Detect validation errors and raise so callers get a clear message
                    if "Validation failed" in text or "Validation error" in text:
                        raise MCPError(f"MCP validation error: {text[:500]}")
                    return {"result": text}
            logger.info(f"MCP response has {len(content_parts)} content parts")
            return {"result": content_parts}
        return {"result": str(result)}


class MCPError(Exception):
    """Custom exception for MCP-related errors."""
    pass


def create_mcp_client(
    client_id: str,
    access_token: str,
    region: str = "na",
    profile_id: str = None,
    account_id: str = None,
) -> AmazonAdsMCP:
    """Factory function to create an MCP client instance."""
    return AmazonAdsMCP(
        client_id=client_id,
        access_token=access_token,
        region=region,
        profile_id=profile_id,
        account_id=account_id,
    )
