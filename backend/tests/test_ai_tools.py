"""Tests for app.services.ai_tools.

Phase 5.2 introduces native tool/function-calling so the model can no
longer accidentally emit ``"$1.50"`` for a numeric ``bid``. These tests
cover:

* Provider-specific spec shape (OpenAI vs Anthropic).
* Tool name allow-list — unknown tools are dropped.
* Argument coercion: JSON-encoded strings (OpenAI) vs already-parsed
  dicts (Anthropic).
* Numeric defence-in-depth: ``"$1.50"`` → 1.5 even when the schema
  somehow lets a string through.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import ai_tools  # noqa: E402


def test_openai_specs_have_function_envelope():
    specs = ai_tools.openai_tool_specs()
    assert specs, "expected at least one tool"
    for s in specs:
        assert s["type"] == "function"
        assert "function" in s
        fn = s["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"


def test_anthropic_specs_use_input_schema_envelope():
    specs = ai_tools.anthropic_tool_specs()
    assert specs
    for s in specs:
        assert "name" in s
        assert "input_schema" in s
        assert "parameters" not in s, "Anthropic schema must use input_schema"


def test_tool_call_to_action_passthrough_for_dict_args():
    args = {
        "body": {
            "targets": [
                {"targetId": "t-1", "bid": 0.5, "state": "ENABLED"}
            ]
        }
    }
    action = ai_tools.tool_call_to_action(
        "campaign_management-update_target_bid", args
    )
    assert action == {
        "tool": "campaign_management-update_target_bid",
        "arguments": args,
        "scope": "inline",
    }


def test_tool_call_to_action_decodes_json_string_args():
    raw = json.dumps({
        "body": {"campaigns": [{"campaignId": "c-1", "dailyBudget": 25}]}
    })
    action = ai_tools.tool_call_to_action(
        "campaign_management-update_campaign_budget", raw
    )
    assert action is not None
    assert action["arguments"]["body"]["campaigns"][0]["dailyBudget"] == 25


def test_tool_call_to_action_unknown_tool_returns_none():
    assert ai_tools.tool_call_to_action("does_not_exist", {}) is None


def test_tool_call_to_action_coerces_currency_strings_in_bids():
    args = {
        "body": {
            "targets": [
                {"targetId": "t-1", "bid": "$1.50"},
                {"targetId": "t-2", "bid": "1,200"},
            ]
        }
    }
    action = ai_tools.tool_call_to_action(
        "campaign_management-update_target_bid", args
    )
    assert action is not None
    targets = action["arguments"]["body"]["targets"]
    assert targets[0]["bid"] == 1.5
    assert targets[1]["bid"] == 1200


def test_tool_call_to_action_coerces_plan_budget_and_default_bids():
    args = {
        "plan": {
            "campaign": {"name": "X", "dailyBudget": "$50"},
            "ad_groups": [
                {
                    "name": "AG1",
                    "defaultBid": "0.75",
                    "keywords": [
                        {"text": "kw1", "match_type": "EXACT", "suggested_bid": "$0.30"}
                    ],
                }
            ],
        }
    }
    action = ai_tools.tool_call_to_action("_ai_campaign_create", args)
    assert action is not None
    plan = action["arguments"]["plan"]
    assert plan["campaign"]["dailyBudget"] == 50
    ag = plan["ad_groups"][0]
    assert ag["defaultBid"] == 0.75
    assert ag["keywords"][0]["suggested_bid"] == 0.30


def test_request_sync_tool_is_queue_scoped():
    action = ai_tools.tool_call_to_action(
        "_request_sync",
        {"kind": "reports", "range_preset": "last_7_days"},
    )
    assert action is not None
    assert action["scope"] == "queue"
    assert action["arguments"]["kind"] == "reports"


def test_tool_calls_to_actions_filters_unknown_tools():
    actions = ai_tools.tool_calls_to_actions([
        ("campaign_management-update_target_bid", {"body": {"targets": [{"targetId": "t1", "bid": 1.0}]}}),
        ("not_a_real_tool", {}),
    ])
    assert len(actions) == 1
    assert actions[0]["tool"] == "campaign_management-update_target_bid"


def test_tool_call_to_action_handles_invalid_json_string():
    action = ai_tools.tool_call_to_action(
        "campaign_management-update_target_bid", "{not valid json"
    )
    assert action is not None
    assert action["arguments"] == {}
