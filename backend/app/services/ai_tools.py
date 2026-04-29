"""Native LLM tool definitions for the AI assistant.

Phase 5.2 replaces the brittle ``[ACTIONS]`` regex protocol with native
function/tool calling on both OpenAI and Anthropic. The model can no
longer accidentally emit ``"$1.50"`` for a numeric ``bid`` field — the
provider validates the schema before the response reaches us.

Design
======

* Tool **names** match exactly what ``ai_action_validator`` /
  ``app.utils.normalize_mcp_call`` expect (e.g.
  ``campaign_management-update_target_bid``).
* Tool **arguments** mirror the eventual MCP body so a tool call can be
  converted directly into the existing ``action = {tool, arguments}``
  shape with **zero extra parsing**.
* Bid / budget arguments are typed as ``number`` so the model has to
  emit a real numeric token — the OpenAI / Anthropic SDK rejects
  ``"$1.50"`` outright.
* We also expose two *queue-only* synthetic tools (``_request_sync``,
  ``_ai_campaign_create``, ``_harvest_execute``) so the model can ask
  the UI to do things the validator already understands.

The exported helpers cover both providers:

* :func:`openai_tool_specs` returns the OpenAI ``tools=[]`` payload.
* :func:`anthropic_tool_specs` returns the Anthropic ``tools=[]``
  payload (slightly different envelope: ``input_schema`` not
  ``parameters``).
* :func:`tool_call_to_action` converts a single provider-emitted tool
  call into the dict the rest of the AI pipeline already understands.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Tool schemas (provider-agnostic) ─────────────────────────────────

_NUMBER = {"type": "number"}
_STRING = {"type": "string"}

_INLINE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "campaign_management-update_target_bid",
        "description": (
            "Change the bid on one or more existing keyword/target IDs. "
            "Use the 'id:' values from the provided context. Bids must be "
            "in account currency."
        ),
        "scope": "inline",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {
                        "targets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "targetId": _STRING,
                                    "bid": _NUMBER,
                                    "state": {
                                        "type": "string",
                                        "enum": ["ENABLED", "PAUSED", "ARCHIVED"],
                                    },
                                },
                                "required": ["targetId"],
                            },
                            "minItems": 1,
                        }
                    },
                    "required": ["targets"],
                },
                "label": _STRING,
                "reason": _STRING,
            },
            "required": ["body"],
        },
    },
    {
        "name": "campaign_management-update_campaign_budget",
        "description": "Set a new daily budget on one or more campaigns.",
        "scope": "inline",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {
                        "campaigns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "campaignId": _STRING,
                                    "dailyBudget": _NUMBER,
                                },
                                "required": ["campaignId", "dailyBudget"],
                            },
                            "minItems": 1,
                        }
                    },
                    "required": ["campaigns"],
                }
            },
            "required": ["body"],
        },
    },
    {
        "name": "campaign_management-update_campaign_state",
        "description": "Pause / enable / archive one or more campaigns.",
        "scope": "inline",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {
                        "campaigns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "campaignId": _STRING,
                                    "state": {
                                        "type": "string",
                                        "enum": ["ENABLED", "PAUSED", "ARCHIVED"],
                                    },
                                },
                                "required": ["campaignId", "state"],
                            },
                            "minItems": 1,
                        }
                    },
                    "required": ["campaigns"],
                }
            },
            "required": ["body"],
        },
    },
    {
        "name": "campaign_management-update_campaign",
        "description": "Rename or update non-budget fields on a campaign.",
        "scope": "inline",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {
                        "campaigns": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "campaignId": _STRING,
                                    "name": _STRING,
                                    "dailyBudget": _NUMBER,
                                    "state": {
                                        "type": "string",
                                        "enum": ["ENABLED", "PAUSED", "ARCHIVED"],
                                    },
                                },
                                "required": ["campaignId"],
                            },
                            "minItems": 1,
                        }
                    },
                    "required": ["campaigns"],
                }
            },
            "required": ["body"],
        },
    },
    {
        "name": "campaign_management-update_ad_group",
        "description": "Rename / change default bid / state of an ad group.",
        "scope": "inline",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {
                        "adGroups": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "adGroupId": _STRING,
                                    "name": _STRING,
                                    "defaultBid": _NUMBER,
                                    "state": {
                                        "type": "string",
                                        "enum": ["ENABLED", "PAUSED", "ARCHIVED"],
                                    },
                                },
                                "required": ["adGroupId"],
                            },
                            "minItems": 1,
                        }
                    },
                    "required": ["adGroups"],
                }
            },
            "required": ["body"],
        },
    },
    {
        "name": "campaign_management-create_target",
        "description": (
            "Add a new keyword/target to an existing ad group. "
            "Always provide campaignId + adGroupId from context."
        ),
        "scope": "inline",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {
                        "targets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "campaignId": _STRING,
                                    "adGroupId": _STRING,
                                    "expression": _STRING,
                                    "expressionType": {
                                        "type": "string",
                                        "enum": ["KEYWORD", "PRODUCT_CATEGORY", "ASIN_SAME_AS"],
                                    },
                                    "matchType": {
                                        "type": "string",
                                        "enum": ["EXACT", "PHRASE", "BROAD"],
                                    },
                                    "bid": _NUMBER,
                                    "state": {
                                        "type": "string",
                                        "enum": ["ENABLED", "PAUSED"],
                                    },
                                },
                                "required": ["campaignId", "adGroupId", "expression", "matchType"],
                            },
                            "minItems": 1,
                        }
                    },
                    "required": ["targets"],
                }
            },
            "required": ["body"],
        },
    },
    {
        "name": "campaign_management-delete_target",
        "description": "Permanently delete one or more keyword/target IDs.",
        "scope": "inline",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {
                        "targetIds": {
                            "type": "array",
                            "items": _STRING,
                            "minItems": 1,
                        }
                    },
                    "required": ["targetIds"],
                }
            },
            "required": ["body"],
        },
    },
]


_QUEUE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "_request_sync",
        "description": (
            "Ask the UI to refresh stale data. Non-mutating. Use when the "
            "user asks about data the context says is missing or stale."
        ),
        "scope": "queue",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["campaigns", "reports", "search_terms", "products"],
                },
                "range_preset": {
                    "type": "string",
                    "enum": [
                        "today", "yesterday", "last_7_days", "last_30_days",
                        "this_week", "last_week", "this_month", "last_month",
                        "month_to_yesterday", "year_to_date",
                    ],
                },
                "reason": _STRING,
            },
            "required": ["kind"],
        },
    },
    {
        "name": "_harvest_execute",
        "description": (
            "Queue a keyword-harvest run from a source (auto) campaign into "
            "an existing or freshly created destination campaign."
        ),
        "scope": "queue",
        "parameters": {
            "type": "object",
            "properties": {
                "source_campaign_id": _STRING,
                "target_mode": {"type": "string", "enum": ["new", "existing"]},
                "target_campaign_id": _STRING,
                "match_type": {
                    "type": "string",
                    "enum": ["EXACT", "PHRASE", "BROAD"],
                },
                "sales_threshold": _NUMBER,
                "acos_threshold": _NUMBER,
            },
            "required": ["source_campaign_id"],
        },
    },
    {
        "name": "_ai_campaign_create",
        "description": (
            "Queue creation of a brand-new campaign from a structured plan. "
            "Use 'plan' object with campaign + ad_groups + keywords."
        ),
        "scope": "queue",
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "object",
                    "properties": {
                        "campaign": {
                            "type": "object",
                            "properties": {
                                "name": _STRING,
                                "adProduct": {
                                    "type": "string",
                                    "enum": [
                                        "SPONSORED_PRODUCTS",
                                        "SPONSORED_BRANDS",
                                        "SPONSORED_DISPLAY",
                                    ],
                                },
                                "targetingType": {
                                    "type": "string",
                                    "enum": ["AUTO", "MANUAL"],
                                },
                                "dailyBudget": _NUMBER,
                            },
                            "required": ["name", "dailyBudget"],
                        },
                        "ad": {
                            "type": "object",
                            "properties": {"asin": _STRING},
                        },
                        "ad_groups": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": _STRING,
                                    "defaultBid": _NUMBER,
                                    "keywords": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "text": _STRING,
                                                "match_type": {
                                                    "type": "string",
                                                    "enum": ["EXACT", "PHRASE", "BROAD"],
                                                },
                                                "suggested_bid": _NUMBER,
                                            },
                                            "required": ["text"],
                                        },
                                    },
                                },
                                "required": ["name", "defaultBid"],
                            },
                            "minItems": 1,
                        },
                    },
                    "required": ["campaign", "ad_groups"],
                }
            },
            "required": ["plan"],
        },
    },
]


ALL_TOOLS: list[dict[str, Any]] = _INLINE_TOOLS + _QUEUE_TOOLS
TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in ALL_TOOLS)


def _scope_for(name: str) -> str:
    for t in ALL_TOOLS:
        if t["name"] == name:
            return t.get("scope", "queue")
    return "queue"


# ── Provider-specific spec builders ───────────────────────────────────

def openai_tool_specs() -> list[dict[str, Any]]:
    """Tool definitions in OpenAI ``chat.completions`` format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in ALL_TOOLS
    ]


