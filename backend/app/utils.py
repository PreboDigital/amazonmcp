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


def _is_keyword_like(s: str) -> bool:
    """True if string looks like keyword text, not an ID."""
    if not s or not isinstance(s, str) or len(s) > 200:
        return False
    if s.isdigit() or (len(s) > 10 and s.replace("-", "").replace("_", "").isdigit()):
        return False
    return True


def extract_target_expression(tgt_data: dict) -> Optional[str]:
    """
    Extract human-readable keyword/expression from Amazon Ads target data.
    MCP returns targetDetails with type-specific keys: keywordTarget, themeTarget, productTarget, etc.
    """
    if not tgt_data or not isinstance(tgt_data, dict):
        return None
    if "target" in tgt_data and isinstance(tgt_data["target"], dict):
        tgt_data = tgt_data["target"]
    target_details = tgt_data.get("targetDetails") or {}
    tgt_type = (tgt_data.get("targetType") or "").upper()

    # Direct fields
    for key in ("keywordText", "keyword", "expression", "text", "value", "targetingClause"):
        val = tgt_data.get(key) or target_details.get(key)
        if val and isinstance(val, str) and _is_keyword_like(val):
            return val

    # MCP targetDetails: keywordTarget, themeTarget, productTarget, productCategoryTarget, etc.
    for detail_key, detail_val in target_details.items():
        if not isinstance(detail_val, dict):
            continue
        for k in ("keyword", "expression", "value", "theme", "asin", "productCategoryId"):
            v = detail_val.get(k)
            if v and isinstance(v, str) and _is_keyword_like(v):
                return v
        # themeTarget has matchType (e.g. KEYWORDS_CLOSE_MATCH) — use as fallback label
        if detail_key == "themeTarget" and detail_val.get("matchType"):
            return f"Theme: {detail_val['matchType']}"
        # productTarget / category may have resolvedExpression
        resolved = detail_val.get("resolvedExpression") or detail_val.get("productCategoryResolved")
        if resolved and isinstance(resolved, str):
            return resolved

    # Nested keywordTarget.keyword, productTarget.asin
    for nest in ("keywordTarget", "productTarget", "targeting"):
        nested = target_details.get(nest)
        if isinstance(nested, dict):
            for k in ("keyword", "expression", "value", "asin"):
                v = nested.get(k)
                if v and isinstance(v, str) and _is_keyword_like(v):
                    return v

    # expression/expressions arrays
    expressions = tgt_data.get("expressions") or tgt_data.get("expression") or target_details.get("expression")
    if isinstance(expressions, list):
        parts = []
        for ex in expressions:
            if isinstance(ex, dict):
                val = ex.get("value") or ex.get("targeting")
                if isinstance(val, dict):
                    val = val.get("value")
                if val and _is_keyword_like(str(val)):
                    parts.append(str(val))
            elif ex and _is_keyword_like(str(ex)):
                parts.append(str(ex))
        if parts:
            return " | ".join(parts)
    expr = tgt_data.get("expression") or target_details.get("expression")
    if isinstance(expr, list) and expr:
        return str(expr[0]) if len(expr) == 1 else " | ".join(str(x) for x in expr)

    # Resolved human-readable
    for k in ("resolvedExpression", "productCategoryResolved", "productBrandResolved"):
        v = target_details.get(k)
        if v and isinstance(v, str):
            return v

    return None


def extract_ad_asin_sku(ad_data: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Extract ASIN and SKU from Amazon Ads ad data.
    Handles: creative.products[], flat asin/sku, productAd.asin, etc.
    """
    if not ad_data or not isinstance(ad_data, dict):
        return (None, None)
    # Unwrap if MCP nests ad inside "ad" key
    if "ad" in ad_data and isinstance(ad_data["ad"], dict):
        ad_data = ad_data["ad"]
    asin = ad_data.get("asin")
    sku = ad_data.get("sku")
    creative = ad_data.get("creative") or ad_data.get("productAd") or {}
    # creative.asin (some SP formats)
    if not asin and creative.get("asin"):
        asin = creative.get("asin")
    # creative.asins (SB format - array)
    asins = creative.get("asins")
    if isinstance(asins, list) and asins and not asin:
        asin = str(asins[0])
    products = creative.get("products") or creative.get("product") or []
    if isinstance(products, dict):
        products = [products]
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = p.get("productId") or p.get("product_id")
        ptype = (p.get("productIdType") or p.get("product_id_type") or "").upper()
        if pid:
            pid = str(pid)
            if ptype == "ASIN" or (not ptype and len(pid) == 10 and pid.isalnum()):
                asin = asin or pid
            elif ptype == "SKU":
                sku = sku or pid
            elif not asin and not sku:
                asin = pid
    return (asin, sku)
