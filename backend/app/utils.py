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
        # productTarget: product.productId (ASIN) nested — MCP format
        if detail_key == "productTarget":
            product = detail_val.get("product")
            if isinstance(product, dict):
                pid = product.get("productId") or product.get("product_id")
                ptype = (product.get("productIdType") or product.get("product_id_type") or "").upper()
                if pid and _is_keyword_like(str(pid)):
                    return f"ASIN: {pid}" if ptype == "ASIN" else str(pid)
            if detail_val.get("matchType"):
                return f"Product: {detail_val['matchType']}"
        # themeTarget has matchType (e.g. KEYWORDS_CLOSE_MATCH) — use as fallback label
        if detail_key == "themeTarget" and detail_val.get("matchType"):
            return f"Theme: {detail_val['matchType']}"
        # productCategoryTarget: productCategoryRefinement.productCategoryRefinement.productCategoryId (nested)
        if detail_key == "productCategoryTarget":
            cat_id = _extract_product_category_id(detail_val)
            if cat_id:
                return f"Category: {cat_id}"
            if detail_val.get("matchType"):
                return f"Category: {detail_val['matchType']}"
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


def _extract_product_category_id(obj: dict, depth: int = 0) -> Optional[str]:
    """Recursively find productCategoryId in productCategoryTarget nested structure."""
    if not obj or not isinstance(obj, dict) or depth > 5:
        return None
    if "productCategoryId" in obj and obj["productCategoryId"]:
        return str(obj["productCategoryId"])
    for v in obj.values():
        if isinstance(v, dict):
            found = _extract_product_category_id(v, depth + 1)
            if found:
                return found
    return None


def _looks_like_asin(s: str) -> bool:
    """True if string looks like an Amazon ASIN (10 alphanumeric chars)."""
    return bool(s and isinstance(s, str) and len(s) == 10 and s.isalnum())


def extract_ad_asin_sku(ad_data: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Extract ASIN and SKU from Amazon Ads ad data.
    Handles: creative.products[], creative.product, flat asin/sku, MCP variants.
    """
    if not ad_data or not isinstance(ad_data, dict):
        return (None, None)
    if "ad" in ad_data and isinstance(ad_data["ad"], dict):
        ad_data = ad_data["ad"]
    asin = ad_data.get("asin")
    sku = ad_data.get("sku")
    creative = ad_data.get("creative") or ad_data.get("productAd") or {}
    # Top-level productId (some MCP formats)
    if not asin and _looks_like_asin(str(ad_data.get("productId", ""))):
        asin = str(ad_data["productId"])
    # creative.asin, creative.productId
    if not asin and creative.get("asin"):
        asin = creative.get("asin")
    if not asin and _looks_like_asin(str(creative.get("productId", ""))):
        asin = str(creative["productId"])
    # creative.asins (SB - array)
    asins = creative.get("asins")
    if isinstance(asins, list) and asins and not asin:
        asin = str(asins[0])
    # creative.products[] or creative.product (MCP: product may be singular object)
    products = creative.get("products") or creative.get("product") or []
    if isinstance(products, dict):
        products = [products]
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = p.get("productId") or p.get("product_id") or p.get("asin")
        ptype = (p.get("productIdType") or p.get("product_id_type") or "").upper()
        if pid:
            pid = str(pid)
            if ptype == "ASIN" or (not ptype and _looks_like_asin(pid)):
                asin = asin or pid
            elif ptype == "SKU":
                sku = sku or pid
            elif not asin and not sku:
                asin = pid
    # Fallback: scan creative for any ASIN-like value (handles unknown MCP nesting)
    if not asin and not sku:
        for key in ("asin", "productId", "product_id"):
            for obj in (creative, ad_data):
                v = obj.get(key) if isinstance(obj, dict) else None
                if v and _looks_like_asin(str(v)):
                    asin = str(v)
                    break
            if asin:
                break
    # Deep scan: creative.products[].productId, creative.product.asin, etc.
    if not asin and not sku:
        for container in (creative.get("products"), creative.get("product"), [creative]):
            if isinstance(container, dict):
                container = [container]
            if not isinstance(container, list):
                continue
            for item in container:
                if not isinstance(item, dict):
                    continue
                for k in ("asin", "productId", "product_id"):
                    v = item.get(k)
                    if v and _looks_like_asin(str(v)):
                        asin = asin or str(v)
                        break
                if asin:
                    break
            if asin:
                break
    # productAd at top level (some MCP formats)
    product_ad = ad_data.get("productAd")
    if isinstance(product_ad, dict) and not asin:
        for k in ("asin", "productId", "asins"):
            v = product_ad.get(k)
            if isinstance(v, list) and v:
                v = v[0]
            if v and _looks_like_asin(str(v)):
                asin = str(v)
                break
    # Recursive scan for ASIN-like values (handles unknown MCP nesting)
    if not asin and not sku:

        def _find_asin(obj, depth: int = 0) -> Optional[str]:
            if depth > 6:
                return None
            if isinstance(obj, dict):
                for key in ("asin", "productId", "product_id"):
                    v = obj.get(key)
                    if v and _looks_like_asin(str(v)):
                        return str(v)
                for v in obj.values():
                    found = _find_asin(v, depth + 1)
                    if found:
                        return found
            elif isinstance(obj, list) and obj:
                return _find_asin(obj[0], depth + 1)
            return None

        asin = _find_asin(ad_data)
    return (asin, sku)


def extract_ad_display_name(ad_data: dict, asin: Optional[str] = None, sku: Optional[str] = None) -> Optional[str]:
    """
    Extract human-readable display name for an ad.
    Product ads often lack name/headline; use ASIN/SKU when available.
    """
    if not ad_data or not isinstance(ad_data, dict):
        return None
    if "ad" in ad_data and isinstance(ad_data["ad"], dict):
        ad_data = ad_data["ad"]
    name = ad_data.get("name") or ad_data.get("adName")
    if name and _is_keyword_like(str(name)):
        return str(name)
    creative = ad_data.get("creative") or ad_data.get("productAd") or {}
    headline = creative.get("headline") or creative.get("brandName")
    if headline and _is_keyword_like(str(headline)):
        return str(headline)
    if asin:
        return f"ASIN: {asin}"
    if sku:
        return f"SKU: {sku}"
    return None
