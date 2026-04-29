"""Two-phase tool result shrinker for the AI assistant.

Why
===

Amazon Ads MCP read tools return *huge* payloads — a single
``query_targets`` on a 30k-keyword account can be megabytes, and
``reporting-create_campaign_report`` blows the OpenAI / Anthropic
context window in one go. We want to:

1. Keep the **full** result available for ML / anomaly / drift /
   storage paths (Phase 3 ML hooks need every row).
2. Hand the LLM a **shrunken** version that fits the context budget
   without dropping the headline numbers, top-N samples, and a
   schema-of-keys preview.

Adsynth solves this with ``tool_llm_payload.shrink_tool_result_for_llm``
keyed on tool name. We port the same shape here, Amazon-flavoured.

Caller pattern::

    full_result = await client.call_tool("campaign_management-query_target", body)
    shrunken = shrink_tool_result_for_llm(
        tool="campaign_management-query_target",
        result=full_result,
        max_bytes=20_000,
        max_rows=25,
    )
    # full_result still goes to the DB / ML pipeline; shrunken goes to the LLM
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# Default budgets chosen so a single shrunken tool result + prompt
# overhead fits comfortably under 30k tokens for both providers.
DEFAULT_MAX_ROWS = 25
DEFAULT_MAX_BYTES = 20_000
DEFAULT_MAX_STRING = 280


# Per-tool the most informative *list* key — the rest of the structure
# is preserved verbatim.
_LIST_KEY_BY_TOOL: dict[str, str] = {
    "campaign_management-query_campaign": "campaigns",
    "campaign_management-query_ad_group": "adGroups",
    "campaign_management-query_target": "targets",
    "campaign_management-query_ad": "ads",
    "reporting-create_campaign_report": "campaigns",
    "reporting-create_search_term_report": "searchTerms",
    "reporting-create_product_report": "products",
}

_FALLBACK_LIST_KEYS: tuple[str, ...] = (
    "campaigns", "adGroups", "targets", "ads", "rows", "items",
    "data", "results", "searchTerms", "products",
)


def _truncate_string(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: max(0, limit - 1)] + "…"
    return value


def _truncate_strings_in_dict(d: dict, limit: int) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = _truncate_string(v, limit)
        elif isinstance(v, dict):
            out[k] = _truncate_strings_in_dict(v, limit)
        elif isinstance(v, list):
            out[k] = [
                _truncate_strings_in_dict(item, limit)
                if isinstance(item, dict) else _truncate_string(item, limit)
                for item in v
            ]
        else:
            out[k] = v
    return out


def _pick_list_key(tool: str, result: dict) -> Optional[str]:
    if tool in _LIST_KEY_BY_TOOL and isinstance(result.get(_LIST_KEY_BY_TOOL[tool]), list):
        return _LIST_KEY_BY_TOOL[tool]
    for key in _FALLBACK_LIST_KEYS:
        if isinstance(result.get(key), list):
            return key
    # Heuristic last resort: the longest list value
    longest_key, longest_len = None, 0
    for k, v in result.items():
        if isinstance(v, list) and len(v) > longest_len:
            longest_key, longest_len = k, len(v)
    return longest_key


def _summarize_rows(rows: list[Any]) -> dict:
    """Return a small "schema preview" + counts that survive truncation."""
    if not rows:
        return {"row_count": 0, "schema_keys": []}
    sample = next((r for r in rows if isinstance(r, dict)), None)
    keys = sorted(sample.keys()) if isinstance(sample, dict) else []
    return {
        "row_count": len(rows),
        "schema_keys": keys,
    }


def shrink_tool_result_for_llm(
    tool: str,
    result: Any,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_string: int = DEFAULT_MAX_STRING,
) -> Any:
    """Trim a single MCP tool result to fit the LLM context budget.

    Behaviour:

    * Lists in known "rows" keys are truncated to ``max_rows`` items
      and annotated with ``_truncated_from`` + ``schema_keys`` so the
      model knows the original size.
    * Long string fields are clipped to ``max_string`` chars.
    * The whole dict is JSON-serialised; if still over ``max_bytes``,
      we fall back to a *summary-only* envelope (top-level keys + row
      counts + the first ``max_rows / 2`` items).
    * Non-dict / non-list inputs are returned unchanged (already
      small enough to ship verbatim).
    """
    if not isinstance(result, (dict, list)):
        return result

    if isinstance(result, list):
        # Wrap so we can use the same dict logic
        wrapped = {"_list": result}
        shrunk = shrink_tool_result_for_llm(
            tool, wrapped, max_rows=max_rows, max_bytes=max_bytes, max_string=max_string,
        )
        return shrunk.get("_list", shrunk)

    out = dict(result)
    list_key = _pick_list_key(tool, out)
    if list_key:
        rows = out[list_key]
        if isinstance(rows, list) and len(rows) > max_rows:
            preview = _summarize_rows(rows)
            out[list_key] = rows[:max_rows]
            out[f"_{list_key}_meta"] = {
                "_truncated_from": preview["row_count"],
                "_kept": max_rows,
                "_schema_keys": preview["schema_keys"],
            }

    # Truncate stringy fields throughout
    out = _truncate_strings_in_dict(out, max_string)

    encoded = json.dumps(out, default=str)
    if len(encoded) <= max_bytes:
        return out

    # Fallback: keep only a "summary envelope" — top-level keys, list
    # counts, and a half-cap of the picked list.
    summary: dict[str, Any] = {"_truncated": True, "_byte_budget": max_bytes}
    for k, v in result.items():
        if isinstance(v, list):
            summary[k] = v[: max(1, max_rows // 2)]
            summary[f"_{k}_meta"] = {
                "_truncated_from": len(v),
                "_kept": min(len(v), max(1, max_rows // 2)),
                "_schema_keys": _summarize_rows(v).get("schema_keys", []),
            }
        elif isinstance(v, dict):
            summary[k] = {"_keys": sorted(v.keys())[:50]}
        elif isinstance(v, str):
            summary[k] = _truncate_string(v, max_string)
        else:
            summary[k] = v
    return summary


def shrink_many(
    items: Iterable[tuple[str, Any]],
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_string: int = DEFAULT_MAX_STRING,
) -> list[Any]:
    """Apply :func:`shrink_tool_result_for_llm` to a sequence of results."""
    return [
        shrink_tool_result_for_llm(
            tool, result,
            max_rows=max_rows, max_bytes=max_bytes, max_string=max_string,
        )
        for tool, result in items
    ]


__all__ = [
    "shrink_tool_result_for_llm",
    "shrink_many",
    "DEFAULT_MAX_ROWS",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_STRING",
]
