"""Read tools for the AI assistant — DB-cached + live MCP.

Adds a third tool tier to ``ai_tools._INLINE_TOOLS`` / ``_QUEUE_TOOLS``:

* ``db_*`` — query the local cache (campaigns, ad_groups, targets,
  search_terms, performance trend, pending changes). Instant, free.
* ``mcp_*`` — live read against Amazon Ads MCP (campaigns / ad_groups /
  targets / single campaign). 1-5s. Use when the user explicitly wants
  fresh data or when DB is empty / stale.

Async report tools stay queue-only via ``_request_sync`` (existing in
``ai_tools``) — chat tool-loops can't wait 30-90s for poll_report.

Design contract:

* :func:`openai_read_tool_specs` returns the OpenAI ``tools=[]`` payload.
* :func:`anthropic_read_tool_specs` returns the Anthropic shape.
* :data:`READ_TOOL_NAMES` lets the loop in ``ai_service.chat`` decide
  whether a tool call should be executed locally and looped, or treated
  as a user-facing action.
* :func:`build_tool_executor` returns an async callable
  ``(name, args) -> dict`` wired with ``db`` / ``cred`` / optional MCP
  client factory. Results are size-bounded so a runaway query can't
  blow the prompt budget on the next hop.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Campaign,
    AdGroup,
    Target,
    PendingChange,
    SearchTermPerformance,
    AccountPerformanceDaily,
    Credential,
)
from app.utils import utcnow

logger = logging.getLogger(__name__)

# Per-tool result row caps — protect the prompt budget on the NEXT hop.
DB_ROW_CAP = 50
MCP_ROW_CAP = 100
RESULT_CHAR_CAP = 24_000  # JSON-serialised tool result hard cap

ToolExecutor = Callable[[str, dict], Awaitable[dict]]


# ── Tool specs (provider-agnostic) ───────────────────────────────────

_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_INTEGER = {"type": "integer"}
_BOOL = {"type": "boolean"}


_READ_TOOLS: list[dict[str, Any]] = [
    # ── DB tools — instant, prefer for cached data ───────────────────
    {
        "name": "db_query_campaigns",
        "description": (
            "Search the locally synced campaigns cache. Returns campaign metadata + "
            "last-known performance metrics (spend, sales, ACOS, clicks, impressions, "
            "orders). Use this FIRST for any 'show me my campaigns' / 'list campaigns "
            "doing X' question. Free, instant. Falls back to mcp_list_campaigns when "
            "the user explicitly asks for live data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["ENABLED", "PAUSED", "ARCHIVED"]},
                "type": {"type": "string", "enum": ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]},
                "name_search": {**_STRING, "description": "Substring match on campaign name (case-insensitive)."},
                "min_spend": _NUMBER,
                "max_acos": {**_NUMBER, "description": "Filter to campaigns with ACOS <= this number."},
                "min_acos": _NUMBER,
                "sort_by": {"type": "string", "enum": ["spend", "sales", "acos", "clicks", "orders", "name"]},
                "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
                "limit": {**_INTEGER, "description": f"Max rows (1-{DB_ROW_CAP}). Default 25."},
            },
        },
    },
    {
        "name": "db_query_ad_groups",
        "description": (
            "Search the locally synced ad groups cache. Filter by campaign id, "
            "state, or name substring."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {**_STRING, "description": "Amazon campaign id (use the id: from context)."},
                "state": {"type": "string", "enum": ["ENABLED", "PAUSED", "ARCHIVED"]},
                "name_search": _STRING,
                "limit": _INTEGER,
            },
        },
    },
    {
        "name": "db_query_targets",
        "description": (
            "Search the locally synced targets/keywords cache. Strong filters for "
            "the most common analyst questions: top spenders, non-converters "
            "(clicks > 0 AND orders = 0), high-ACOS, or full-text keyword search. "
            "Use this BEFORE asking the user to sync — the DB usually has the answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ad_group_id": _STRING,
                "campaign_id": _STRING,
                "keyword_search": {**_STRING, "description": "Substring match on keyword/expression text."},
                "match_type": {"type": "string", "enum": ["EXACT", "PHRASE", "BROAD"]},
                "state": {"type": "string", "enum": ["ENABLED", "PAUSED", "ARCHIVED"]},
                "non_converting": {**_BOOL, "description": "Only targets with clicks > 0 AND orders = 0 (wasted spend)."},
                "high_acos": {**_BOOL, "description": "Only targets with acos > 50% AND spend > 0."},
                "min_clicks": _INTEGER,
                "min_spend": _NUMBER,
                "sort_by": {"type": "string", "enum": ["spend", "sales", "acos", "clicks", "orders", "bid"]},
                "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
                "limit": _INTEGER,
            },
        },
    },
    {
        "name": "db_query_search_terms",
        "description": (
            "Search the locally synced search-term-report cache. Customer search "
            "queries with full per-term metrics. Use for 'what did customers actually "
            "search for?' / 'wasted search terms' / 'terms to harvest as keywords'. "
            "If the date range is outside what's cached the result will say so — "
            "then call _request_sync(kind=search_terms)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {**_STRING, "description": "ISO date YYYY-MM-DD. Required."},
                "end_date": {**_STRING, "description": "ISO date YYYY-MM-DD. Required."},
                "campaign_id": _STRING,
                "term_search": {**_STRING, "description": "Substring match on search_term text."},
                "non_converting": _BOOL,
                "high_acos": _BOOL,
                "min_clicks": _INTEGER,
                "sort_by": {"type": "string", "enum": ["cost", "sales", "acos", "clicks", "purchases"]},
                "sort_dir": {"type": "string", "enum": ["asc", "desc"]},
                "limit": _INTEGER,
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "db_query_performance_trend",
        "description": (
            "Daily account-level performance rows from the local cache. Spend, "
            "sales, ACOS, ROAS, clicks, impressions, orders, CTR, CPC per day."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": _STRING,
                "end_date": _STRING,
                "limit": _INTEGER,
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "db_query_pending_changes",
        "description": (
            "List items currently sitting in the Approval Queue (proposed but "
            "not yet applied). Filter by source / change_type / status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "approved", "rejected", "applied"]},
                "source": {"type": "string", "enum": ["ai_optimizer", "ai_chat", "ai_assistant", "harvest", "bid_rule", "manual"]},
                "change_type": _STRING,
                "limit": _INTEGER,
            },
        },
    },
    # ── MCP live reads — slow, only when DB is stale / user asks ─────
    {
        "name": "mcp_list_campaigns",
        "description": (
            "Live snapshot from Amazon Ads MCP: campaigns across SP/SB/SD with "
            "current state and budget. ~1-5s. Use only when the user explicitly "
            "asks 'right now' / 'live' / 'what's currently active in Amazon' or "
            "when db_query_campaigns returned empty."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ad_product": {"type": "string", "enum": ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]},
                "all_products": {**_BOOL, "description": "Fetch SP+SB+SD in parallel. Default true."},
            },
        },
    },
    {
        "name": "mcp_list_ad_groups",
        "description": (
            "Live ad groups for a campaign from Amazon Ads MCP. ~1-3s. Provide "
            "campaign_id from db_query_campaigns or the context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": _STRING,
                "ad_product": {"type": "string", "enum": ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]},
            },
        },
    },
    {
        "name": "mcp_list_targets",
        "description": (
            "Live targets/keywords from Amazon Ads MCP for a campaign or ad group. "
            "~2-5s for a typical campaign. Returns live state + bid (no perf metrics — "
            "perf comes from db_query_targets)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": _STRING,
                "ad_group_id": _STRING,
                "ad_product": {"type": "string", "enum": ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"]},
            },
        },
    },
]


READ_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in _READ_TOOLS)


def openai_read_tool_specs() -> list[dict[str, Any]]:
    """OpenAI ``tools=[]`` payload for read tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in _READ_TOOLS
    ]


