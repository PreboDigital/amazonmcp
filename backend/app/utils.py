"""
Shared utility functions.
"""

import logging
from typing import Optional
import uuid as uuid_mod
from datetime import datetime, timezone
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def parse_uuid(value: str, field_name: str = "id") -> uuid_mod.UUID:
    """
    Parse a string as UUID, raising a 400 HTTPException on invalid input
    instead of letting a bare ValueError bubble up as a 500.
    """
    try:
        return uuid_mod.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid UUID for '{field_name}': {value!r}",
        )


def safe_error_detail(exc: Exception, fallback: str = "An internal error occurred. Please try again later.") -> str:
    """
    Return a sanitized error message safe for client consumption.
    Logs the real exception detail server-side.
    """
    logger.error(f"Operation failed: {exc}", exc_info=True)
    return fallback


def utcnow() -> datetime:
    """
    Return the current UTC time as a naive datetime (no tzinfo).
    Replaces the deprecated ``datetime.utcnow()``.
    Naive datetimes are used because our DB columns are TIMESTAMP WITHOUT TIME ZONE.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def extract_target_expression(tgt_data: dict) -> Optional[str]:
    """
    Extract human-readable keyword/expression from Amazon Ads target data.
    Handles: keywordText (SP), targetDetails.keyword, expression (product targets).
    """
    target_details = tgt_data.get("targetDetails") or {}
    expr = (
        tgt_data.get("keywordText")
        or tgt_data.get("expression")
        or tgt_data.get("keyword")
        or target_details.get("keyword")
        or target_details.get("expression")
    )
    if expr and isinstance(expr, str):
        return expr
    if isinstance(expr, list) and expr:
        return str(expr[0]) if len(expr) == 1 else " | ".join(str(x) for x in expr)
    expressions = tgt_data.get("expressions") or tgt_data.get("expression")
    if isinstance(expressions, list):
        parts = []
        for ex in expressions:
            if isinstance(ex, dict):
                val = ex.get("value") or ex.get("targeting")
                if isinstance(val, dict):
                    val = val.get("value")
                if val:
                    parts.append(str(val))
            elif ex:
                parts.append(str(ex))
        if parts:
            return " | ".join(parts)
    resolved = target_details.get("resolvedExpression") or target_details.get("productCategoryResolved") or target_details.get("productBrandResolved")
    if resolved:
        return str(resolved)
    return None


def extract_ad_asin_sku(ad_data: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Extract ASIN and SKU from Amazon Ads ad data.
    Handles: creative.products[].productIdType + productId, flat asin/sku.
    """
    asin = ad_data.get("asin")
    sku = ad_data.get("sku")
    creative = ad_data.get("creative") or {}
    products = creative.get("products") or creative.get("product") or []
    if isinstance(products, dict):
        products = [products]
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = p.get("productId")
        ptype = (p.get("productIdType") or "").upper()
        if pid:
            if ptype == "ASIN" or (not ptype and len(pid) == 10 and pid.isalnum()):
                asin = asin or pid
            elif ptype == "SKU":
                sku = sku or pid
            elif not asin and not sku:
                asin = pid
    return (asin, sku)
