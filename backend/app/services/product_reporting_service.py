"""
Product Reporting Service — Fetches, parses, and stores product/business
report data from Amazon Ads.
"""

import gzip
import json
import logging
import uuid
from datetime import date, timedelta
from typing import Optional

import httpx
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp_client import AmazonAdsMCP
from app.models import ProductPerformanceDaily
from app.utils import marketplace_today, normalize_amazon_date

logger = logging.getLogger(__name__)


def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _resolve_product_id(row: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    asin = (
        row.get("advertisedAsin")
        or row.get("asin")
        or row.get("childAsin")
        or row.get("productAsin")
        or row.get("productId")
    )
    sku = row.get("advertisedSku") or row.get("sku") or row.get("sellerSku")
    name = (
        row.get("advertisedProductTitle")
        or row.get("productName")
        or row.get("itemName")
        or row.get("name")
    )
    asin = str(asin).strip() if asin else None
    sku = str(sku).strip() if sku else None
    name = str(name).strip() if name else None
    return asin, sku, name


def _derive_metrics(spend: float, sales: float, impressions: int, clicks: int, orders: int) -> dict:
    ctr = (clicks / impressions * 100) if impressions > 0 else None
    cpc = (spend / clicks) if clicks > 0 else None
    acos = (spend / sales * 100) if sales > 0 else None
    roas = (sales / spend) if spend > 0 else None
    cvr = (orders / clicks * 100) if clicks > 0 else None
    return {
        "ctr": round(ctr, 2) if ctr is not None else None,
        "cpc": round(cpc, 2) if cpc is not None else None,
        "acos": round(acos, 2) if acos is not None else None,
        "roas": round(roas, 2) if roas is not None else None,
        "cvr": round(cvr, 2) if cvr is not None else None,
    }


class ProductReportingService:
    """Fetches and stores product-level report data from Amazon Ads."""

    def __init__(
        self,
        client: AmazonAdsMCP,
        advertiser_account_id: Optional[str] = None,
        marketplace: Optional[str] = None,
    ):
        self.client = client
        self.advertiser_account_id = advertiser_account_id
        self.marketplace = marketplace
        if advertiser_account_id:
            self.client.set_advertiser_account_id(advertiser_account_id)

    async def sync_products(
        self,
        db: AsyncSession,
        credential_id: uuid.UUID,
        start_date: str = None,
        end_date: str = None,
        ad_product: str = "SPONSORED_PRODUCTS",
        pending_report_id: str = None,
        max_wait: int = 180,
        profile_id: Optional[str] = None,
    ) -> dict:
        """
        Create/resume product report, poll, download, and store product rows.
        Returns pending report ID if still processing.
        """
        if not end_date:
            end_date = marketplace_today(self.marketplace, self.client.region).isoformat()
        if not start_date:
            start_date = (
                marketplace_today(self.marketplace, self.client.region) - timedelta(days=30)
            ).isoformat()

        try:
            report_ids = []

            if pending_report_id:
                logger.info("Resuming pending product report: %s", pending_report_id)
                try:
                    check = await self.client.retrieve_report_v3(pending_report_id)
                    status = self.client._get_report_status(check)
                    if status == "COMPLETED":
                        rows = await self._download_report_data(check)
                        stored = await self._store_rows(
                            db, credential_id, rows, start_date, end_date, ad_product, profile_id
                        )
                        return {"status": "completed", "rows_stored": stored}
                    if status in ("PENDING", "PROCESSING"):
                        report_ids = [pending_report_id]
                except Exception as e:
                    logger.warning("Failed to check pending product report: %s", e)

            if not report_ids:
                result = await self.client.create_advertised_product_report(
                    start_date=start_date,
                    end_date=end_date,
                    ad_product=ad_product,
                    advertiser_account_id=self.advertiser_account_id,
                    time_unit="DAILY",
                )
                report_ids = self._extract_report_ids(result)

            if not report_ids:
                return {"status": "error", "message": "No report ID returned from Amazon Ads"}

            report_id = report_ids[0]
            completed = await self._poll_report_v3(report_id, max_wait=max_wait, interval=10)
            status = self.client._get_report_status(completed)
            if status == "COMPLETED":
                rows = await self._download_report_data(completed)
                stored = await self._store_rows(
                    db, credential_id, rows, start_date, end_date, ad_product, profile_id
                )
                return {"status": "completed", "rows_stored": stored}

            return {
                "status": "pending",
                "_pending_report_id": report_id,
                "message": f"Product report still processing ({status}).",
            }
        except Exception as e:
            logger.error("Product report sync failed: %s", e)
            try:
                await db.rollback()
            except Exception:
                pass
            return {"status": "error", "message": str(e)}

    async def _poll_report_v3(self, report_id: str, max_wait: int = 180, interval: int = 10) -> dict:
        import asyncio

        elapsed = 0
        last_result = {}
        while elapsed < max_wait:
            await asyncio.sleep(interval)
            elapsed += interval
            result = await self.client.retrieve_report_v3(report_id)
            last_result = result
            status = self.client._get_report_status(result)
            logger.info("Product report poll (%ss): status=%s", elapsed, status)
            if status == "COMPLETED":
                return result
            if status in ("FAILED", "CANCELLED"):
                return result
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
        # Replace the selected date range to keep sync idempotent.
        delete_conds = [
            ProductPerformanceDaily.credential_id == credential_id,
            ProductPerformanceDaily.ad_product == ad_product,
            ProductPerformanceDaily.date >= start_date,
            ProductPerformanceDaily.date <= end_date,
        ]
        if profile_id is not None:
            delete_conds.append(ProductPerformanceDaily.profile_id == profile_id)
        else:
            delete_conds.append(ProductPerformanceDaily.profile_id.is_(None))
        await db.execute(delete(ProductPerformanceDaily).where(and_(*delete_conds)))

        aggregates: dict[tuple[str, str, str, str], dict] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            asin, sku, name = _resolve_product_id(row)
            if not asin and not sku:
                continue

            row_date = normalize_amazon_date(
                row.get("date")
                or row.get("reportDate")
                or row.get("report_date")
                or row.get("day")
            ) or end_date
            if row_date < start_date or row_date > end_date:
                continue

            spend = _to_float(row.get("cost") or row.get("spend") or row.get("metric.totalCost"))
            sales = _to_float(
                row.get("sales7d")
                or row.get("sales14d")
                or row.get("sales")
                or row.get("attributedSales14d")
                or row.get("metric.sales")
            )
            impressions = _to_int(row.get("impressions") or row.get("metric.impressions"))
            clicks = _to_int(row.get("clicks") or row.get("metric.clicks"))
            orders = _to_int(
                row.get("purchases7d")
                or row.get("purchases14d")
                or row.get("orders")
                or row.get("attributedConversions14d")
                or row.get("metric.purchases")
            )
            units_sold = _to_int(
                row.get("unitsSoldClicks7d")
                or row.get("unitsSoldClicks14d")
                or row.get("unitsSold")
                or row.get("units_sold")
            )

            key = (row_date, asin or "", sku or "", ad_product)
            bucket = aggregates.get(key)
            if not bucket:
                bucket = {
                    "date": row_date,
                    "asin": asin,
                    "sku": sku,
                    "product_name": name,
                    "impressions": 0,
                    "clicks": 0,
                    "spend": 0.0,
                    "sales": 0.0,
                    "orders": 0,
                    "units_sold": 0,
                    "raw_data": row,
                }
                aggregates[key] = bucket

            bucket["impressions"] += impressions
            bucket["clicks"] += clicks
            bucket["spend"] += spend
            bucket["sales"] += sales
            bucket["orders"] += orders
            bucket["units_sold"] += units_sold
            if not bucket["product_name"] and name:
                bucket["product_name"] = name

        stored = 0
        for item in aggregates.values():
            derived = _derive_metrics(
                spend=item["spend"],
                sales=item["sales"],
                impressions=item["impressions"],
                clicks=item["clicks"],
                orders=item["orders"],
            )
            db.add(ProductPerformanceDaily(
                credential_id=credential_id,
                profile_id=profile_id,
                date=item["date"],
                asin=item["asin"],
                sku=item["sku"],
                product_name=item["product_name"],
                ad_product=ad_product,
                impressions=item["impressions"],
                clicks=item["clicks"],
                spend=round(item["spend"], 2),
                sales=round(item["sales"], 2),
                orders=item["orders"],
                units_sold=item["units_sold"],
                ctr=derived["ctr"],
                cpc=derived["cpc"],
                acos=derived["acos"],
                roas=derived["roas"],
                cvr=derived["cvr"],
                report_date_start=start_date,
                report_date_end=end_date,
                source="product_report",
                raw_data=item["raw_data"],
            ))
            stored += 1

        await db.flush()
        logger.info("Stored %d product rows for %s–%s", stored, start_date, end_date)
        return stored

    @staticmethod
    async def _download_report_data(report_response: dict) -> list[dict]:
        rows: list[dict] = []
        if not isinstance(report_response, dict):
            return rows

        for entry in report_response.get("success", []):
            if not isinstance(entry, dict):
                continue
            report = entry.get("report", {})
            if not isinstance(report, dict):
                continue
            if report.get("status") != "COMPLETED":
                continue

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
                    try:
                        text = gzip.decompress(resp.content).decode("utf-8")
                    except Exception:
                        text = resp.content.decode("utf-8")
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        rows.extend(parsed)
                    elif isinstance(parsed, dict):
                        for key in ("rows", "data", "results", "products"):
                            if isinstance(parsed.get(key), list):
                                rows.extend(parsed[key])
                                break
                        else:
                            rows.append(parsed)
                except Exception as e:
                    logger.warning("Failed to download product report part: %s", e)

        return rows

    @staticmethod
    def _extract_report_ids(result: dict) -> list:
        if not isinstance(result, dict):
            return []
        for entry in result.get("success", []):
            if isinstance(entry, dict):
                report = entry.get("report", {})
                if isinstance(report, dict) and "reportId" in report:
                    return [report["reportId"]]
        if "reportIds" in result and isinstance(result["reportIds"], list):
            return result["reportIds"]
        if "reportId" in result:
            return [result["reportId"]]
        return []


async def query_product_rows(
    db: AsyncSession,
    credential_id: uuid.UUID,
    start_date: str,
    end_date: str,
    profile_id: Optional[str] = None,
    limit: int = 100,
    sort_by: str = "sales",
) -> list[dict]:
    base_where = [
        ProductPerformanceDaily.credential_id == credential_id,
        ProductPerformanceDaily.date >= start_date,
        ProductPerformanceDaily.date <= end_date,
    ]
    if profile_id is not None:
        base_where.append(ProductPerformanceDaily.profile_id == profile_id)
    else:
        base_where.append(ProductPerformanceDaily.profile_id.is_(None))

    spend_sum = func.coalesce(func.sum(ProductPerformanceDaily.spend), 0.0).label("spend")
    sales_sum = func.coalesce(func.sum(ProductPerformanceDaily.sales), 0.0).label("sales")
    impr_sum = func.coalesce(func.sum(ProductPerformanceDaily.impressions), 0).label("impressions")
    clicks_sum = func.coalesce(func.sum(ProductPerformanceDaily.clicks), 0).label("clicks")
    orders_sum = func.coalesce(func.sum(ProductPerformanceDaily.orders), 0).label("orders")
    units_sum = func.coalesce(func.sum(ProductPerformanceDaily.units_sold), 0).label("units_sold")

    query = (
        select(
            ProductPerformanceDaily.asin.label("asin"),
            ProductPerformanceDaily.sku.label("sku"),
            func.max(ProductPerformanceDaily.product_name).label("product_name"),
            spend_sum,
            sales_sum,
            impr_sum,
            clicks_sum,
            orders_sum,
            units_sum,
        )
        .where(and_(*base_where))
        .group_by(ProductPerformanceDaily.asin, ProductPerformanceDaily.sku)
    )

    sort_key = (sort_by or "sales").lower()
    if sort_key == "spend":
        query = query.order_by(spend_sum.desc())
    elif sort_key == "orders":
        query = query.order_by(orders_sum.desc())
    elif sort_key == "clicks":
        query = query.order_by(clicks_sum.desc())
    else:
        query = query.order_by(sales_sum.desc())

    if limit and limit > 0:
        query = query.limit(limit)

    result = await db.execute(query)
    rows = []
    for r in result.all():
        spend = float(r.spend or 0)
        sales = float(r.sales or 0)
        impressions = int(r.impressions or 0)
        clicks = int(r.clicks or 0)
        orders = int(r.orders or 0)
        derived = _derive_metrics(spend, sales, impressions, clicks, orders)
        rows.append({
            "asin": r.asin,
            "sku": r.sku,
            "product_name": r.product_name or (f"ASIN {r.asin}" if r.asin else (f"SKU {r.sku}" if r.sku else "Unknown Product")),
            "impressions": impressions,
            "clicks": clicks,
            "spend": round(spend, 2),
            "sales": round(sales, 2),
            "orders": orders,
            "units_sold": int(r.units_sold or 0),
            "ctr": derived["ctr"],
            "cpc": derived["cpc"],
            "acos": derived["acos"],
            "roas": derived["roas"],
            "cvr": derived["cvr"],
        })
    return rows


async def get_product_summary(
    db: AsyncSession,
    credential_id: uuid.UUID,
    start_date: str,
    end_date: str,
    profile_id: Optional[str] = None,
) -> dict:
    where = [
        ProductPerformanceDaily.credential_id == credential_id,
        ProductPerformanceDaily.date >= start_date,
        ProductPerformanceDaily.date <= end_date,
    ]
    if profile_id is not None:
        where.append(ProductPerformanceDaily.profile_id == profile_id)
    else:
        where.append(ProductPerformanceDaily.profile_id.is_(None))

    totals_q = await db.execute(
        select(
            func.coalesce(func.sum(ProductPerformanceDaily.spend), 0.0).label("spend"),
            func.coalesce(func.sum(ProductPerformanceDaily.sales), 0.0).label("sales"),
            func.coalesce(func.sum(ProductPerformanceDaily.impressions), 0).label("impressions"),
            func.coalesce(func.sum(ProductPerformanceDaily.clicks), 0).label("clicks"),
            func.coalesce(func.sum(ProductPerformanceDaily.orders), 0).label("orders"),
            func.coalesce(func.sum(ProductPerformanceDaily.units_sold), 0).label("units_sold"),
        ).where(and_(*where))
    )
    totals = totals_q.one()

    count_q = await db.execute(
        select(func.count())
        .select_from(
            select(
                ProductPerformanceDaily.asin,
                ProductPerformanceDaily.sku,
            )
            .where(and_(*where))
            .group_by(ProductPerformanceDaily.asin, ProductPerformanceDaily.sku)
            .subquery()
        )
    )
    product_count = int(count_q.scalar() or 0)

    spend = float(totals.spend or 0)
    sales = float(totals.sales or 0)
    impressions = int(totals.impressions or 0)
    clicks = int(totals.clicks or 0)
    orders = int(totals.orders or 0)
    units_sold = int(totals.units_sold or 0)
    derived = _derive_metrics(spend, sales, impressions, clicks, orders)

    top_products = await query_product_rows(
        db,
        credential_id=credential_id,
        start_date=start_date,
        end_date=end_date,
        profile_id=profile_id,
        limit=10,
        sort_by="sales",
    )

    return {
        "has_data": product_count > 0,
        "summary": {
            "spend": round(spend, 2),
            "sales": round(sales, 2),
            "impressions": impressions,
            "clicks": clicks,
            "orders": orders,
            "units_sold": units_sold,
            "acos": derived["acos"] or 0,
            "roas": derived["roas"] or 0,
            "ctr": derived["ctr"] or 0,
            "cpc": derived["cpc"] or 0,
            "cvr": derived["cvr"] or 0,
            "total_products": product_count,
        },
        "top_products": top_products,
        "date_range": f"{start_date} to {end_date}",
    }