def anthropic_read_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in _READ_TOOLS
    ]


# ── Helpers ──────────────────────────────────────────────────────────


def _scope_profile(query, model, profile_id: Optional[str]):
    """Mirror routers.ai._scope_profile so reads obey the active profile."""
    if not hasattr(model, "profile_id"):
        return query
    if profile_id is not None:
        return query.where(model.profile_id == profile_id)
    return query.where(model.profile_id.is_(None))


def _clamp_limit(value: Any, *, default: int = 25, ceiling: int = DB_ROW_CAP) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, ceiling))


def _campaign_dict(c: Campaign) -> dict:
    spend = c.spend or 0
    sales = c.sales or 0
    return {
        "campaign_id": c.amazon_campaign_id,
        "name": c.campaign_name,
        "type": c.campaign_type,
        "targeting": c.targeting_type,
        "state": c.state,
        "daily_budget": c.daily_budget,
        "spend": spend,
        "sales": sales,
        "acos": c.acos or (round(spend / sales * 100, 1) if sales > 0 else 0),
        "roas": c.roas or (round(sales / spend, 2) if spend > 0 else 0),
        "clicks": c.clicks,
        "impressions": c.impressions,
        "orders": c.orders,
    }


def _target_dict(t: Target) -> dict:
    return {
        "target_id": t.amazon_target_id,
        "ad_group_id": t.amazon_ad_group_id,
        "campaign_id": t.amazon_campaign_id,
        "keyword": t.expression_value,
        "type": t.target_type,
        "match_type": t.match_type,
        "state": t.state,
        "bid": t.bid,
        "spend": t.spend,
        "sales": t.sales,
        "acos": t.acos,
        "clicks": t.clicks,
        "impressions": t.impressions,
        "orders": t.orders,
    }


