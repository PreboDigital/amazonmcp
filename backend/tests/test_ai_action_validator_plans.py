"""Tests for queue-only plan validators in ai_action_validator."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
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
def db_with_existing(monkeypatch):
    monkeypatch.setattr(validator, "_target_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(validator, "_campaign_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(validator, "_ad_group_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(validator, "_ad_exists", AsyncMock(return_value=True))
    return AsyncMock(name="db")


@pytest.fixture
def db_missing(monkeypatch):
    monkeypatch.setattr(validator, "_target_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(validator, "_campaign_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(validator, "_ad_group_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(validator, "_ad_exists", AsyncMock(return_value=False))
    return AsyncMock(name="db")


# ── _harvest_execute ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_harvest_basic_ok(db_with_existing, cred):
    action = {
        "tool": "_harvest_execute",
        "arguments": {
            "source_campaign_id": "auto-1",
            "sales_threshold": 1.0,
            "target_mode": "new",
        },
    }
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert result.ok, result.error


@pytest.mark.asyncio
async def test_harvest_missing_source(db_with_existing, cred):
    action = {"tool": "_harvest_execute", "arguments": {}}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "source_campaign_id" in result.error


@pytest.mark.asyncio
async def test_harvest_unknown_source_campaign(db_missing, cred):
    action = {
        "tool": "_harvest_execute",
        "arguments": {"source_campaign_id": "nope"},
    }
    result = await validator.validate_ai_action(action, db_missing, cred)
    assert not result.ok
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_harvest_existing_requires_target_campaign(db_with_existing, cred):
    action = {
        "tool": "_harvest_execute",
        "arguments": {
            "source_campaign_id": "auto-1",
            "target_mode": "existing",
        },
    }
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "target_campaign_id" in result.error


@pytest.mark.asyncio
async def test_harvest_negative_sales_threshold(db_with_existing, cred):
    action = {
        "tool": "_harvest_execute",
        "arguments": {"source_campaign_id": "auto-1", "sales_threshold": -1},
    }
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "negative" in result.error


@pytest.mark.asyncio
async def test_harvest_invalid_match_type(db_with_existing, cred):
    action = {
        "tool": "_harvest_execute",
        "arguments": {"source_campaign_id": "auto-1", "match_type": "fuzzy"},
    }
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok


# ── _ai_campaign_create ───────────────────────────────────────────────

def _good_plan() -> dict:
    return {
        "plan": {
            "campaign": {
                "name": "Test Campaign",
                "adProduct": "SPONSORED_PRODUCTS",
                "targetingType": "manual",
                "dailyBudget": 50.0,
            },
            "ad": {"asin": "B000TEST123"},
            "ad_groups": [
                {
                    "name": "Group A",
                    "defaultBid": 0.75,
                    "keywords": [
                        {"text": "headphones", "match_type": "exact", "bid": 1.0},
                    ],
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_campaign_plan_ok(db_with_existing, cred):
    action = {"tool": "_ai_campaign_create", "arguments": _good_plan()}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert result.ok, result.error


@pytest.mark.asyncio
async def test_campaign_plan_requires_asin_for_sp(db_with_existing, cred):
    args = _good_plan()
    args["plan"]["ad"] = {}
    args["plan"]["campaign"].pop("asin", None)
    action = {"tool": "_ai_campaign_create", "arguments": args}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "ASIN" in result.error


@pytest.mark.asyncio
async def test_campaign_plan_invalid_ad_product(db_with_existing, cred):
    args = _good_plan()
    args["plan"]["campaign"]["adProduct"] = "MAGIC_ADS"
    action = {"tool": "_ai_campaign_create", "arguments": args}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "adProduct" in result.error


@pytest.mark.asyncio
async def test_campaign_plan_zero_budget_rejected(db_with_existing, cred):
    args = _good_plan()
    args["plan"]["campaign"]["dailyBudget"] = 0.5
    action = {"tool": "_ai_campaign_create", "arguments": args}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "minimum" in result.error.lower()


@pytest.mark.asyncio
async def test_campaign_plan_manual_requires_keywords(db_with_existing, cred):
    args = _good_plan()
    args["plan"]["ad_groups"][0]["keywords"] = []
    action = {"tool": "_ai_campaign_create", "arguments": args}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "keyword" in result.error


@pytest.mark.asyncio
async def test_campaign_plan_auto_keyword_warning(db_with_existing, cred):
    args = _good_plan()
    args["plan"]["campaign"]["targetingType"] = "auto"
    action = {"tool": "_ai_campaign_create", "arguments": args}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert result.ok, result.error
    assert any("ignored for AUTO" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_campaign_plan_too_many_keywords_rejected(db_with_existing, cred):
    args = _good_plan()
    args["plan"]["ad_groups"][0]["keywords"] = [
        {"text": f"kw{i}", "match_type": "broad"} for i in range(500)
    ]
    action = {"tool": "_ai_campaign_create", "arguments": args}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "max" in result.error


@pytest.mark.asyncio
async def test_campaign_plan_too_many_ad_groups_rejected(db_with_existing, cred):
    args = _good_plan()
    args["plan"]["ad_groups"] = [
        {"name": f"AG-{i}", "defaultBid": 0.5, "keywords": [{"text": "x", "match_type": "broad"}]}
        for i in range(50)
    ]
    action = {"tool": "_ai_campaign_create", "arguments": args}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "ad groups" in result.error


# ── _request_sync ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_sync_ok(db_with_existing, cred):
    action = {
        "tool": "_request_sync",
        "arguments": {"kind": "REPORTS", "range_preset": "Last_7_Days"},
    }
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert result.ok, result.error
    assert result.arguments["kind"] == "reports"
    assert result.arguments["range_preset"] == "last_7_days"


@pytest.mark.asyncio
async def test_request_sync_kind_required(db_with_existing, cred):
    action = {"tool": "_request_sync", "arguments": {}}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
    assert "kind" in result.error


@pytest.mark.asyncio
async def test_request_sync_invalid_kind(db_with_existing, cred):
    action = {"tool": "_request_sync", "arguments": {"kind": "everything"}}
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok


@pytest.mark.asyncio
async def test_request_sync_invalid_range_preset(db_with_existing, cred):
    action = {
        "tool": "_request_sync",
        "arguments": {"kind": "campaigns", "range_preset": "since_dawn_of_time"},
    }
    result = await validator.validate_ai_action(action, db_with_existing, cred)
    assert not result.ok
