"""Tests for app.services.harvest_service._harvest_to_existing.

Amazon Sponsored Products keywords/targets live on an *ad group*, not
a campaign (per the SP campaign-structure docs). The harvester used to
silently fall back to ``adGroups[0]`` if no ad group was supplied,
which dumped harvested keyword targets into product-targeting groups
when a manual campaign held both. The validator + UI now require an
explicit ``target_ad_group_id``; this test pins the service-level
contract so a direct caller (script, retry, replay) cannot reintroduce
the silent fallback.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.harvest_service import HarvestService  # noqa: E402


class _FakeMCP:
    """Minimal MCP stub. ``query_ad_groups`` would have been the source
    of the silent fallback in the old code; the test asserts it is
    *never* called when no ad-group id is passed in.
    """

    def __init__(self):
        self.calls: list[str] = []

    async def query_ad_groups(self, **_):
        self.calls.append("query_ad_groups")
        return {"adGroups": [{"adGroupId": "fallback-ag", "campaignId": "c-1"}]}

    async def call_tool(self, *_a, **_kw):
        self.calls.append("call_tool")
        return {"targets": []}


def _run(coro):
    return asyncio.run(coro)


def test_harvest_existing_hard_errors_without_ad_group(monkeypatch):
    client = _FakeMCP()
    service = HarvestService(client)

    async def _candidates(_self, _campaign_id):
        return {"targets": [{"keyword": "rugby tackle bag", "matchType": "EXACT", "bid": 0.5}]}

    monkeypatch.setattr(HarvestService, "get_harvest_candidates", _candidates)

    # Pretend the harvest filter passes our one keyword through.
    def _filter(targets, **_):
        return [
            {"keyword": "rugby tackle bag", "matchType": "EXACT", "bid": 0.5},
        ], {"window": "30d"}

    import app.services.harvest_service as hs_mod
    monkeypatch.setattr(hs_mod, "filter_target_list_for_harvest", _filter)
    monkeypatch.setattr(hs_mod, "normalize_target_list", lambda x: x or [])

    result = _run(
        service._harvest_to_existing(
            source_campaign_id="c-source",
            target_campaign_id="c-target",
            target_ad_group_id=None,
            sales_threshold=1.0,
            acos_threshold=None,
            match_type=None,
            negate_in_source=False,
            clicks_threshold=None,
            lookback_days=30,
        )
    )

    assert result["status"] == "error"
    assert "target_ad_group_id" in (result.get("error") or "")
    # The legacy silent fallback would have called query_ad_groups → call_tool;
    # neither must run when no ad-group id is supplied.
    assert "query_ad_groups" not in client.calls
    assert "call_tool" not in client.calls


def test_harvest_existing_proceeds_with_ad_group(monkeypatch):
    client = _FakeMCP()
    service = HarvestService(client)

    async def _candidates(_self, _campaign_id):
        return {"targets": [{"keyword": "rugby tackle bag", "matchType": "EXACT", "bid": 0.5}]}

    monkeypatch.setattr(HarvestService, "get_harvest_candidates", _candidates)

    import app.services.harvest_service as hs_mod
    monkeypatch.setattr(hs_mod, "normalize_target_list", lambda x: x or [])
    monkeypatch.setattr(
        hs_mod,
        "filter_target_list_for_harvest",
        lambda targets, **_: (
            [{"keyword": "rugby tackle bag", "matchType": "EXACT", "bid": 0.5}],
            {"window": "30d"},
        ),
    )

    async def _no_negate(*_a, **_kw):
        return 0

    monkeypatch.setattr(HarvestService, "_negate_keywords_in_source", _no_negate)

    result = _run(
        service._harvest_to_existing(
            source_campaign_id="c-source",
            target_campaign_id="c-target",
            target_ad_group_id="ag-explicit",
            sales_threshold=1.0,
            acos_threshold=None,
            match_type=None,
            negate_in_source=False,
            clicks_threshold=None,
            lookback_days=30,
        )
    )

    assert result["status"] == "success"
    assert result["target_ad_group_id"] == "ag-explicit"
    assert result["keywords_harvested"] == 1
    # The create_target call_tool fired; the silent fallback path did not.
    assert "call_tool" in client.calls
    assert "query_ad_groups" not in client.calls