# ── DB executors ─────────────────────────────────────────────────────


async def _exec_db_query_campaigns(
    args: dict, *, db: AsyncSession, cred: Credential,
) -> dict:
    limit = _clamp_limit(args.get("limit"))
    sort_by = args.get("sort_by") or "spend"
    sort_dir = (args.get("sort_dir") or "desc").lower()

    q = select(Campaign).where(Campaign.credential_id == cred.id)
    q = _scope_profile(q, Campaign, cred.profile_id)
    if args.get("state"):
        q = q.where(Campaign.state == args["state"])
    if args.get("type"):
        q = q.where(Campaign.campaign_type == args["type"])
    if args.get("name_search"):
        q = q.where(Campaign.campaign_name.ilike(f"%{args['name_search']}%"))
    if args.get("min_spend") is not None:
        q = q.where(Campaign.spend >= args["min_spend"])
    if args.get("max_acos") is not None:
        q = q.where(Campaign.acos <= args["max_acos"])
    if args.get("min_acos") is not None:
        q = q.where(Campaign.acos >= args["min_acos"])

    sort_col = {
        "spend": Campaign.spend,
        "sales": Campaign.sales,
        "acos": Campaign.acos,
        "clicks": Campaign.clicks,
        "orders": Campaign.orders,
        "name": Campaign.campaign_name,
    }.get(sort_by, Campaign.spend)
    q = q.order_by(sort_col.asc() if sort_dir == "asc" else sort_col.desc())
    q = q.limit(limit)

    rows = (await db.execute(q)).scalars().all()
    return {
        "source": "db",
        "table": "campaigns",
        "filters": {k: v for k, v in args.items() if v is not None},
        "count": len(rows),
        "rows": [_campaign_dict(c) for c in rows],
    }


async def _exec_db_query_ad_groups(
    args: dict, *, db: AsyncSession, cred: Credential,
) -> dict:
    limit = _clamp_limit(args.get("limit"), default=50, ceiling=DB_ROW_CAP)
    q = select(AdGroup).where(AdGroup.credential_id == cred.id)
    q = _scope_profile(q, AdGroup, cred.profile_id)
    if args.get("campaign_id"):
        q = q.where(AdGroup.amazon_campaign_id == args["campaign_id"])
    if args.get("state"):
        q = q.where(AdGroup.state == args["state"])
    if args.get("name_search"):
        q = q.where(AdGroup.ad_group_name.ilike(f"%{args['name_search']}%"))
    q = q.limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {
        "source": "db",
        "table": "ad_groups",
        "count": len(rows),
        "rows": [
            {
                "ad_group_id": ag.amazon_ad_group_id,
                "campaign_id": ag.amazon_campaign_id,
                "name": ag.ad_group_name,
                "state": ag.state,
                "default_bid": ag.default_bid,
            }
            for ag in rows
        ],
    }


