"""Unit tests for harvest target metrics / threshold helpers."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import harvest_filtering as hf  # noqa: E402


def test_pick_metrics_prefers_7d_when_lookback_7():
    t = {"attributedSales7d": 10.0, "clicks7d": 5, "acos7d": 25.0, "sales": 99.0}
    s, a, c, label = hf.pick_harvest_metrics(t, 7)
    assert s == 10.0
    assert c == 5.0
    assert a == 25.0
    assert "7" in label or label == "7d"


def test_clicks_threshold_filters():
    rows = [
        {"keywordText": "a", "attributedSales7d": 5, "acos7d": 10, "clicks7d": 2},
        {"keywordText": "b", "attributedSales7d": 5, "acos7d": 10, "clicks7d": 8},
    ]
    q, _ = hf.filter_target_list_for_harvest(
        rows,
        sales_threshold=1.0,
        acos_threshold=None,
        clicks_threshold=5,
        lookback_days=7,
        match_type_filter=None,
    )
    assert len(q) == 1
    assert q[0]["keyword"] == "b"


def test_match_type_override_on_output():
    rows = [
        {"keywordText": "x", "matchType": "BROAD", "attributedSales7d": 5, "clicks7d": 10},
    ]
    q, _ = hf.filter_target_list_for_harvest(
        rows,
        sales_threshold=1.0,
        acos_threshold=None,
        clicks_threshold=None,
        lookback_days=7,
        match_type_filter="exact",
    )
    assert len(q) == 1
    assert q[0]["matchType"] == "EXACT"


def test_acos_threshold_rejects_high_acos():
    rows = [
        {"keywordText": "hi", "attributedSales7d": 10, "acos7d": 50, "clicks7d": 5},
        {"keywordText": "lo", "attributedSales7d": 10, "acos7d": 20, "clicks7d": 5},
    ]
    q, _ = hf.filter_target_list_for_harvest(
        rows,
        sales_threshold=1.0,
        acos_threshold=30.0,
        clicks_threshold=None,
        lookback_days=7,
        match_type_filter=None,
    )
    assert [k["keyword"] for k in q] == ["lo"]
