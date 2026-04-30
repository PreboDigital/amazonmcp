"""Targeting report merge helpers (sync-side Target metrics)."""

import pytest

from app.services.reporting_service import (
    ReportingService,
    targeting_perf_acos,
)


def test_merge_targeting_report_rows_aggregates_impressions():
    rows = [
        {"keywordId": "k1", "impressions": 100, "clicks": 2, "cost": 1.5, "sales7d": 10.0, "purchases7d": 1},
        {"keywordId": "k1", "impressions": 50, "clicks": 1, "cost": 0.5, "sales7d": 0.0, "purchases7d": 0},
    ]
    merged = ReportingService.merge_targeting_report_rows(rows)
    assert merged["k1"]["impressions"] == 150
    assert merged["k1"]["clicks"] == 3
    assert merged["k1"]["spend"] == pytest.approx(2.0)
    assert merged["k1"]["sales"] == pytest.approx(10.0)
    assert merged["k1"]["orders"] == 1


@pytest.mark.parametrize(
    "spend,sales,expected",
    [
        (10.0, 100.0, 10.0),
        (5.0, 0.0, None),
        (0.0, 0.0, None),
    ],
)
def test_targeting_perf_acos(spend, sales, expected):
    got = targeting_perf_acos(spend, sales)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)