async def _exec_db_query_targets(
    args: dict, *, db: AsyncSession, cred: Credential,
) -> dict:
    limit = _clamp_limit(args.get("limit"), default=30)
    sort_by = args.get("sort_by") or "spend"
    sort_dir = (args.get("sort_dir") or "desc").lower()

    q = select(Target).where(Target.credential_id == cred.id)
    q = _scope_profile(q, Target, cred.profile_id)
    if args.get("ad_group_id"):
        q = q.where(Target.amazon_ad_group_id == args["ad_group_id"])
    if args.get("campaign_id"):
        q = q.where(Target.amazon_campaign_id == args["campaign_id"])
    if args.get("keyword_search"):
        q = q.where(Target.expression_value.ilike(f"%{args['keyword_search']}%"))
    if args.get("match_type"):
        q = q.where(Target.match_type == args["match_type"])
    if args.get("state"):
        q = q.where(Target.state == args["state"])
    if args.get("non_converting"):
        q = q.where(and_(Target.clicks > 0, or_(Target.orders == 0, Target.orders.is_(None))))
    if args.get("high_acos"):
        q = q.where(and_(Target.acos > 50, Target.spend > 0))
    if args.get("min_clicks") is not None:
        q = q.where(Target.clicks >= args["min_clicks"])
    if args.get("min_spend") is not None:
        q = q.where(Target.spend >= args["min_spend"])

    sort_col = {
        "spend": Target.spend,
        "sales": Target.sales,
        "acos": Target.acos,
        "clicks": Target.clicks,
        "orders": Target.orders,
        "bid": Target.bid,
    }.get(sort_by, Target.spend)
    q = q.order_by(sort_col.asc() if sort_dir == "asc" else sort_col.desc())
    q = q.limit(limit)

    rows = (await db.execute(q)).scalars().all()
    return {
        "source": "db",
        "table": "targets",
        "filters": {k: v for k, v in args.items() if v is not None},
        "count": len(rows),
        "rows": [_target_dict(t) for t in rows],
    }


async def _exec_db_query_search_terms(
    args: dict, *, db: AsyncSession, cred: Credential,
) -> dict:
    limit = _clamp_limit(args.get("limit"), default=30)
    sort_by = args.get("sort_by") or "cost"
    sort_dir = (args.get("sort_dir") or "desc").lower()
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    if not start_date or not end_date:
        return {"source": "db", "table": "search_terms", "error": "start_date and end_date are required (YYYY-MM-DD)."}

    q = select(SearchTermPerformance).where(SearchTermPerformance.credential_id == cred.id)
    q = _scope_profile(q, SearchTermPerformance, cred.profile_id)
    q = q.where(and_(
        SearchTermPerformance.date >= start_date,
        SearchTermPerformance.date <= end_date,
    ))
    if args.get("campaign_id"):
        q = q.where(SearchTermPerformance.amazon_campaign_id == args["campaign_id"])
    if args.get("term_search"):
        q = q.where(SearchTermPerformance.search_term.ilike(f"%{args['term_search']}%"))
    if args.get("non_converting"):
        q = q.where(and_(
            SearchTermPerformance.clicks > 0,
            or_(SearchTermPerformance.purchases == 0, SearchTermPerformance.purchases.is_(None)),
        ))
    if args.get("high_acos"):
        q = q.where(and_(SearchTermPerformance.acos > 50, SearchTermPerformance.cost > 0))
    if args.get("min_clicks") is not None:
        q = q.where(SearchTermPerformance.clicks >= args["min_clicks"])

    sort_col = {
        "cost": SearchTermPerformance.cost,
        "sales": SearchTermPerformance.sales,
        "acos": SearchTermPerformance.acos,
        "clicks": SearchTermPerformance.clicks,
        "purchases": SearchTermPerformance.purchases,
    }.get(sort_by, SearchTermPerformance.cost)
    q = q.order_by(sort_col.asc() if sort_dir == "asc" else sort_col.desc())
    q = q.limit(limit)

    rows = (await db.execute(q)).scalars().all()
    if not rows:
        # Empty range — tell the model to call _request_sync rather than guess.
        return {
            "source": "db",
            "table": "search_terms",
            "count": 0,
            "rows": [],
            "hint": (
                f"No search-term rows cached for {start_date}..{end_date}. "
                "Call _request_sync(kind='search_terms') with an appropriate "
                "range_preset to fetch this window."
            ),
        }
    return {
        "source": "db",
        "table": "search_terms",
        "date_range": f"{start_date}..{end_date}",
        "count": len(rows),
        "rows": [
            {
                "date": st.date,
                "search_term": st.search_term,
                "campaign_id": st.amazon_campaign_id,
                "campaign_name": st.campaign_name,
                "ad_group_id": st.amazon_ad_group_id,
                "keyword": st.keyword,
                "match_type": st.match_type,
                "impressions": st.impressions,
                "clicks": st.clicks,
                "cost": st.cost,
                "sales": st.sales,
                "purchases": st.purchases,
                "acos": st.acos,
            }
            for st in rows
        ],
    }