def anthropic_tool_specs() -> list[dict[str, Any]]:
    """Tool definitions in Anthropic ``messages`` format."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in ALL_TOOLS
    ]


# ── Tool-call → action converter ─────────────────────────────────────

def _coerce_arguments(raw: Any) -> dict[str, Any]:
    """Turn provider tool-call arguments into a dict.

    OpenAI returns a JSON-encoded string; Anthropic returns a parsed
    dict. Numeric values that arrived as strings (e.g. ``"$1.50"``) are
    coerced where the schema expects numbers — defence in depth on top
    of the strict schema enforcement.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            decoded = json.loads(s)
        except json.JSONDecodeError:
            logger.warning("Tool call arguments are not valid JSON: %s", s[:200])
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _strip_currency(value: Any) -> Any:
    """Best-effort numeric coercion — handles ``"$1.50"`` / ``"1,200"``.

    The provider schema *should* prevent this, but Anthropic occasionally
    coerces ``"1.50"`` → string. We re-parse so the validator doesn't
    reject a structurally-fine action over a token quirk.
    """
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace(",", "")
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return value
    return value


_NUMERIC_PATHS: tuple[tuple[str, ...], ...] = (
    ("body", "targets", "*", "bid"),
    ("body", "campaigns", "*", "dailyBudget"),
    ("body", "adGroups", "*", "defaultBid"),
    ("plan", "campaign", "dailyBudget"),
    ("plan", "ad_groups", "*", "defaultBid"),
    ("plan", "ad_groups", "*", "keywords", "*", "suggested_bid"),
    ("sales_threshold",),
    ("acos_threshold",),
)


