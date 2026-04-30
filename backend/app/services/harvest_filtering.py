"""Harvest candidate metrics + threshold filtering (no MCP imports).

Used by HarvestService, harvest preview API, and unit tests.
"""

from __future__ import annotations

from typing import Any, Optional


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _flat_target_row(t: dict) -> dict:
    """Merge nested metric blobs so field names from MCP variants are visible."""
    if not isinstance(t, dict):
        return {}
    merged = dict(t)
    for key in ("extendedData", "metrics", "performance", "insight"):
        blob = t.get(key)
        if isinstance(blob, dict):
            merged.update(blob)
    return merged


def _window_order(lookback_days: int) -> list[str]:
    lb = max(1, min(int(lookback_days or 30), 90))
    if lb <= 1:
        return ["1d", "7d", "14d", "30d", "any"]
    if lb <= 7:
        return ["7d", "14d", "30d", "1d", "any"]
    if lb <= 14:
        return ["14d", "7d", "30d", "1d", "any"]
    return ["30d", "14d", "7d", "1d", "any"]


def _first_present_float(t: dict, keys: tuple[str, ...]) -> float:
    for k in keys:
        if k in t and t.get(k) is not None:
            return _to_float(t.get(k))
    return 0.0


def _keys_for_window(metric: str, window: str) -> tuple[str, ...]:
    """Ordered MCP / Ads field names for sales | clicks | acos."""
    if window == "any":
        if metric == "sales":
            return (
                "attributedSales30d",
                "attributedSales14d",
                "attributedSales7d",
                "attributedSales1d",
                "attributedSalesSameSku30d",
                "attributedSalesSameSku14d",
                "attributedSalesSameSku7d",
                "sales30d",
                "sales14d",
                "sales7d",
                "sales1d",
                "sales",
                "attributedSales",
                "revenue",
            )
        if metric == "clicks":
            return (
                "clicks30d",
                "clicks14d",
                "clicks7d",
                "clicks1d",
                "attributedClicks30d",
                "attributedClicks14d",
                "attributedClicks7d",
                "clicks",
            )
        return (
            "acos30d",
            "acos14d",
            "acos7d",
            "acos1d",
            "acos",
            "advertisingCostOfSales",
        )
    suf = window
    if metric == "sales":
        return (
            f"attributedSales{suf}",
            f"attributedSalesSameSku{suf}",
            f"sales{suf}",
        )
    if metric == "clicks":
        return (
            f"clicks{suf}",
            f"attributedClicks{suf}",
        )
    return (
        f"acos{suf}",
        f"advertisingCostOfSales{suf}",
    )


def pick_harvest_metrics(target: dict, lookback_days: int) -> tuple[float, float, float, str]:
    """
    Best-effort (sales, acos, clicks, window_label) aligned to lookback_days.
    Falls back to aggregate / unqualified fields when window-specific data is absent.
    """
    t = _flat_target_row(target)
    for window in _window_order(lookback_days):
        if window == "any":
            s = _first_present_float(t, _keys_for_window("sales", "any"))
            c = _first_present_float(t, _keys_for_window("clicks", "any"))
            a = _first_present_float(t, _keys_for_window("acos", "any"))
            return s, a, c, "aggregate"
        s = _first_present_float(t, _keys_for_window("sales", window))
        c = _first_present_float(t, _keys_for_window("clicks", window))
        a = _first_present_float(t, _keys_for_window("acos", window))
        if s > 0 or c > 0 or a > 0:
            return s, a, c, window
    s = _first_present_float(t, ("sales", "attributedSales7d", "attributedSales"))
    c = _first_present_float(t, ("clicks", "clicks7d"))
    a = _first_present_float(t, ("acos", "acos7d"))
    return s, a, c, "aggregate"


def normalize_target_list(targets_raw: Any) -> list[dict]:
    """Normalize query_targets payload to a flat list of target dicts."""
    if isinstance(targets_raw, list):
        return [x for x in targets_raw if isinstance(x, dict)]
    if isinstance(targets_raw, dict):
        for key in ("targets", "result", "results", "items"):
            inner = targets_raw.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def _norm_match_type(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    s = str(value).strip().upper()
    if s in ("BROAD", "PHRASE", "EXACT"):
        return s
    return None


def filter_target_list_for_harvest(
    target_list: list[dict],
    *,
    sales_threshold: float,
    acos_threshold: Optional[float],
    clicks_threshold: Optional[int],
    lookback_days: int,
    match_type_filter: Optional[str],
) -> tuple[list[dict], str]:
    """
    Same rules as HarvestService existing-campaign filtering.
    Returns (qualified_keyword_dicts, metrics_window).
    """
    want_mt = _norm_match_type(match_type_filter)
    qualified: list[dict] = []
    window_used = "aggregate"
    for t in target_list:
        row = _flat_target_row(t)
        kw_text = (
            row.get("keyword")
            or row.get("keywordText")
            or row.get("expression")
            or row.get("text")
        )
        if not kw_text:
            continue
        sales, acos, clicks, win = pick_harvest_metrics(row, lookback_days)
        if win != "aggregate":
            window_used = win
        try:
            if float(sales) < float(sales_threshold):
                continue
            if acos_threshold is not None and float(acos) > float(acos_threshold):
                continue
            if clicks_threshold is not None and int(clicks) < int(clicks_threshold):
                continue
        except (TypeError, ValueError):
            continue
        effective_mt = want_mt or _norm_match_type(row.get("matchType")) or "BROAD"
        qualified.append({
            "keyword": str(kw_text).strip(),
            "matchType": effective_mt,
            "bid": row.get("bid"),
            "clicks": clicks,
            "sales": sales,
            "spend": row.get("spend") or row.get("cost"),
            "acos": acos,
        })
    return qualified, window_used