async def _exec_db_query_performance_trend(
    args: dict, *, db: AsyncSession, cred: Credential,
) -> dict:
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    if not start_date or not end_date:
        # Default: last 30 days.
        end_date = utcnow().strftime("%Y-%m-%d")
        start_date = (utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    limit = _clamp_limit(args.get("limit"), default=90, ceiling=180)

    q = select(AccountPerformanceDaily).where(
        AccountPerformanceDaily.credential_id == cred.id,
        AccountPerformanceDaily.date >= start_date,
        AccountPerformanceDaily.date <= end_date,
    )
    q = _scope_profile(q, AccountPerformanceDaily, cred.profile_id)
    q = q.order_by(AccountPerformanceDaily.date.asc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {
        "source": "db",
        "table": "account_performance_daily",
        "date_range": f"{start_date}..{end_date}",
        "count": len(rows),
        "rows": [
            {
                "date": r.date,
                "spend": r.total_spend,
                "sales": r.total_sales,
                "impressions": r.total_impressions,
                "clicks": r.total_clicks,
                "orders": r.total_orders,
                "acos": r.avg_acos,
                "roas": r.avg_roas,
                "ctr": r.avg_ctr,
                "cpc": r.avg_cpc,
            }
            for r in rows
        ],
    }


async def _exec_db_query_pending_changes(
    args: dict, *, db: AsyncSession, cred: Credential,
) -> dict:
    limit = _clamp_limit(args.get("limit"), default=25)
    q = select(PendingChange).where(PendingChange.credential_id == cred.id)
    q = _scope_profile(q, PendingChange, cred.profile_id)
    if args.get("status"):
        q = q.where(PendingChange.status == args["status"])
    else:
        q = q.where(PendingChange.status == "pending")
    if args.get("source"):
        q = q.where(PendingChange.source == args["source"])
    if args.get("change_type"):
        q = q.where(PendingChange.change_type == args["change_type"])
    q = q.order_by(PendingChange.created_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {
        "source": "db",
        "table": "pending_changes",
        "count": len(rows),
        "rows": [
            {
                "id": str(pc.id),
                "type": pc.change_type,
                "entity_type": pc.entity_type,
                "entity_name": pc.entity_name,
                "campaign_name": pc.campaign_name,
                "current_value": pc.current_value,
                "proposed_value": pc.proposed_value,
                "source": pc.source,
                "ai_reasoning": (pc.ai_reasoning or "")[:240],
                "confidence": pc.confidence,
                "estimated_impact": pc.estimated_impact,
                "created_at": pc.created_at.isoformat() if pc.created_at else None,
            }
            for pc in rows
        ],
    }


# ── MCP executors (live, ~1-5s) ──────────────────────────────────────


def _truncate_mcp_rows(items: list, key: str) -> dict:
    capped = list(items)[:MCP_ROW_CAP]
    return {
        "source": "mcp",
        "key": key,
        "count": len(capped),
        "total_returned_by_mcp": len(items),
        "truncated": len(items) > MCP_ROW_CAP,
        "rows": capped,
    }


async def _exec_mcp_list_campaigns(args: dict, *, mcp_client) -> dict:
    if mcp_client is None:
        return {"error": "MCP client unavailable for this credential."}
    ad_product = args.get("ad_product")
    all_products = args.get("all_products", True) and not ad_product
    result = await mcp_client.query_campaigns(ad_product=ad_product, all_products=all_products)
    return _truncate_mcp_rows(result.get("campaigns") or [], "campaigns")


async def _exec_mcp_list_ad_groups(args: dict, *, mcp_client) -> dict:
    if mcp_client is None:
        return {"error": "MCP client unavailable for this credential."}
    result = await mcp_client.query_ad_groups(
        campaign_id=args.get("campaign_id"),
        ad_product=args.get("ad_product") or "SPONSORED_PRODUCTS",
    )
    return _truncate_mcp_rows(result.get("adGroups") or [], "adGroups")


async def _exec_mcp_list_targets(args: dict, *, mcp_client) -> dict:
    if mcp_client is None:
        return {"error": "MCP client unavailable for this credential."}
    result = await mcp_client.query_targets(
        campaign_id=args.get("campaign_id"),
        ad_group_id=args.get("ad_group_id"),
        ad_product=args.get("ad_product") or "SPONSORED_PRODUCTS",
    )
    return _truncate_mcp_rows(result.get("targets") or [], "targets")


# ── Dispatch ─────────────────────────────────────────────────────────


_DB_DISPATCH = {
    "db_query_campaigns": _exec_db_query_campaigns,
    "db_query_ad_groups": _exec_db_query_ad_groups,
    "db_query_targets": _exec_db_query_targets,
    "db_query_search_terms": _exec_db_query_search_terms,
    "db_query_performance_trend": _exec_db_query_performance_trend,
    "db_query_pending_changes": _exec_db_query_pending_changes,
}

_MCP_DISPATCH = {
    "mcp_list_campaigns": _exec_mcp_list_campaigns,
    "mcp_list_ad_groups": _exec_mcp_list_ad_groups,
    "mcp_list_targets": _exec_mcp_list_targets,
}


def build_tool_executor(
    *,
    db: AsyncSession,
    cred: Credential,
    mcp_client_factory: Optional[Callable[[], Awaitable[Any]]] = None,
) -> ToolExecutor:
    """Return a callable ``(name, args) -> dict`` for the chat loop.

    ``mcp_client_factory`` is awaited lazily on the first ``mcp_*`` call
    so we don't pay token-refresh cost on chats that only hit the DB
    cache. A ``None`` factory disables MCP reads (still returns a
    structured ``{"error": ...}`` so the model can react).
    """
    mcp_client_holder: dict[str, Any] = {}

    async def _get_mcp_client():
        if mcp_client_factory is None:
            return None
        if "client" not in mcp_client_holder:
            try:
                mcp_client_holder["client"] = await mcp_client_factory()
            except Exception as exc:
                logger.warning("MCP client factory failed: %s", exc)
                mcp_client_holder["client"] = None
        return mcp_client_holder["client"]

    async def _execute(name: str, args: dict) -> dict:
        args = args or {}
        try:
            if name in _DB_DISPATCH:
                return await _DB_DISPATCH[name](args, db=db, cred=cred)
            if name in _MCP_DISPATCH:
                client = await _get_mcp_client()
                return await _MCP_DISPATCH[name](args, mcp_client=client)
        except Exception as exc:
            logger.exception("read tool %s failed", name)
            return {"error": f"{name} failed: {str(exc)[:240]}"}
        return {"error": f"Unknown read tool: {name}"}

    return _execute


__all__ = [
    "READ_TOOL_NAMES",
    "RESULT_CHAR_CAP",
    "openai_read_tool_specs",
    "anthropic_read_tool_specs",
    "build_tool_executor",
    "ToolExecutor",
]
