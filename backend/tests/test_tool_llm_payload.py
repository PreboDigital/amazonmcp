"""Tests for app.services.tool_llm_payload.shrink_tool_result_for_llm."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import tool_llm_payload as shrink  # noqa: E402


def test_shrink_truncates_known_list_key_and_annotates():
    rows = [{"campaignId": str(i), "name": f"c{i}", "spend": i} for i in range(60)]
    out = shrink.shrink_tool_result_for_llm(
        "campaign_management-query_campaign",
        {"campaigns": rows, "nextToken": None},
        max_rows=10,
    )
    assert len(out["campaigns"]) == 10
    meta = out["_campaigns_meta"]
    assert meta["_truncated_from"] == 60
    assert meta["_kept"] == 10
    assert "campaignId" in meta["_schema_keys"]


def test_shrink_clips_long_strings():
    out = shrink.shrink_tool_result_for_llm(
        "campaign_management-query_target",
        {"targets": [{"targetId": "t1", "expression": "x" * 1000}]},
        max_string=120,
    )
    expr = out["targets"][0]["expression"]
    assert len(expr) <= 120


def test_shrink_falls_back_to_summary_envelope_on_byte_overflow():
    rows = [{"campaignId": str(i), "name": "x" * 200} for i in range(5)]
    out = shrink.shrink_tool_result_for_llm(
        "campaign_management-query_campaign",
        {"campaigns": rows},
        max_rows=5,
        max_bytes=100,
    )
    encoded = json.dumps(out, default=str)
    assert encoded.count("_truncated") >= 1


def test_shrink_picks_fallback_list_key_for_unknown_tool():
    out = shrink.shrink_tool_result_for_llm(
        "some_unknown_tool",
        {"items": [{"x": i} for i in range(40)]},
        max_rows=5,
    )
    assert len(out["items"]) == 5
    assert out["_items_meta"]["_truncated_from"] == 40


def test_shrink_passes_small_results_through_unchanged():
    payload = {"campaigns": [{"campaignId": "c-1", "name": "ok"}]}
    out = shrink.shrink_tool_result_for_llm(
        "campaign_management-query_campaign", payload, max_rows=5,
    )
    assert out["campaigns"][0]["campaignId"] == "c-1"
    assert "_campaigns_meta" not in out


def test_shrink_handles_top_level_list_payload():
    rows = [{"x": i} for i in range(40)]
    out = shrink.shrink_tool_result_for_llm("anything", rows, max_rows=5)
    # The wrapper unwraps so caller still sees a list
    assert isinstance(out, list)
    assert len(out) == 5


def test_shrink_returns_scalars_unchanged():
    assert shrink.shrink_tool_result_for_llm("any", "ok") == "ok"
    assert shrink.shrink_tool_result_for_llm("any", 42) == 42
