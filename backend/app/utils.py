"""Shared utility functions."""

import logging
import re
from typing import Any, Optional
import uuid as uuid_mod
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ── Amazon marketplace → IANA timezone map ────────────────────────────
# Used to compute "today" in the advertiser's reporting timezone, which is
# how Amazon Ads aggregates daily metrics. Server-local time (or UTC) can
# return data for the wrong day around midnight in the advertiser's locale.
MARKETPLACE_TIMEZONES: dict[str, str] = {
    # North America
    "US": "America/Los_Angeles",  # Amazon reports use account-level TZ; PT is the default for US Sellers/Vendors
    "CA": "America/Los_Angeles",
    "MX": "America/Mexico_City",
    "BR": "America/Sao_Paulo",
    # Europe
    "GB": "Europe/London",
    "UK": "Europe/London",
    "IE": "Europe/Dublin",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels",
    "AT": "Europe/Vienna",
    "PT": "Europe/Lisbon",
    "FI": "Europe/Helsinki",
    "LU": "Europe/Luxembourg",
    "SE": "Europe/Stockholm",
    "PL": "Europe/Warsaw",
    "TR": "Europe/Istanbul",
    # Asia-Pacific
    "JP": "Asia/Tokyo",
    "AU": "Australia/Sydney",
    "IN": "Asia/Kolkata",
    "SG": "Asia/Singapore",
    # Middle East & Africa
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "EG": "Africa/Cairo",
    "ZA": "Africa/Johannesburg",
}

REGION_FALLBACK_TIMEZONES: dict[str, str] = {
    "na": "America/Los_Angeles",
    "eu": "Europe/London",
    "fe": "Asia/Tokyo",
}


def resolve_marketplace_timezone(
    marketplace: Optional[str] = None,
    region: Optional[str] = None,
) -> ZoneInfo:
    """
    Resolve the IANA timezone for a marketplace code (e.g. "US", "GB").
    Falls back to the region-level default and finally UTC.
    """
    if marketplace:
        tz_name = MARKETPLACE_TIMEZONES.get(str(marketplace).upper())
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                pass
    if region:
        tz_name = REGION_FALLBACK_TIMEZONES.get(str(region).lower())
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                pass
    return ZoneInfo("UTC")


def marketplace_now(
    marketplace: Optional[str] = None,
    region: Optional[str] = None,
) -> datetime:
    """Current time in the advertiser's marketplace timezone (UTC fallback)."""
    return datetime.now(resolve_marketplace_timezone(marketplace, region))


def marketplace_today(
    marketplace: Optional[str] = None,
    region: Optional[str] = None,
) -> date:
    """
    Today's date in the advertiser's marketplace timezone.

    Use this — never ``date.today()`` — when computing report ranges, "yesterday",
    rolling windows, or anything else compared against Amazon Ads daily buckets.
    """
    return marketplace_now(marketplace, region).date()


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


def normalize_amazon_date(value: Optional[str]) -> Optional[str]:
    """
    Normalize Amazon date-like strings to YYYY-MM-DD where possible.
    Supports:
    - YYYYMMDD
    - YYYY-MM-DD
    - ISO datetimes (YYYY-MM-DDTHH:MM:SS...)
    """
    if not value:
        return value
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{8}", s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if len(s) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
        return s[:10]
    return s


def normalize_state_value(value: Any, for_storage: bool = False) -> Any:
    """Normalize campaign state values for MCP calls/storage."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    if not v:
        return value
    up = v.upper()
    if up in ("ENABLED", "PAUSED", "ARCHIVED"):
        return up.lower() if for_storage else up
    return v


def normalize_mcp_tool_name(tool_name: str) -> str:
    """Normalize common AI/generated tool name variants to canonical MCP names."""
    if not isinstance(tool_name, str):
        return tool_name
    tool = tool_name.strip()
    if not tool:
        return tool

    # Common namespace typos/variants
    tool = tool.replace("campaign-management", "campaign_management")
    tool = tool.replace("account-management", "account_management")
    tool = tool.replace("reporting_", "reporting-")
    tool = tool.replace("campaign_management_", "campaign_management-")
    tool = tool.replace("account_management_", "account_management-")

    return tool


def _to_float(value: Any) -> Any:
    """Best-effort currency/number parsing."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return value
    s = value.strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return value