def _walk(node: Any, path: tuple[str, ...]) -> None:
    if not path or node is None:
        return
    head, *rest = path
    if head == "*":
        if isinstance(node, list):
            for item in node:
                _walk(item, tuple(rest))
        return
    if isinstance(node, dict):
        if not rest:
            if head in node:
                node[head] = _strip_currency(node[head])
            return
        _walk(node.get(head), tuple(rest))


def _coerce_numerics(args: dict[str, Any]) -> dict[str, Any]:
    for path in _NUMERIC_PATHS:
        _walk(args, path)
    return args


def tool_call_to_action(name: str, raw_arguments: Any) -> dict[str, Any] | None:
    """Convert a single provider tool call into the action shape.

    Returns ``None`` when the tool is not in the allow-list — the
    caller should drop / log such calls instead of forwarding them.
    """
    if name not in TOOL_NAMES:
        logger.warning("Ignoring tool call for unknown tool %r", name)
        return None
    args = _coerce_arguments(raw_arguments)
    args = _coerce_numerics(args)
    return {
        "tool": name,
        "arguments": args,
        "scope": _scope_for(name),
    }


def tool_calls_to_actions(
    calls: Iterable[tuple[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Batch convert ``[(name, raw_args), ...]`` to a list of actions."""
    out: list[dict[str, Any]] = []
    for name, raw in calls or []:
        action = tool_call_to_action(name, raw)
        if action is not None:
            out.append(action)
    return out
