"""
Product Image Service — Fetch product images by ASIN.
Uses Amazon Product Advertising API (PA-API) when configured.
Falls back to raw_data extraction and ASIN-based URL pattern.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Amazon product image URL pattern (US marketplace) — works for many products
# Format: https://images-na.ssl-images-amazon.com/images/P/{ASIN}.jpg
ASIN_IMAGE_URL_TEMPLATE = "https://images-na.ssl-images-amazon.com/images/P/{asin}.jpg"


def _extract_from_raw_data(raw: dict) -> Optional[str]:
    """Extract product/creative image URL from MCP raw_data if present."""
    if not raw:
        return None
    for path in (
        ("creative", "primaryImage", "url"),
        ("creative", "images", 0, "url"),
        ("creative", "imageUrl"),
        ("landingPage", "url"),
    ):
        try:
            obj = raw
            for key in path:
                obj = obj[key] if isinstance(obj, (dict, list)) else None
                if obj is None:
                    break
            if isinstance(obj, str) and obj.startswith("http"):
                return obj
        except (KeyError, IndexError, TypeError):
            continue
    return None


def _asin_fallback_url(asin: str) -> str:
    """Return ASIN-based image URL (may not work for all products)."""
    return ASIN_IMAGE_URL_TEMPLATE.format(asin=asin)


def _fetch_paapi_image(access_key: str, secret_key: str, partner_tag: str, asin: str) -> Optional[str]:
    """Fetch product image URL via PA-API (sync, run in thread). Requires: pip install python-amazon-paapi"""
    try:
        try:
            from amazon_paapi import AmazonApi
            from amazon_paapi.models import Country
        except ImportError:
            logger.debug("python-amazon-paapi not installed; PA-API image fetch skipped")
            return None

        api = AmazonApi(
            key=access_key,
            secret=secret_key,
            tag=partner_tag,
            country=Country.US,
        )
        items = api.get_items(items=[asin])
        if items and len(items) > 0:
            item = items[0]
            if hasattr(item, "images") and item.images:
                primary = getattr(item.images, "primary", None)
                if primary:
                    large = getattr(primary, "large", None) or getattr(primary, "medium", None) or getattr(primary, "small", None)
                    if large and hasattr(large, "url"):
                        return large.url
    except Exception as e:
        logger.debug(f"PA-API image fetch failed for {asin}: {e}")
    return None


async def get_product_image_url(
    asin: Optional[str],
    raw_data: Optional[dict] = None,
    paapi_access_key: Optional[str] = None,
    paapi_secret_key: Optional[str] = None,
    paapi_partner_tag: Optional[str] = None,
) -> Optional[str]:
    """
    Get product image URL for an ASIN.
    Priority: 1) raw_data extraction, 2) PA-API if configured, 3) ASIN fallback URL.
    """
    # 1. Try raw_data first
    if raw_data:
        url = _extract_from_raw_data(raw_data)
        if url:
            return url

    if not asin:
        return None

    # 2. Try PA-API if configured
    if paapi_access_key and paapi_secret_key and paapi_partner_tag:
        try:
            url = await asyncio.to_thread(
                _fetch_paapi_image,
                paapi_access_key,
                paapi_secret_key,
                paapi_partner_tag,
                asin,
            )
            if url:
                return url
        except Exception as e:
            logger.debug(f"PA-API async fetch failed: {e}")

    # 3. Fallback to ASIN-based URL (works for many products)
    return _asin_fallback_url(asin)
