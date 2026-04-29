"""Tests for app.services.ai_action_validator."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import ai_action_validator as validator  # noqa: E402


@pytest.fixture
def cred():
    return SimpleNamespace(id=uuid.uuid4(), profile_id="prof-1")


@pytest.fixture
def db_with_existing_ids(monkeypatch):
    """Patch DB lookups so all entity IDs are 'found'."""
    monkeypatch.setattr(validator, "_target_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(validator, "_campaign_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(validator, "_ad_group_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(validator, "_ad_exists", AsyncMock(return_value=True))
    return AsyncMock(name="db")


@pytest.fixture
def db_missing_ids(monkeypatch):
    monkeypatch.setattr(validator, "_target_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(validator, "_campaign_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(validator, "_ad_group_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(validator, "_ad_exists", AsyncMock(return_value=False))
    return AsyncMock(name="db")


# ── Smoke ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejects_non_dict_action(db_with_existing_ids, cred):
    result = await validator.validate_ai_action("oops", db_with_existing_ids, cred)
    assert not result.ok
    assert "not a dict" in result.error


@pytest.mark.asyncio
async def test_rejects_missing_tool(db_with_existing_ids, cred):
    result = await validator.validate_ai_action({"arguments": {}}, db_with_existing_ids, cred)
    assert not result.ok
    assert "tool" in result.error


@pytest.mark.asyncio
async def test_rejects_unknown_tool(db_with_existing_ids, cred):
    action = {"tool": "campaign_management-do_evil", "arguments": {"body": {}}}
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert not result.ok
    assert "not permitted" in result.error


@pytest.mark.asyncio
async def test_rejects_inline_only_for_queue_tool(db_with_existing_ids, cred):
    action = {"tool": "_harvest_execute", "arguments": {"source_campaign_id": "c1"}}
    result = await validator.validate_ai_action(
        action, db_with_existing_ids, cred, allow_queue_only_tools=False,
    )
    assert not result.ok
    assert "queue-only" in result.error


@pytest.mark.asyncio
async def test_passes_queue_tool_in_queue_mode(db_with_existing_ids, cred):
    action = {"tool": "_harvest_execute", "arguments": {"source_campaign_id": "c1"}}
    result = await validator.validate_ai_action(
        action, db_with_existing_ids, cred, allow_queue_only_tools=True,
    )
    assert result.ok
    assert result.tool == "_harvest_execute"


# ── Bid update ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_target_bid_update_ok(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-update_target_bid",
        "arguments": {"body": {"targets": [{"targetId": "t1", "bid": "0.85"}]}},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert result.ok, result.error
    assert result.arguments["body"]["targets"][0]["bid"] == 0.85


@pytest.mark.asyncio
async def test_target_bid_below_minimum_rejected(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-update_target_bid",
        "arguments": {"body": {"targets": [{"targetId": "t1", "bid": 0.001}]}},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert not result.ok
    assert "minimum" in result.error


@pytest.mark.asyncio
async def test_target_bid_above_max_rejected(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-update_target_bid",
        "arguments": {"body": {"targets": [{"targetId": "t1", "bid": 9999}]}},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert not result.ok
    assert "maximum" in result.error


@pytest.mark.asyncio
async def test_target_bid_unknown_id_rejected(db_missing_ids, cred):
    action = {
        "tool": "campaign_management-update_target_bid",
        "arguments": {"body": {"targets": [{"targetId": "phantom", "bid": 1.0}]}},
    }
    result = await validator.validate_ai_action(action, db_missing_ids, cred)
    assert not result.ok
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_unflattened_target_bid_normalized(db_with_existing_ids, cred):
    """``normalize_mcp_arguments`` should turn a flat ``{targetId, bid}`` into ``{body:{targets:[…]}}``."""
    action = {
        "tool": "campaign_management-update_target_bid",
        "arguments": {"targetId": "t1", "bid": 0.5},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert result.ok, result.error
    assert result.arguments["body"]["targets"][0]["targetId"] == "t1"


# ── Budget update ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_update_ok(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-update_campaign_budget",
        "arguments": {"body": {"campaigns": [{"campaignId": "c1", "dailyBudget": "$50.00"}]}},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert result.ok, result.error
    assert result.arguments["body"]["campaigns"][0]["dailyBudget"] == 50.0


@pytest.mark.asyncio
async def test_budget_update_below_min(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-update_campaign_budget",
        "arguments": {"body": {"campaigns": [{"campaignId": "c1", "dailyBudget": 0.5}]}},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert not result.ok
    assert "minimum" in result.error


# ── State / rename ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_campaign_state_normalized(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-update_campaign_state",
        "arguments": {"body": {"campaigns": [{"campaignId": "c1", "state": "paused"}]}},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert result.ok
    assert result.arguments["body"]["campaigns"][0]["state"] == "PAUSED"


@pytest.mark.asyncio
async def test_invalid_state_rejected(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-update_campaign_state",
        "arguments": {"body": {"campaigns": [{"campaignId": "c1", "state": "wat"}]}},
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert not result.ok


@pytest.mark.asyncio
async def test_create_target_requires_match_type(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-create_target",
        "arguments": {
            "body": {
                "targets": [{
                    "campaignId": "c1",
                    "adGroupId": "g1",
                    "expression": "wireless headphones",
                    "matchType": "exact",
                    "bid": 1.5,
                    "state": "enabled",
                }]
            }
        },
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert result.ok, result.error
    target = result.arguments["body"]["targets"][0]
    assert target["matchType"] == "EXACT"
    assert target["state"] == "ENABLED"
    assert target["bid"] == 1.5


@pytest.mark.asyncio
async def test_create_target_too_long_keyword_rejected(db_with_existing_ids, cred):
    action = {
        "tool": "campaign_management-create_target",
        "arguments": {
            "body": {
                "targets": [{
                    "campaignId": "c1",
                    "adGroupId": "g1",
                    "expression": "x" * 200,
                    "matchType": "EXACT",
                    "bid": 1.0,
                }]
            }
        },
    }
    result = await validator.validate_ai_action(action, db_with_existing_ids, cred)
    assert not result.ok
    assert "exceeds" in result.error


# ── Batch ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_batch_partitions_results(db_with_existing_ids, cred):
    actions = [
        {
            "tool": "campaign_management-update_target_bid",
            "arguments": {"body": {"targets": [{"targetId": "t1", "bid": 0.5}]}},
            "label": "ok",
        },
        {
            "tool": "campaign_management-update_target_bid",
            "arguments": {"body": {"targets": [{"targetId": "t1", "bid": 99999}]}},
            "label": "bad",
        },
    ]
    accepted, rejected = await validator.validate_ai_actions(
        actions, db_with_existing_ids, cred,
    )
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0]["error"]
