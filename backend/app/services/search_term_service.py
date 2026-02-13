"""
Search Term Service — Fetches, parses, and stores search term performance data
from Amazon Ads search term reports via MCP.

Search term data enables the AI assistant to answer questions like:
- Which search terms drove sales in auto campaigns?
- Which search terms have clicks but zero conversions?
- What customer queries should be harvested to manual campaigns?
"""

import gzip
import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_client import AmazonAdsMCP
from app.models import SearchTermPerformance

logger = logging.getLogger(__name__)


class SearchTermService:
    """Fetches and stores search term reports from Amazon Ads."""

    def __init__(
        self,
        client: AmazonAdsMCP,
        advertiser_account_id: Optional[str] = None,
    ):
        self.client = client
        self.advertiser_account_id = advertiser_account_id

    async def sync_search_terms(
        self,
        db: AsyncSession,
        credential_id: uuid.UUID,
        start_date: str = None,
        end_date: str = None,
        ad_product: str = "SPONSORED_PRODUCTS",
        pending_report_id: str = None,
        max_wait: int = 120,
        profile_id: Optional[str] = None,
    ) -> dict:
        """
        Full sync pipeline:
        1. Create (or resume) a search term report via MCP
        2. Poll for completion
        3. Download and parse GZIP_JSON data
        4. Store rows in SearchTermPerformance table

        Returns:
            dict with status, rows_stored, or _pending_report_id if still processing.
        """
        # Default to last 30 days
        if not end_date:
            end_date = date.today().isoformat()
        if not start_date:
            start_date = (date.today() - timedelta(days=30)).isoformat()

        try:
            report_ids = []

            # Phase 1: Resume a pending report if provided
            if pending_report_id:
                logger.info(f"Resuming pending search term report: {pending_report_id}")
                try:
                    check = await self.client.retrieve_report_v3(pending_report_id)
                    status = self.client._get_report_status(check)
                    if status == "COMPLETED":
                        rows = await self._download_report_data(check)
                        if rows is not None:
                            try:
                                stored = await self._store_rows(
                                    db, credential_id, rows, start_date, end_date, ad_product, profile_id
                                )
                                return {"status": "completed", "rows_stored": stored}
                            except Exception as store_err:
                                logger.error(f"Failed to store search term rows: {store_err}")
                                await db.rollback()
                                return {"status": "error", "message": f"Downloaded {len(rows)} rows but failed to store: {str(store_err)[:200]}"}
                    elif status in ("PENDING", "PROCESSING"):
                        report_ids = [pending_report_id]
                except Exception as e:
                    logger.warning(f"Failed to check pending report: {e}")
                    try:
                        await db.rollback()
                    except Exception:
                        pass

            # Phase 2: Create a new report via the v3 API (direct HTTP, not MCP)
            if not report_ids:
                logger.info(f"Creating search term report: {start_date} to {end_date} ({ad_product})")
                result = await self.client.create_search_term_report(
                    start_date=start_date,
                    end_date=end_date,
                    ad_product=ad_product,
                    advertiser_account_id=self.advertiser_account_id,
                    time_unit="SUMMARY",
                )
                logger.info(f"Search term report create response: {json.dumps(result, default=str)[:1000]}")
                report_ids = self._extract_report_ids(result)
                logger.info(f"Search term report IDs: {report_ids}")

            if not report_ids:
                return {"status": "error", "message": "No report ID returned from Amazon Ads"}

            # Phase 3: Poll for completion using v3 API
            report_id = report_ids[0]
            completed = await self._poll_report_v3(report_id, max_wait, interval=10)

            status = self.client._get_report_status(completed)
            if status == "COMPLETED":
                rows = await self._download_report_data(completed)
                if rows is not None:
                    try:
                        stored = await self._store_rows(
                            db, credential_id, rows, start_date, end_date, ad_product, profile_id
                        )
                        return {"status": "completed", "rows_stored": stored}
                    except Exception as store_err:
                        logger.error(f"Failed to store search term rows: {store_err}")
                        await db.rollback()
                        return {"status": "error", "message": f"Downloaded {len(rows)} rows but failed to store: {str(store_err)[:200]}"}
                return {"status": "completed", "rows_stored": 0, "message": "Report completed but no data"}

            # Still pending — return ID for later
            logger.info(f"Search term report still {status} after polling")
            return {
                "status": "pending",
                "_pending_report_id": report_id,
                "message": f"Report still processing ({status}). Retry with the pending ID.",
            }

        except Exception as e:
            logger.error(f"Search term sync failed: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            return {"status": "error", "message": str(e)}

    async def _poll_report_v3(
        self, report_id: str, max_wait: int = 120, interval: int = 10
    ) -> dict:
        """Poll for search term report completion using v3 API."""
        import asyncio
        elapsed = 0
        last_result = {}

        while elapsed < max_wait:
            await asyncio.sleep(interval)
            elapsed += interval

            result = await self.client.retrieve_report_v3(report_id)
            last_result = result
            status = self.client._get_report_status(result)
            logger.info(f"Search term report poll ({elapsed}s): status={status}")

            if status == "COMPLETED":
                return result
            elif status in ("FAILED", "CANCELLED"):
                logger.warning(f"Search term report ended with status: {status}")
                return result

        logger.warning(f"Search term report polling timed out after {max_wait}s")
        return last_result

    async def _store_rows(
        self,
        db: AsyncSession,
        credential_id: uuid.UUID,
        rows: list[dict],
        start_date: str,
        end_date: str,
        ad_product: str,
        profile_id: Optional[str] = None,
    ) -> int:
        """Parse and upsert search term rows into the database."""
        # Clear existing data for this credential + profile + date range + ad product
        # to avoid duplicates on re-sync
        delete_conds = [
            SearchTermPerformance.credential_id == credential_id,
            SearchTermPerformance.ad_product == ad_product,
            SearchTermPerformance.report_date_start == start_date,
            SearchTermPerformance.report_date_end == end_date,
        ]
        if profile_id is not None:
            delete_conds.append(SearchTermPerformance.profile_id == profile_id)
        else:
            delete_conds.append(SearchTermPerformance.profile_id.is_(None))
        await db.execute(delete(SearchTermPerformance).where(and_(*delete_conds)))

        stored = 0
        for row in rows:
            if not isinstance(row, dict):
                continue

            search_term = row.get("searchTerm") or row.get("search_term") or ""
            if not search_term:
                continue

            cost = float(row.get("cost") or row.get("spend") or 0)
            clicks = int(row.get("clicks") or 0)
            impressions = int(row.get("impressions") or 0)
            purchases = int(
                row.get("purchases7d") or row.get("purchases14d")
                or row.get("purchases") or row.get("orders") or 0
            )
            sales = float(
                row.get("sales7d") or row.get("sales14d")
                or row.get("sales") or 0
            )
            units = int(
                row.get("unitsSoldClicks7d") or row.get("unitsSoldClicks14d")
                or row.get("units_sold") or 0
            )

            acos = round(cost / sales * 100, 2) if sales > 0 else None
            roas = round(sales / cost, 2) if cost > 0 else None
            ctr = round(clicks / impressions * 100, 2) if impressions > 0 else None
            cpc = round(cost / clicks, 2) if clicks > 0 else None

            # Amazon returns IDs as integers; model expects strings
            def _str(val):
                return str(val) if val is not None else None

            stp = SearchTermPerformance(
                credential_id=credential_id,
                profile_id=profile_id,
                search_term=search_term,
                keyword=row.get("keyword") or row.get("targeting"),
                keyword_id=_str(row.get("keywordId") or row.get("keyword_id")),
                keyword_type=row.get("keywordType") or row.get("keyword_type"),
                match_type=row.get("matchType") or row.get("match_type"),
                targeting=row.get("targeting"),
                amazon_campaign_id=_str(row.get("campaignId") or row.get("campaign_id")),
                campaign_name=row.get("campaignName") or row.get("campaign_name"),
                amazon_ad_group_id=_str(row.get("adGroupId") or row.get("ad_group_id")),
                ad_group_name=row.get("adGroupName") or row.get("ad_group_name"),
                date=row.get("date") or "SUMMARY",
                impressions=impressions,
                clicks=clicks,
                cost=cost,
                ctr=ctr,
                cpc=cpc,
                purchases=purchases,
                sales=sales,
                units_sold=units,
                acos=acos,
                roas=roas,
                ad_product=ad_product,
                report_date_start=start_date,
                report_date_end=end_date,
            )
            db.add(stp)
            stored += 1

        await db.flush()
        logger.info(f"Stored {stored} search term rows for {start_date}–{end_date}")
        return stored

    @staticmethod
    async def _download_report_data(report_response: dict) -> Optional[list]:
        """Download and decompress report data from completed report S3 URLs."""
        all_rows = []
        if not isinstance(report_response, dict):
            return all_rows

        for entry in report_response.get("success", []):
            if not isinstance(entry, dict):
                continue
            report = entry.get("report", {})
            if not isinstance(report, dict):
                continue
            if report.get("status") != "COMPLETED":
                continue

            # Collect download URLs — v3 API uses 'url' at report level,
            # MCP uses 'completedReportParts' array
            urls = []
            if report.get("url"):
                urls.append(report["url"])
            for part in report.get("completedReportParts", []):
                if isinstance(part, dict) and part.get("url"):
                    urls.append(part["url"])

            for url in urls:
                try:
                    async with httpx.AsyncClient(timeout=60.0) as http:
                        resp = await http.get(url)
                        resp.raise_for_status()

                    # Decompress
                    try:
                        data = gzip.decompress(resp.content)
                        text = data.decode("utf-8")
                    except Exception:
                        text = resp.content.decode("utf-8")

                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        all_rows.extend(parsed)
                    elif isinstance(parsed, dict):
                        for key in ("rows", "data", "searchTerms", "results"):
                            if key in parsed and isinstance(parsed[key], list):
                                all_rows.extend(parsed[key])
                                break
                        else:
                            all_rows.append(parsed)

                    logger.info(f"Downloaded search term report part: {len(all_rows)} rows so far")
                except Exception as e:
                    logger.warning(f"Failed to download report part: {e}")

        return all_rows

    @staticmethod
    def _extract_report_ids(result: dict) -> list:
        """Extract report IDs from MCP response."""
        if not isinstance(result, dict):
            return []

        # Format: {"success": [{"report": {"reportId": "..."}}]}
        for entry in result.get("success", []):
            if isinstance(entry, dict):
                report = entry.get("report", {})
                if isinstance(report, dict) and "reportId" in report:
                    return [report["reportId"]]

        if "reportIds" in result:
            return result["reportIds"]
        if "reports" in result and isinstance(result["reports"], list):
            ids = [
                r["reportId"] for r in result["reports"]
                if isinstance(r, dict) and "reportId" in r
            ]
            if ids:
                return ids
        if "reportId" in result:
            return [result["reportId"]]

        return []


async def get_search_term_summary(
    db: AsyncSession,
    credential_id: uuid.UUID,
    min_clicks: int = 0,
    max_results: int = 100,
    profile_id: Optional[str] = None,
) -> dict:
    """
    Query stored search term data for the AI context.
    Returns categorized search terms: top by sales, non-converting, high ACOS, etc.
    Filters by profile_id when provided (multi-account credentials).
    """
    q = select(SearchTermPerformance).where(SearchTermPerformance.credential_id == credential_id)
    if profile_id is not None:
        q = q.where(SearchTermPerformance.profile_id == profile_id)
    else:
        q = q.where(SearchTermPerformance.profile_id.is_(None))
    result = await db.execute(q)
    all_terms = result.scalars().all()

    if not all_terms:
        return {"total": 0, "has_data": False}

    # Categorize
    with_sales = [t for t in all_terms if (t.purchases or 0) > 0]
    non_converting = [t for t in all_terms if (t.clicks or 0) > 0 and (t.purchases or 0) == 0]
    high_acos = [t for t in all_terms if (t.acos or 0) > 50 and (t.cost or 0) > 0]

    def _term_dict(t):
        return {
            "search_term": t.search_term,
            "keyword": t.keyword,
            "match_type": t.match_type,
            "keyword_type": t.keyword_type,
            "campaign_name": t.campaign_name,
            "ad_group_name": t.ad_group_name,
            "impressions": t.impressions or 0,
            "clicks": t.clicks or 0,
            "cost": t.cost or 0,
            "purchases": t.purchases or 0,
            "sales": t.sales or 0,
            "acos": t.acos,
            "roas": t.roas,
            "ctr": t.ctr,
            "cpc": t.cpc,
        }

    # Sort by relevant metrics
    top_by_sales = sorted(with_sales, key=lambda t: t.sales or 0, reverse=True)[:max_results]
    top_non_converting = sorted(non_converting, key=lambda t: t.cost or 0, reverse=True)[:max_results]
    top_high_acos = sorted(high_acos, key=lambda t: t.cost or 0, reverse=True)[:50]
    top_by_clicks = sorted(all_terms, key=lambda t: t.clicks or 0, reverse=True)[:50]

    # Date range info
    dates = [t.report_date_start for t in all_terms if t.report_date_start]
    date_range_start = min(dates) if dates else None
    dates_end = [t.report_date_end for t in all_terms if t.report_date_end]
    date_range_end = max(dates_end) if dates_end else None

    total_cost = sum(t.cost or 0 for t in all_terms)
    total_sales = sum(t.sales or 0 for t in all_terms)
    total_clicks = sum(t.clicks or 0 for t in all_terms)
    total_purchases = sum(t.purchases or 0 for t in all_terms)

    return {
        "has_data": True,
        "total": len(all_terms),
        "date_range": f"{date_range_start} to {date_range_end}" if date_range_start else None,
        "summary": {
            "total_search_terms": len(all_terms),
            "with_sales": len(with_sales),
            "non_converting": len(non_converting),
            "high_acos_count": len(high_acos),
            "total_cost": total_cost,
            "total_sales": total_sales,
            "total_clicks": total_clicks,
            "total_purchases": total_purchases,
        },
        "top_by_sales": [_term_dict(t) for t in top_by_sales],
        "top_non_converting": [_term_dict(t) for t in top_non_converting],
        "top_high_acos": [_term_dict(t) for t in top_high_acos],
        "top_by_clicks": [_term_dict(t) for t in top_by_clicks],
    }
