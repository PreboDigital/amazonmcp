"""Tests for app.services.mutation_aftercare.

Phase 5.3 reads back recently-applied mutations and reports any drift
between what we asked Amazon to do and what its query API actually
returns. These tests pin the per-tool verifier behaviour using a fake
MCP client.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import mutation_aftercare as mac  # noqa: E402


class FakeMCP:
    def __init__(self, *, targets=None, campaigns=None, ad_groups=None, raise_on=None):
        self._targets = targets or []
        self._campaigns = campaigns or []
        self._ad_groups = ad_groups or []
        self._raise_on = raise_on or set()

    async def query_targets(self, ad_group_id=None, all_products=False, **_):
        if "query_targets" in self._raise_on:
            raise RuntimeError("simulated MCP error")
        items = self._targets
        if ad_group_id:
            items = [t for t in items if t.get("adGroupId") == ad_group_id]
        return {"targets": items}

    async def query_campaigns(self, all_products=False, **_):
        if "query_campaigns" in self._raise_on:
            raise RuntimeError("simulated MCP error")
        return {"campaigns": self._campaigns}

    async def query_ad_groups(self, all_products=False, **_):
        if "query_ad_groups" in self._raise_on:
            raise RuntimeError("simulated MCP error")
        return {"adGroups": self._ad_groups}


def _run(coro):
    return asyncio.run(coro)


# ── Target update ────────────────────────────────────────────────────

def test_verify_target_update_ok_when_bid_matches():
    client = FakeMCP(targets=[{"targetId": "t-1", "bid": 0.50, "state": "ENABLED"}])
    args = {"body": {"targets": [{"targetId": "t-1", "bid": 0.50}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_target_bid", args))
    assert report["ok"] is True
    assert report["drift"] == []


def test_verify_target_update_reports_drift_on_clamped_bid():
    client = FakeMCP(targets=[{"targetId": "t-1", "bid": 1.00, "state": "ENABLED"}])
    args = {"body": {"targets": [{"targetId": "t-1", "bid": 0.50}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_target_bid", args))
    assert report["ok"] is False
    assert any(d["field"] == "bid" for d in report["drift"])


def test_verify_target_update_reports_missing_target():
    client = FakeMCP(targets=[])
    args = {"body": {"targets": [{"targetId": "t-missing", "bid": 0.50}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_target", args))
    assert report["ok"] is False
    assert any(d["observed"] == "missing" for d in report["drift"])


def test_verify_target_update_state_drift():
    client = FakeMCP(targets=[{"targetId": "t-1", "bid": 0.50, "state": "ENABLED"}])
    args = {"body": {"targets": [{"targetId": "t-1", "state": "PAUSED"}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_target_bid", args))
    assert any(d["field"] == "state" for d in report["drift"])


# ── Target delete ────────────────────────────────────────────────────

def test_verify_target_delete_ok_when_absent():
    client = FakeMCP(targets=[])
    args = {"body": {"targetIds": ["t-1", "t-2"]}}
    report = _run(mac.verify_mutation(client, "campaign_management-delete_target", args))
    assert report["ok"] is True


def test_verify_target_delete_drift_when_still_present():
    client = FakeMCP(targets=[{"targetId": "t-1"}])
    args = {"body": {"targetIds": ["t-1"]}}
    report = _run(mac.verify_mutation(client, "campaign_management-delete_target", args))
    assert report["ok"] is False
    assert report["drift"][0]["observed"] == "still_present"


# ── Target create ────────────────────────────────────────────────────

def test_verify_target_create_ok_when_expression_present():
    client = FakeMCP(targets=[
        {"targetId": "t-99", "adGroupId": "ag-1", "expression": "shoes"},
    ])
    args = {"body": {"targets": [
        {"adGroupId": "ag-1", "expression": "shoes", "matchType": "EXACT"},
    ]}}
    report = _run(mac.verify_mutation(client, "campaign_management-create_target", args))
    assert report["ok"] is True


def test_verify_target_create_drift_when_expression_missing():
    client = FakeMCP(targets=[])
    args = {"body": {"targets": [
        {"adGroupId": "ag-1", "expression": "missing-keyword", "matchType": "EXACT"},
    ]}}
    report = _run(mac.verify_mutation(client, "campaign_management-create_target", args))
    assert report["ok"] is False


# ── Campaign update ──────────────────────────────────────────────────

def test_verify_campaign_update_budget_drift():
    client = FakeMCP(campaigns=[{"campaignId": "c-1", "dailyBudget": 30.0, "state": "ENABLED"}])
    args = {"body": {"campaigns": [{"campaignId": "c-1", "dailyBudget": 50.0}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_campaign_budget", args))
    assert report["ok"] is False
    assert any(d["field"] == "dailyBudget" for d in report["drift"])


def test_verify_campaign_update_no_drift_when_budget_matches():
    client = FakeMCP(campaigns=[{"campaignId": "c-1", "dailyBudget": 50.0, "state": "ENABLED"}])
    args = {"body": {"campaigns": [{"campaignId": "c-1", "dailyBudget": 50.0}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_campaign_budget", args))
    assert report["ok"] is True


def test_verify_campaign_update_handles_nested_budget_object():
    """Some MCP responses nest budget under a {'budget': {'budget': 50}} object."""
    client = FakeMCP(campaigns=[
        {"campaignId": "c-1", "budget": {"budget": 50.0}, "state": "ENABLED"},
    ])
    args = {"body": {"campaigns": [{"campaignId": "c-1", "dailyBudget": 50.0}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_campaign_budget", args))
    assert report["ok"] is True


def test_verify_campaign_update_tolerates_non_dict_budget_field():
    """A scalar / null ``budget`` value must not crash the verifier."""
    client = FakeMCP(campaigns=[
        {"campaignId": "c-1", "budget": 50.0, "state": "ENABLED"},
    ])
    args = {"body": {"campaigns": [{"campaignId": "c-1", "dailyBudget": 50.0}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_campaign_budget", args))
    # No crash — drift entry possible because dailyBudget+nested-budget
    # don't match, but ``ok`` reflects the absence of a thrown exception.
    assert "error" not in report
    assert isinstance(report.get("drift"), list)


# ── Ad group update ──────────────────────────────────────────────────

def test_verify_ad_group_update_default_bid_drift():
    client = FakeMCP(ad_groups=[{"adGroupId": "ag-1", "defaultBid": 1.20, "state": "ENABLED", "name": "AG1"}])
    args = {"body": {"adGroups": [{"adGroupId": "ag-1", "defaultBid": 0.80}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_ad_group", args))
    assert report["ok"] is False
    assert any(d["field"] == "defaultBid" for d in report["drift"])


# ── Verifier swallows read-back failures ────────────────────────────

def test_verify_handles_query_exception():
    client = FakeMCP(raise_on={"query_targets"})
    args = {"body": {"targets": [{"targetId": "t-1", "bid": 0.50}]}}
    report = _run(mac.verify_mutation(client, "campaign_management-update_target_bid", args))
    # Read-back failure → report still returns; ok=False because target not found
    assert report["ok"] is False


def test_verify_unknown_tool_returns_skipped():
    client = FakeMCP()
    report = _run(mac.verify_mutation(client, "_harvest_execute", {}))
    assert report["ok"] is True
    assert report["skipped"] is True


def test_verify_missing_body_returns_error():
    client = FakeMCP()
    report = _run(mac.verify_mutation(client, "campaign_management-update_target_bid", {}))
    assert report["ok"] is False
    assert "body" in (report.get("error") or "")


# ── build_aftercare ──────────────────────────────────────────────────

def test_build_aftercare_ok_message_when_no_drift():
    aftercare = mac.build_aftercare(
        "campaign_management-update_target_bid",
        {"body": {"targets": [{"targetId": "t-1", "bid": 0.5}]}},
        {"mcpStatus": "OK"},
        {"ok": True, "drift": [], "checked": 1, "found": 1},
    )
    assert aftercare["headline"] == "Applied and verified"
    assert "matches" in aftercare["summary"]
    assert isinstance(aftercare["next_prompts"], list)


def test_build_aftercare_drift_message_lists_fields():
    aftercare = mac.build_aftercare(
        "campaign_management-update_target_bid",
        {"body": {"targets": [{"targetId": "t-1", "bid": 0.5}]}},
        {"mcpStatus": "OK"},
        {
            "ok": False,
            "drift": [{"targetId": "t-1", "field": "bid", "expected": 0.5, "observed": 1.0}],
            "checked": 1, "found": 1,
        },
    )
    assert "drift" in aftercare["headline"].lower()
    assert "bid" in aftercare["summary"]


def test_build_aftercare_verifier_error_surfaced():
    aftercare = mac.build_aftercare(
        "campaign_management-update_target_bid",
        {"body": {"targets": [{"targetId": "t-1", "bid": 0.5}]}},
        {"mcpStatus": "OK"},
        {"ok": False, "error": "boom"},
    )
    assert "verification" in aftercare["headline"].lower()
    assert "boom" in aftercare["summary"]


def test_build_aftercare_skipped_for_synthetic_tool():
    aftercare = mac.build_aftercare(
        "_harvest_execute", {}, {"ok": True}, {"ok": True, "skipped": True},
    )
    assert "no aftercare verifier" in aftercare["headline"]
    assert "did not run a read-back" in aftercare["summary"]