def _ensure_body(arguments: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return {}
    if isinstance(arguments.get("body"), dict):
        return dict(arguments["body"])
    return dict(arguments)


def normalize_mcp_arguments(tool_name: str, arguments: Optional[dict[str, Any]]) -> dict[str, Any]:
    """
    Normalize MCP arguments so apply/inline actions are resilient to small
    shape mistakes from UI/AI (missing body wrapper, single-item forms, case).
    """
    body = _ensure_body(arguments)

    def _normalize_states(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            i = dict(item)
            if "state" in i:
                i["state"] = normalize_state_value(i["state"])
            out.append(i)
        return out

    if tool_name == "campaign_management-update_campaign_budget":
        if "campaignId" in body and "campaigns" not in body:
            body = {"campaigns": [{"campaignId": body.get("campaignId"), "dailyBudget": _to_float(body.get("dailyBudget"))}]}
        if isinstance(body.get("campaigns"), list):
            body["campaigns"] = [
                {**c, "dailyBudget": _to_float(c.get("dailyBudget"))}
                for c in body["campaigns"] if isinstance(c, dict)
            ]

    elif tool_name in ("campaign_management-update_campaign_state", "campaign_management-update_campaign"):
        if "campaignId" in body and "campaigns" not in body:
            body = {"campaigns": [body]}
        if isinstance(body.get("campaigns"), list):
            campaigns = _normalize_states(body["campaigns"])
            # Normalize budget if present on generic update payload
            for c in campaigns:
                if "dailyBudget" in c:
                    c["dailyBudget"] = _to_float(c.get("dailyBudget"))
            body["campaigns"] = campaigns

    elif tool_name in ("campaign_management-update_target", "campaign_management-update_target_bid", "campaign_management-create_target"):
        if "targetId" in body and "targets" not in body:
            body = {"targets": [body]}
        if isinstance(body.get("targets"), list):
            targets = _normalize_states(body["targets"])
            for t in targets:
                if "bid" in t:
                    t["bid"] = _to_float(t.get("bid"))
            body["targets"] = targets

    elif tool_name == "campaign_management-delete_target":
        if "targetId" in body and "targetIds" not in body:
            body = {"targetIds": [body["targetId"]]}

    elif tool_name == "campaign_management-delete_campaign":
        if "campaignId" in body and "campaignIds" not in body:
            body = {"campaignIds": [body["campaignId"]]}

    elif tool_name == "campaign_management-delete_ad_group":
        if "adGroupId" in body and "adGroupIds" not in body:
            body = {"adGroupIds": [body["adGroupId"]]}

    elif tool_name == "campaign_management-delete_ad":
        if "adId" in body and "adIds" not in body:
            body = {"adIds": [body["adId"]]}

    elif tool_name == "campaign_management-create_ad_group":
        if "campaignId" in body and "adGroups" not in body:
            body = {"adGroups": [body]}
        if isinstance(body.get("adGroups"), list):
            groups = _normalize_states(body["adGroups"])
            for g in groups:
                if "defaultBid" in g:
                    g["defaultBid"] = _to_float(g.get("defaultBid"))
            body["adGroups"] = groups

    elif tool_name == "campaign_management-create_ad":
        if "adGroupId" in body and "ads" not in body:
            body = {"ads": [body]}
        if isinstance(body.get("ads"), list):
            body["ads"] = _normalize_states(body["ads"])

    elif tool_name == "campaign_management-update_ad_group":
        if "adGroupId" in body and "adGroups" not in body:
            body = {"adGroups": [body]}
        if isinstance(body.get("adGroups"), list):
            groups = _normalize_states(body["adGroups"])
            for g in groups:
                if "defaultBid" in g:
                    g["defaultBid"] = _to_float(g.get("defaultBid"))
            body["adGroups"] = groups

    elif tool_name == "campaign_management-update_ad":
        if "adId" in body and "ads" not in body:
            body = {"ads": [body]}
        if isinstance(body.get("ads"), list):
            body["ads"] = _normalize_states(body["ads"])

    return {"body": body}


def normalize_mcp_call(tool_name: str, arguments: Optional[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Return normalized MCP (tool_name, arguments) pair."""
    normalized_tool = normalize_mcp_tool_name(tool_name)
    normalized_args = normalize_mcp_arguments(normalized_tool, arguments)
    return normalized_tool, normalized_args


def extract_mcp_error(result: Any) -> Optional[str]:
    """
    Best-effort extraction of an MCP/tool execution error from parsed payloads.
    Returns an error message when one is detected, otherwise None.
    """
    if result is None:
        return None

    if isinstance(result, str):
        txt = result.strip()
        low = txt.lower()
        if any(token in low for token in ("error", "failed", "exception", "validation")):
            return txt[:500]
        return None

    if isinstance(result, list):
        for item in result:
            err = extract_mcp_error(item)
            if err:
                return err
        return None

    if not isinstance(result, dict):
        return None

    # Common explicit error fields
    for key in ("error", "errorMessage", "error_message"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]
        if val:
            return str(val)[:500]

    errs = result.get("errors")
    if errs:
        return str(errs)[:500]

    status = str(result.get("status") or "").upper()
    if status in ("FAILED", "FAILURE", "ERROR", "CANCELLED"):
        return str(result.get("message") or status)[:500]

    # Recurse into common nested payloads
    for key in ("result", "results", "success", "data", "items"):
        nested = result.get(key)
        err = extract_mcp_error(nested)
        if err:
            return err

    return None


def build_mcp_fallback_call(
    tool_name: str,
    arguments: Optional[dict[str, Any]],
) -> Optional[tuple[str, dict[str, Any]]]:
    """
    Return a fallback MCP call for known flaky/specialized update tools.
    Some accounts reject specialized tools but accept generic update_* payloads.
    """
    if not isinstance(tool_name, str) or tool_name.startswith("_"):
        return None

    args = arguments if isinstance(arguments, dict) else {}
    body = args.get("body") if isinstance(args.get("body"), dict) else args

    if tool_name == "campaign_management-update_campaign_budget":
        campaigns = []
        if isinstance(body.get("campaigns"), list):
            for c in body["campaigns"]:
                if not isinstance(c, dict):
                    continue
                cid = c.get("campaignId")
                if not cid:
                    continue
                item = {"campaignId": cid}
                if "dailyBudget" in c:
                    item["dailyBudget"] = _to_float(c.get("dailyBudget"))
                campaigns.append(item)
        elif body.get("campaignId"):
            campaigns.append({
                "campaignId": body.get("campaignId"),
                "dailyBudget": _to_float(body.get("dailyBudget")),
            })
        if campaigns:
            return ("campaign_management-update_campaign", {"body": {"campaigns": campaigns}})
        return None

    if tool_name == "campaign_management-update_campaign_state":
        campaigns = []
        if isinstance(body.get("campaigns"), list):
            for c in body["campaigns"]:
                if not isinstance(c, dict):
                    continue
                cid = c.get("campaignId")
                if not cid:
                    continue
                item = {"campaignId": cid}
                if "state" in c:
                    item["state"] = normalize_state_value(c.get("state"))
                campaigns.append(item)
        elif body.get("campaignId"):
            campaigns.append({
                "campaignId": body.get("campaignId"),
                "state": normalize_state_value(body.get("state")),
            })
        if campaigns:
            return ("campaign_management-update_campaign", {"body": {"campaigns": campaigns}})
        return None

    if tool_name == "campaign_management-update_target_bid":
        targets = []
        if isinstance(body.get("targets"), list):
            for t in body["targets"]:
                if not isinstance(t, dict):
                    continue
                tid = t.get("targetId")
                if not tid:
                    continue
                item = {"targetId": tid}
                if "bid" in t:
                    item["bid"] = _to_float(t.get("bid"))
                targets.append(item)
        elif body.get("targetId"):
            targets.append({
                "targetId": body.get("targetId"),
                "bid": _to_float(body.get("bid")),
            })
        if targets:
            return ("campaign_management-update_target", {"body": {"targets": targets}})
        return None

    return None


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
