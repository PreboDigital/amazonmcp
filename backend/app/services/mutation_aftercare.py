"""Mutation aftercare — verify Amazon actually applied what we asked.

Amazon's MCP API often returns a generic ``{"success": true}`` envelope
even when the underlying mutation was partially or silently rejected
(e.g. a bid that hit a per-campaign minimum gets clamped, a state
change is queued but not yet visible, a delete returns OK but the
target reappears in the next query).

Phase 5.3 closes that loop:

1. After each successful ``call_tool`` the apply path runs
   :func:`verify_mutation`, which issues a *read-back* query for the
   touched entities.
2. The read-back response is diffed against what the mutation asked
   for. Any mismatched field is emitted as ``drift``.
3. :func:`build_aftercare` packages a small report — ``headline``,
   ``summary``, ``verification`` block, ``next_prompts`` (suggested
   follow-up user actions). The router stuffs this into
   ``PendingChange.apply_result`` so the UI can show "applied — Amazon
   confirmed bid is $0.50" or "applied, but Amazon clamped bid to
   $1.00 (account minimum)".

Verification is **best effort**. If the read-back call fails (auth,
quota, scope mismatch) we still mark the mutation as applied; the
aftercare just records ``{verified: False, error: ...}``. The
underlying ``mcp_result`` is never overwritten.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _bid_close_enough(a: Optional[float], b: Optional[float], tol: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _index_by(items: list[dict], key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        v = item.get(key)
        if isinstance(v, (str, int)):
            out[str(v)] = item
    return out


# ── Read-backs (best-effort, swallow exceptions) ─────────────────────

async def _read_targets_for_ids(
    client,
    target_ids: list[str],
    *,
    ad_group_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> dict[str, dict]:
    """Read back the specified targets via the most-scoped query possible.

    Strategy (in order, falling back on each failure):

    1. Direct ``campaign_management-query_target`` with
       ``targetIdFilter`` — Amazon's id-scoped read returns only the
       rows we actually mutated. O(N) on the request body, not on the
       account.
    2. ``query_targets(ad_group_id=..)`` when an ad_group_id is known
       — bounded to the ad group's targets.
    3. ``query_targets(campaign_id=..)`` when a campaign id is known.
    4. Full account ``query_targets(all_products=True)`` — last resort
       (the legacy behaviour).

    Best-effort: any exception falls through to the next strategy. We
    never raise; an empty dict means "verifier could not read back".
    """
    if not target_ids:
        return {}
    wanted = {str(t) for t in target_ids if t}

    # 1. Targeted id-filter call via raw MCP body
    try:
        ad_products = ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]
        merged: list[dict] = []
        for ap in ad_products:
            body = {
                "adProductFilter": {"include": [ap]},
                "targetIdFilter": {"include": list(wanted)},
            }
            try:
                resp = await client.call_tool(
                    "campaign_management-query_target", {"body": body}
                )
            except Exception as exc:
                logger.debug("Aftercare targetIdFilter(%s) failed: %s", ap, exc)
                continue
            if isinstance(resp, dict):
                rows = resp.get("targets") or resp.get("result") or []
                if isinstance(rows, list):
                    merged.extend(r for r in rows if isinstance(r, dict))
        if merged:
            by_id: dict[str, dict] = {}
            for t in merged:
                tid = t.get("targetId") or t.get("id")
                if tid and str(tid) in wanted:
                    by_id[str(tid)] = t
            if by_id:
                return by_id
    except Exception as exc:  # pragma: no cover — outer guard
        logger.debug("Aftercare targetIdFilter path errored: %s", exc)

    # 2-4. Scoped → unscoped fallback chain
    fallback_calls = []
    if ad_group_id:
        fallback_calls.append(
            lambda: client.query_targets(ad_group_id=ad_group_id, all_products=True)
        )
    if campaign_id:
        fallback_calls.append(
            lambda: client.query_targets(campaign_id=campaign_id, all_products=True)
        )
    fallback_calls.append(lambda: client.query_targets(all_products=True))

    for call in fallback_calls:
        try:
            result = await call()
        except Exception as exc:
            logger.debug("Aftercare fallback query_targets failed: %s", exc)
            continue
        targets = result.get("targets") if isinstance(result, dict) else None
        if not isinstance(targets, list):
            continue
        by_id = {}
        for t in targets:
            tid = t.get("targetId") or t.get("id")
            if tid and str(tid) in wanted:
                by_id[str(tid)] = t
        if by_id:
            return by_id
    return {}


async def _read_targets_for_ad_group(client, ad_group_id: Optional[str]) -> list[dict]:
    if not ad_group_id:
        return []
    try:
        result = await client.query_targets(
            ad_group_id=ad_group_id, all_products=True
        )
    except Exception as exc:
        logger.warning("Aftercare query_targets(ad_group=%s) failed: %s", ad_group_id, exc)
        return []
    return result.get("targets") if isinstance(result, dict) else []


async def _read_campaigns_for_ids(client, campaign_ids: list[str]) -> dict[str, dict]:
    """Read back specific campaigns via ``campaignIdFilter`` when supported."""
    if not campaign_ids:
        return {}
    wanted = {str(c) for c in campaign_ids if c}

    try:
        result = await client.query_campaigns(
            filters={"campaignIdFilter": {"include": list(wanted)}},
            all_products=True,
        )
    except Exception as exc:
        logger.debug("Aftercare query_campaigns(filtered) failed: %s", exc)
        result = None

    if not isinstance(result, dict):
        try:
            result = await client.query_campaigns(all_products=True)
        except Exception as exc:
            logger.warning("Aftercare query_campaigns fallback failed: %s", exc)
            return {}

    campaigns = result.get("campaigns") if isinstance(result, dict) else None
    if not isinstance(campaigns, list):
        return {}
    return {
        str(c.get("campaignId")): c
        for c in campaigns
        if c.get("campaignId") and str(c["campaignId"]) in wanted
    }


async def _read_ad_groups_for_ids(
    client,
    ad_group_ids: list[str],
    *,
    campaign_id: Optional[str] = None,
) -> dict[str, dict]:
    """Read back specific ad groups, prefer scoping by parent campaign."""
    if not ad_group_ids:
        return {}
    wanted = {str(g) for g in ad_group_ids if g}

    if campaign_id:
        try:
            result = await client.query_ad_groups(campaign_id=campaign_id, all_products=True)
        except Exception as exc:
            logger.debug("Aftercare query_ad_groups(campaign=%s) failed: %s", campaign_id, exc)
            result = None
    else:
        result = None

    if not isinstance(result, dict):
        try:
            result = await client.query_ad_groups(all_products=True)
        except Exception as exc:
            logger.warning("Aftercare query_ad_groups failed: %s", exc)
            return {}

    groups = result.get("adGroups") if isinstance(result, dict) else None
    if not isinstance(groups, list):
        return {}
    return {
        str(g.get("adGroupId")): g
        for g in groups
        if g.get("adGroupId") and str(g["adGroupId"]) in wanted
    }


async def _read_ad_groups_for_campaign(client, campaign_id: Optional[str]) -> list[dict]:
    """List the ad groups currently in a campaign (used by create-verify)."""
    if not campaign_id:
        return []
    try:
        result = await client.query_ad_groups(campaign_id=campaign_id, all_products=True)
    except Exception as exc:
        logger.warning("Aftercare query_ad_groups(campaign=%s) failed: %s", campaign_id, exc)
        return []
    groups = result.get("adGroups") if isinstance(result, dict) else None
    return groups if isinstance(groups, list) else []


async def _read_ads_for_ids(
    client,
    ad_ids: list[str],
    *,
    ad_group_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> dict[str, dict]:
    """Read back specific product ads. Scoped query first, then full account.

    Mirrors the target / ad-group helpers: tries the cheapest matching
    query (ad-group → campaign → all) and indexes by ``adId``.
    """
    if not ad_ids:
        return {}
    wanted = {str(a) for a in ad_ids if a}

    fallback_calls: list = []
    if ad_group_id:
        fallback_calls.append(
            lambda: client.query_ads(ad_group_id=ad_group_id, all_products=True)
        )
    if campaign_id:
        fallback_calls.append(
            lambda: client.query_ads(campaign_id=campaign_id, all_products=True)
        )
    fallback_calls.append(lambda: client.query_ads(all_products=True))

    for call in fallback_calls:
        try:
            result = await call()
        except Exception as exc:
            logger.debug("Aftercare query_ads fallback failed: %s", exc)
            continue
        ads = result.get("ads") if isinstance(result, dict) else None
        if not isinstance(ads, list):
            continue
        by_id: dict[str, dict] = {}
        for ad in ads:
            if not isinstance(ad, dict):
                continue
            aid = ad.get("adId") or ad.get("id")
            if aid and str(aid) in wanted:
                by_id[str(aid)] = ad
        if by_id:
            return by_id
    return {}


async def _read_ads_for_ad_group(client, ad_group_id: Optional[str]) -> list[dict]:
    if not ad_group_id:
        return []
    try:
        result = await client.query_ads(ad_group_id=ad_group_id, all_products=True)
    except Exception as exc:
        logger.warning("Aftercare query_ads(ad_group=%s) failed: %s", ad_group_id, exc)
        return []
    ads = result.get("ads") if isinstance(result, dict) else None
    return ads if isinstance(ads, list) else []


# ── Per-tool verifiers ───────────────────────────────────────────────

async def _verify_target_update(client, body: dict) -> dict:
    requested = body.get("targets") or []
    ids = [str(t.get("targetId")) for t in requested if t.get("targetId")]
    actual = await _read_targets_for_ids(client, ids)
    drift: list[dict] = []
    for req in requested:
        tid = str(req.get("targetId") or "")
        if not tid:
            continue
        observed = actual.get(tid)
        if observed is None:
            drift.append({"targetId": tid, "field": "_existence", "expected": "present", "observed": "missing"})
            continue
        if "bid" in req:
            req_bid = _to_float(req["bid"])
            obs_bid = _to_float(observed.get("bid"))
            if not _bid_close_enough(req_bid, obs_bid):
                drift.append({
                    "targetId": tid, "field": "bid",
                    "expected": req_bid, "observed": obs_bid,
                })
        if "state" in req:
            if str(req["state"]).upper() != str(observed.get("state") or "").upper():
                drift.append({
                    "targetId": tid, "field": "state",
                    "expected": req["state"], "observed": observed.get("state"),
                })
    return {
        "checked": len(ids),
        "found": len(actual),
        "drift": drift,
        "ok": not drift and len(actual) == len(ids),
    }


async def _verify_target_delete(client, body: dict) -> dict:
    target_ids = [str(t) for t in (body.get("targetIds") or []) if t]
    actual = await _read_targets_for_ids(client, target_ids)
    drift: list[dict] = []
    for tid in target_ids:
        if tid in actual:
            drift.append({"targetId": tid, "field": "_existence", "expected": "deleted", "observed": "still_present"})
    return {
        "checked": len(target_ids),
        "found_after_delete": len(actual),
        "drift": drift,
        "ok": not drift,
    }


async def _verify_target_create(client, body: dict) -> dict:
    requested = body.get("targets") or []
    if not requested:
        return {"checked": 0, "drift": [], "ok": True}
    ad_group_ids = {str(t.get("adGroupId")) for t in requested if t.get("adGroupId")}
    drift: list[dict] = []
    matched = 0
    for ag_id in ad_group_ids:
        existing = await _read_targets_for_ad_group(client, ag_id)
        existing_expressions = {
            str(
                t.get("expression")
                or t.get("keywordText")
                or t.get("keyword")
                or ""
            ).strip().lower()
            for t in existing
        }
        for req in requested:
            if str(req.get("adGroupId")) != ag_id:
                continue
            expr = str(
                req.get("expression")
                or req.get("keyword")
                or req.get("keywordText")
                or ""
            ).strip().lower()
            if expr and expr in existing_expressions:
                matched += 1
            else:
                drift.append({
                    "adGroupId": ag_id, "field": "expression",
                    "expected": expr, "observed": "missing",
                })
    return {
        "checked": len(requested),
        "matched": matched,
        "drift": drift,
        "ok": not drift,
    }


async def _verify_campaign_update(client, body: dict) -> dict:
    requested = body.get("campaigns") or []
    ids = [str(c.get("campaignId")) for c in requested if c.get("campaignId")]
    actual = await _read_campaigns_for_ids(client, ids)
    drift: list[dict] = []
    for req in requested:
        cid = str(req.get("campaignId") or "")
        if not cid:
            continue
        observed = actual.get(cid)
        if observed is None:
            drift.append({"campaignId": cid, "field": "_existence", "expected": "present", "observed": "missing"})
            continue
        if "dailyBudget" in req:
            req_b = _to_float(req["dailyBudget"])
            obs_b = _to_float(observed.get("dailyBudget"))
            if obs_b is None:
                # Some MCP responses nest budget under a `budget` object.
                # Guard against the value being a non-dict (e.g. a raw
                # number or null) before calling .get().
                budget_field = observed.get("budget")
                if isinstance(budget_field, dict):
                    obs_b = _to_float(budget_field.get("budget"))
            if req_b is not None and obs_b is not None and abs(req_b - obs_b) > 0.01:
                drift.append({
                    "campaignId": cid, "field": "dailyBudget",
                    "expected": req_b, "observed": obs_b,
                })
        if "state" in req:
            if str(req["state"]).upper() != str(observed.get("state") or "").upper():
                drift.append({
                    "campaignId": cid, "field": "state",
                    "expected": req["state"], "observed": observed.get("state"),
                })
        if "name" in req:
            if str(req["name"]) != str(observed.get("name") or ""):
                drift.append({
                    "campaignId": cid, "field": "name",
                    "expected": req["name"], "observed": observed.get("name"),
                })
    return {
        "checked": len(ids),
        "found": len(actual),
        "drift": drift,
        "ok": not drift and len(actual) == len(ids),
    }


async def _verify_ad_group_update(client, body: dict) -> dict:
    requested = body.get("adGroups") or []
    ids = [str(g.get("adGroupId")) for g in requested if g.get("adGroupId")]
    actual = await _read_ad_groups_for_ids(client, ids)
    drift: list[dict] = []
    for req in requested:
        gid = str(req.get("adGroupId") or "")
        if not gid:
            continue
        observed = actual.get(gid)
        if observed is None:
            drift.append({"adGroupId": gid, "field": "_existence", "expected": "present", "observed": "missing"})
            continue
        if "defaultBid" in req:
            req_b = _to_float(req["defaultBid"])
            obs_b = _to_float(observed.get("defaultBid"))
            if not _bid_close_enough(req_b, obs_b):
                drift.append({
                    "adGroupId": gid, "field": "defaultBid",
                    "expected": req_b, "observed": obs_b,
                })
        if "state" in req:
            if str(req["state"]).upper() != str(observed.get("state") or "").upper():
                drift.append({
                    "adGroupId": gid, "field": "state",
                    "expected": req["state"], "observed": observed.get("state"),
                })
        if "name" in req:
            if str(req["name"]) != str(observed.get("name") or ""):
                drift.append({
                    "adGroupId": gid, "field": "name",
                    "expected": req["name"], "observed": observed.get("name"),
                })
    return {
        "checked": len(ids),
        "found": len(actual),
        "drift": drift,
        "ok": not drift and len(actual) == len(ids),
    }


async def _verify_campaign_delete(client, body: dict) -> dict:
    """Confirm the requested campaigns are gone after a delete call."""
    campaign_ids = [str(c) for c in (body.get("campaignIds") or []) if c]
    actual = await _read_campaigns_for_ids(client, campaign_ids)
    drift: list[dict] = []
    for cid in campaign_ids:
        observed = actual.get(cid)
        if observed is None:
            continue
        # Some accounts soft-delete by flipping state to ARCHIVED instead
        # of physically removing the row. Treat ARCHIVED as success but
        # surface ENABLED/PAUSED as drift.
        observed_state = str(observed.get("state") or "").upper()
        if observed_state == "ARCHIVED":
            continue
        drift.append({
            "campaignId": cid,
            "field": "_existence",
            "expected": "deleted",
            "observed": f"still present ({observed_state or 'unknown state'})",
        })
    return {
        "checked": len(campaign_ids),
        "found_after_delete": len(actual),
        "drift": drift,
        "ok": not drift,
    }


async def _verify_ad_group_delete(client, body: dict) -> dict:
    """Confirm the requested ad groups are gone after a delete call."""
    ag_ids = [str(g) for g in (body.get("adGroupIds") or []) if g]
    actual = await _read_ad_groups_for_ids(client, ag_ids)
    drift: list[dict] = []
    for gid in ag_ids:
        observed = actual.get(gid)
        if observed is None:
            continue
        observed_state = str(observed.get("state") or "").upper()
        if observed_state == "ARCHIVED":
            continue
        drift.append({
            "adGroupId": gid,
            "field": "_existence",
            "expected": "deleted",
            "observed": f"still present ({observed_state or 'unknown state'})",
        })
    return {
        "checked": len(ag_ids),
        "found_after_delete": len(actual),
        "drift": drift,
        "ok": not drift,
    }


async def _verify_ad_delete(client, body: dict) -> dict:
    """Confirm the requested ads are gone after a delete call."""
    ad_ids = [str(a) for a in (body.get("adIds") or []) if a]
    actual = await _read_ads_for_ids(client, ad_ids)
    drift: list[dict] = []
    for aid in ad_ids:
        observed = actual.get(aid)
        if observed is None:
            continue
        observed_state = str(observed.get("state") or "").upper()
        if observed_state == "ARCHIVED":
            continue
        drift.append({
            "adId": aid,
            "field": "_existence",
            "expected": "deleted",
            "observed": f"still present ({observed_state or 'unknown state'})",
        })
    return {
        "checked": len(ad_ids),
        "found_after_delete": len(actual),
        "drift": drift,
        "ok": not drift,
    }


async def _verify_ad_group_create(client, body: dict) -> dict:
    """After create_ad_group, look up the parent campaigns and match by name.

    The MCP response usually returns the new ``adGroupId`` directly,
    but we cannot rely on that being exposed to the verifier in a
    consistent shape across SP/SB/SD. Matching by ``(campaignId, name)``
    is robust and catches the "Amazon accepted but never persisted"
    case as well as duplicate-name silent rejections.
    """
    requested = body.get("adGroups") or []
    if not requested:
        return {"checked": 0, "drift": [], "ok": True}

    by_campaign: dict[str, list[dict]] = {}
    for ag in requested:
        cid = str(ag.get("campaignId") or "")
        if cid:
            by_campaign.setdefault(cid, []).append(ag)

    drift: list[dict] = []
    matched = 0
    for cid, asks in by_campaign.items():
        existing = await _read_ad_groups_for_campaign(client, cid)
        existing_by_name = {
            str(g.get("name") or "").strip().lower(): g
            for g in existing
            if isinstance(g, dict)
        }
        for req in asks:
            name = str(req.get("name") or "").strip().lower()
            if not name:
                drift.append({
                    "campaignId": cid,
                    "field": "name",
                    "expected": "non-empty",
                    "observed": "empty",
                })
                continue
            observed = existing_by_name.get(name)
            if observed is None:
                drift.append({
                    "campaignId": cid,
                    "field": "_existence",
                    "expected": f"ad group named {name!r}",
                    "observed": "missing",
                })
                continue
            matched += 1
            if "defaultBid" in req:
                req_b = _to_float(req["defaultBid"])
                obs_b = _to_float(observed.get("defaultBid"))
                if not _bid_close_enough(req_b, obs_b):
                    drift.append({
                        "campaignId": cid,
                        "adGroupId": observed.get("adGroupId"),
                        "field": "defaultBid",
                        "expected": req_b,
                        "observed": obs_b,
                    })
    return {
        "checked": len(requested),
        "matched": matched,
        "drift": drift,
        "ok": not drift,
    }


async def _verify_ad_create(client, body: dict) -> dict:
    """After create_ad, confirm each (adGroupId, asin|sku) is present."""
    requested = body.get("ads") or []
    if not requested:
        return {"checked": 0, "drift": [], "ok": True}

    by_ad_group: dict[str, list[dict]] = {}
    for ad in requested:
        gid = str(ad.get("adGroupId") or "")
        if gid:
            by_ad_group.setdefault(gid, []).append(ad)

    drift: list[dict] = []
    matched = 0
    for gid, asks in by_ad_group.items():
        existing = await _read_ads_for_ad_group(client, gid)
        existing_keys: set[str] = set()
        for ad in existing:
            if not isinstance(ad, dict):
                continue
            asin = str(ad.get("asin") or "").strip().upper()
            sku = str(ad.get("sku") or "").strip()
            if asin:
                existing_keys.add(f"asin:{asin}")
            if sku:
                existing_keys.add(f"sku:{sku}")
        for req in asks:
            asin = str(req.get("asin") or "").strip().upper()
            sku = str(req.get("sku") or "").strip()
            key = f"asin:{asin}" if asin else (f"sku:{sku}" if sku else "")
            if not key:
                drift.append({
                    "adGroupId": gid,
                    "field": "identifier",
                    "expected": "asin or sku",
                    "observed": "missing",
                })
                continue
            if key in existing_keys:
                matched += 1
            else:
                drift.append({
                    "adGroupId": gid,
                    "field": "_existence",
                    "expected": key,
                    "observed": "missing",
                })
    return {
        "checked": len(requested),
        "matched": matched,
        "drift": drift,
        "ok": not drift,
    }


async def _verify_ad_update(client, body: dict) -> dict:
    """After update_ad, diff state for each requested adId."""
    requested = body.get("ads") or []
    ids = [str(a.get("adId")) for a in requested if a.get("adId")]
    actual = await _read_ads_for_ids(client, ids)
    drift: list[dict] = []
    for req in requested:
        aid = str(req.get("adId") or "")
        if not aid:
            continue
        observed = actual.get(aid)
        if observed is None:
            drift.append({
                "adId": aid,
                "field": "_existence",
                "expected": "present",
                "observed": "missing",
            })
            continue
        if "state" in req:
            if str(req["state"]).upper() != str(observed.get("state") or "").upper():
                drift.append({
                    "adId": aid,
                    "field": "state",
                    "expected": req["state"],
                    "observed": observed.get("state"),
                })
        if "name" in req:
            if str(req["name"]) != str(observed.get("name") or ""):
                drift.append({
                    "adId": aid,
                    "field": "name",
                    "expected": req["name"],
                    "observed": observed.get("name"),
                })
    return {
        "checked": len(ids),
        "found": len(actual),
        "drift": drift,
        "ok": not drift and len(actual) == len(ids),
    }


def campaign_id_from_harvest_mcp_result(mcp_result: Any) -> Optional[str]:
    """Best-effort new manual campaign id from create_campaign_harvest_targets or harvest service result."""
    if not isinstance(mcp_result, dict):
        return None
    for key in ("target_campaign_id", "targetCampaignId", "campaignId", "manualCampaignId"):
        v = mcp_result.get(key)
        if v:
            return str(v)
    for nested_key in ("raw_result", "result", "data"):
        inner = mcp_result.get(nested_key)
        if isinstance(inner, dict):
            found = campaign_id_from_harvest_mcp_result(inner)
            if found:
                return found
    hr = mcp_result.get("harvestResults") or mcp_result.get("harvestRequestResults")
    if isinstance(hr, list):
        for item in hr:
            if isinstance(item, dict):
                for key in ("targetCampaignId", "campaignId", "manualCampaignId"):
                    v = item.get(key)
                    if v:
                        return str(v)
    return None


async def verify_harvest_execution(client, arguments: dict, mcp_result: dict) -> dict:
    """Read-back after ``_harvest_execute`` (existing = keywords on target campaign; new = campaign exists)."""
    if mcp_result.get("status") == "error":
        return {"ok": True, "skipped": True, "reason": "harvest service reported error"}
    mode = mcp_result.get("mode")
    if mode == "existing_campaign":
        tcid = mcp_result.get("target_campaign_id")
        keywords = mcp_result.get("keywords") or []
        if not tcid:
            return {"ok": False, "error": "missing target_campaign_id in harvest result", "drift": []}
        try:
            res = await client.query_targets(campaign_id=str(tcid), all_products=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300], "drift": []}
        targets = res.get("targets") if isinstance(res, dict) else None
        if not isinstance(targets, list):
            targets = []
        found_exprs: set[str] = set()
        for t in targets:
            if not isinstance(t, dict):
                continue
            txt = str(
                t.get("keywordText")
                or t.get("expression")
                or t.get("keyword")
                or ""
            ).strip().lower()
            if txt:
                found_exprs.add(txt)
        drift: list[dict] = []
        for kw in keywords:
            if not isinstance(kw, dict):
                continue
            ktxt = str(kw.get("keyword") or kw.get("text") or "").strip().lower()
            if not ktxt:
                continue
            if ktxt not in found_exprs:
                drift.append({
                    "keyword": ktxt,
                    "field": "_existence",
                    "expected": "present in target campaign",
                    "observed": "missing",
                })
        return {
            "checked": len(keywords),
            "matched": len(keywords) - len(drift),
            "drift": drift,
            "ok": not drift,
        }
    if mode == "new_campaign":
        cid = campaign_id_from_harvest_mcp_result(mcp_result)
        if not cid:
            return {"ok": True, "skipped": True, "reason": "no target campaign id in harvest result"}
        actual = await _read_campaigns_for_ids(client, [cid])
        if cid not in actual:
            return {
                "checked": 1,
                "found": 0,
                "drift": [{
                    "campaignId": cid,
                    "field": "_existence",
                    "expected": "present",
                    "observed": "missing",
                }],
                "ok": False,
            }
        return {"checked": 1, "found": 1, "drift": [], "ok": True}
    return {"ok": True, "skipped": True, "reason": "unknown harvest mode in result"}


async def verify_harvest_create_campaign_result(client, arguments: dict, mcp_result: dict) -> dict:
    """Read-back after ``campaign_management-create_campaign_harvest_targets``."""
    cid = campaign_id_from_harvest_mcp_result(mcp_result)
    if not cid:
        return {"ok": True, "skipped": True, "reason": "no campaign id in MCP harvest result"}
    actual = await _read_campaigns_for_ids(client, [cid])
    if cid not in actual:
        return {
            "checked": 1,
            "found": 0,
            "drift": [{
                "campaignId": cid,
                "field": "_existence",
                "expected": "present",
                "observed": "missing",
            }],
            "ok": False,
        }
    return {"checked": 1, "found": 1, "drift": [], "ok": True}


# ── Public verify entrypoint ─────────────────────────────────────────

_VERIFIERS = {
    "campaign_management-update_target_bid": _verify_target_update,
    "campaign_management-update_target": _verify_target_update,
    "campaign_management-create_target": _verify_target_create,
    "campaign_management-delete_target": _verify_target_delete,
    "campaign_management-update_campaign_budget": _verify_campaign_update,
    "campaign_management-update_campaign_state": _verify_campaign_update,
    "campaign_management-update_campaign": _verify_campaign_update,
    "campaign_management-delete_campaign": _verify_campaign_delete,
    "campaign_management-update_ad_group": _verify_ad_group_update,
    "campaign_management-create_ad_group": _verify_ad_group_create,
    "campaign_management-delete_ad_group": _verify_ad_group_delete,
    "campaign_management-create_ad": _verify_ad_create,
    "campaign_management-update_ad": _verify_ad_update,
    "campaign_management-delete_ad": _verify_ad_delete,
}


async def verify_mutation(client, tool: str, arguments: dict) -> dict:
    """Read-back a recently-applied mutation; return verification report.

    Always returns a dict; on error the dict has ``ok=False`` and an
    ``error`` field — the apply path should *not* mark the mutation as
    failed because Amazon already accepted the write.
    """
    verifier = _VERIFIERS.get(tool)
    if verifier is None:
        return {"ok": True, "skipped": True, "reason": "no verifier for tool"}
    body = (arguments or {}).get("body") if isinstance(arguments, dict) else None
    if not isinstance(body, dict):
        return {"ok": False, "error": "arguments.body missing"}
    try:
        report = await verifier(client, body)
    except Exception as exc:
        logger.exception("verify_mutation failed for %s: %s", tool, exc)
        return {"ok": False, "error": str(exc)[:300]}
    report["checked_at"] = _now_iso()
    return report


# ── Aftercare summary builder ────────────────────────────────────────

def _summarize_drift(drift: list[dict]) -> str:
    if not drift:
        return ""
    parts: list[str] = []
    for d in drift[:5]:
        eid = d.get("targetId") or d.get("campaignId") or d.get("adGroupId") or "?"
        parts.append(
            f"{eid}.{d.get('field')}: expected {d.get('expected')!r} "
            f"got {d.get('observed')!r}"
        )
    if len(drift) > 5:
        parts.append(f"… +{len(drift) - 5} more")
    return "; ".join(parts)


def _next_prompts_for(tool: str, drift: list[dict]) -> list[str]:
    has_drift = bool(drift)
    if tool in (
        "campaign_management-update_target_bid",
        "campaign_management-update_target",
    ):
        if has_drift:
            return [
                "Show me which targets had clamped bids and why",
                "Re-attempt the bid changes with adjusted values",
            ]
        return [
            "Show 7-day performance for the targets I just updated",
            "Find similar targets that might benefit from the same change",
        ]
    if tool == "campaign_management-update_campaign_budget":
        return [
            "Show the next 24 hours of pacing for these campaigns",
            "Are any other campaigns budget-capped right now?",
        ]
    if tool == "campaign_management-update_campaign_state":
        return [
            "What changed in spend after pausing/enabling these campaigns?",
            "Show me yesterday's performance for these campaigns",
        ]
    if tool == "campaign_management-create_target":
        return [
            "Show first-day performance for the new keywords",
            "Suggest negative keywords to protect spend",
        ]
    if tool == "campaign_management-delete_target":
        return [
            "Replace the deleted keywords with stronger variants",
            "Show 30-day cumulative spend recovered",
        ]
    if tool == "campaign_management-update_ad_group":
        return ["Show ad group performance for the last 7 days"]
    if tool == "campaign_management-create_ad_group":
        if has_drift:
            return [
                "List ad groups in the parent campaign and confirm names",
                "Re-attempt the create with a different ad group name",
            ]
        return [
            "Add keywords to the new ad group",
            "Set bid rules on the new ad group",
        ]
    if tool == "campaign_management-delete_ad_group":
        if has_drift:
            return [
                "Force-archive the ad group instead of delete",
                "Re-sync ad groups and retry",
            ]
        return ["Show campaigns left without ad groups (cleanup)"]
    if tool == "campaign_management-delete_campaign":
        if has_drift:
            return [
                "Force-archive the campaign instead of delete",
                "Re-sync campaigns and retry",
            ]
        return ["Show 30-day spend recovered from deleted campaigns"]
    if tool == "campaign_management-create_ad":
        if has_drift:
            return [
                "Confirm the ASIN/SKU is valid in the seller catalog",
                "Re-sync ads and verify Amazon accepted the create",
            ]
        return ["Show first-day impressions for the new ads"]
    if tool == "campaign_management-update_ad":
        return ["Show 7-day performance for the updated ads"]
    if tool == "campaign_management-delete_ad":
        if has_drift:
            return [
                "Force-archive the ad instead of delete",
                "Re-sync ads and retry",
            ]
        return ["Show ad groups now missing ads (cleanup)"]
    if tool == "_harvest_execute":
        if has_drift:
            return [
                "List targets in the target manual campaign that did not verify",
                "Re-sync campaigns and retry the harvest batch",
            ]
        return [
            "Review performance of harvested keywords after 48 hours",
            "Suggest negatives to protect the new manual spend",
        ]
    if tool == "campaign_management-create_campaign_harvest_targets":
        if has_drift:
            return [
                "Confirm the new manual campaign exists in Amazon Ads console",
                "Re-run discover accounts / campaign sync",
            ]
        return [
            "Monitor the new manual campaign's first week of delivery",
            "Compare ACOS vs the source auto campaign",
        ]
    return []


def build_aftercare(
    tool: str,
    arguments: dict,
    mcp_result: Any,
    verification: Optional[dict],
) -> dict:
    """Package a verification report into a UI-friendly aftercare dict."""
    verification = verification or {}
    drift = verification.get("drift") or []
    ok = bool(verification.get("ok"))
    skipped = bool(verification.get("skipped"))

    if skipped:
        headline = "Applied (no aftercare verifier registered for this tool)"
        summary = "Amazon accepted the call; we did not run a read-back."
    elif "error" in verification:
        headline = "Applied — verification could not run"
        summary = f"Read-back failed: {verification.get('error')}"
    elif ok:
        headline = "Applied and verified"
        summary = "Amazon read-back matches the requested values."
    else:
        headline = "Applied with drift"
        summary = (
            f"Amazon accepted the call but the read-back disagrees on "
            f"{len(drift)} field(s): {_summarize_drift(drift)}"
        )

    return {
        "tool": tool,
        "headline": headline,
        "summary": summary,
        "verification": verification,
        "next_prompts": _next_prompts_for(tool, drift),
        "mcp_result_excerpt": _excerpt(mcp_result),
    }


def _excerpt(value: Any, limit: int = 500) -> Any:
    """Return a small-bytes excerpt of a possibly-large MCP result."""
    if value is None:
        return None
    try:
        import json
        s = json.dumps(value, default=str)
    except Exception:
        s = str(value)
    if len(s) <= limit:
        return value
    return {"_truncated": True, "preview": s[:limit]}
