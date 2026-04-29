"""Tests for CampaignCreationService rollback path.

Phase 5+ adds compensating-delete for partial-fail campaigns. We pin
the contract:

* When the campaign is created but every ad-group create fails, the
  service walks the rollback steps in reverse and deletes the campaign.
* When at least one ad group succeeds, no rollback runs (partial plans
  are recoverable in the UI).
* Rollback exceptions don't bubble — they are recorded under
  ``results['rollback_errors']``.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# CampaignCreationService imports app.mcp_client which depends on the
# external ``mcp`` SDK. Tests don't need the real client — stub the
# module tree so the import resolves to a placeholder.
if "mcp" not in sys.modules:
    mcp_stub = types.ModuleType("mcp")
    mcp_stub.ClientSession = object
    sys.modules["mcp"] = mcp_stub
    client_pkg = types.ModuleType("mcp.client")
    sys.modules["mcp.client"] = client_pkg
    streamable = types.ModuleType("mcp.client.streamable_http")
    streamable.streamablehttp_client = lambda *a, **kw: None
    sys.modules["mcp.client.streamable_http"] = streamable

from app.services.campaign_creation_service import CampaignCreationService  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _plan():
    return {
        "campaign": {
            "name": "C",
            "adProduct": "SPONSORED_PRODUCTS",
            "targetingType": "manual",
            "dailyBudget": 50,
        },
        "ad": {"asin": "B0XYZ123"},
        "ad_groups": [
            {"name": "AG1", "defaultBid": 0.5, "keywords": [
                {"text": "kw1", "matchType": "EXACT", "bid": 0.4},
            ]},
        ],
    }


def _make_client(*, ag_fails: bool = False, ad_fails: bool = False, target_fails: bool = False):
    c = AsyncMock()
    c.create_campaign.return_value = {"campaigns": [{"campaignId": "camp-1"}]}
    if ag_fails:
        c.create_ad_group.side_effect = RuntimeError("ag boom")
    else:
        c.create_ad_group.return_value = {"adGroups": [{"adGroupId": "ag-1"}]}
    if ad_fails:
        c.create_ad.side_effect = RuntimeError("ad boom")
    else:
        c.create_ad.return_value = {"ads": [{"adId": "ad-1"}]}
    if target_fails:
        c.create_target.side_effect = RuntimeError("target boom")
    else:
        c.create_target.return_value = {"targets": [{"targetId": "t-1"}]}
    c.delete_target.return_value = {"ok": True}
    c.delete_ad.return_value = {"ok": True}
    c.delete_ad_group.return_value = {"ok": True}
    c.delete_campaign.return_value = {"ok": True}
    return c


def test_no_rollback_when_full_plan_succeeds():
    client = _make_client()
    svc = CampaignCreationService(client)
    res = _run(svc.execute_plan(_plan()))
    assert res["campaign_id"] == "camp-1"
    assert res["ad_group_ids"] == ["ag-1"]
    assert res["rollback_performed"] is False
    client.delete_campaign.assert_not_awaited()


def test_rollback_when_all_ad_groups_fail():
    client = _make_client(ag_fails=True)
    svc = CampaignCreationService(client)
    res = _run(svc.execute_plan(_plan()))
    assert res["campaign_id"] == "camp-1"
    assert res["ad_group_ids"] == []
    assert res["rollback_performed"] is True
    client.delete_campaign.assert_awaited_once_with(["camp-1"])


def test_no_rollback_when_target_create_fails_but_ad_group_lives():
    """Partial plan — keep what we have so the user can fix forward."""
    client = _make_client(target_fails=True)
    svc = CampaignCreationService(client)
    res = _run(svc.execute_plan(_plan()))
    assert res["campaign_id"] == "camp-1"
    assert res["ad_group_ids"] == ["ag-1"]
    assert res["rollback_performed"] is False
    assert any("Target creation failed" in e for e in res["errors"])
    client.delete_campaign.assert_not_awaited()


def test_rollback_errors_are_recorded_not_raised():
    client = _make_client(ag_fails=True)
    client.delete_campaign.side_effect = RuntimeError("delete boom")
    svc = CampaignCreationService(client)
    res = _run(svc.execute_plan(_plan()))
    assert res["rollback_performed"] is True
    assert any("rollback failed" in e for e in res["rollback_errors"])


def test_opt_out_rollback_keeps_orphans():
    client = _make_client(ag_fails=True)
    svc = CampaignCreationService(client)
    res = _run(svc.execute_plan(_plan(), rollback_on_failure=False))
    assert res["rollback_performed"] is False
    client.delete_campaign.assert_not_awaited()
