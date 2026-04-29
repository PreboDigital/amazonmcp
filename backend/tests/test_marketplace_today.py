"""Tests for marketplace-aware "today" / timezone resolution.

These guard the Phase 1 fix that replaced ``date.today()`` (server-local TZ)
with marketplace/region-aware date calculations across optimizer, audit,
search-term, product, and cron flows.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils import (  # noqa: E402
    MARKETPLACE_TIMEZONES,
    REGION_FALLBACK_TIMEZONES,
    marketplace_now,
    marketplace_today,
    resolve_marketplace_timezone,
)


def test_resolve_marketplace_timezone_known_marketplace():
    assert resolve_marketplace_timezone("US").key == "America/Los_Angeles"
    assert resolve_marketplace_timezone("GB").key == "Europe/London"
    assert resolve_marketplace_timezone("JP").key == "Asia/Tokyo"


def test_resolve_marketplace_timezone_unknown_falls_back_to_region():
    assert resolve_marketplace_timezone("XX", region="eu").key == "Europe/London"
    assert resolve_marketplace_timezone(None, region="fe").key == "Asia/Tokyo"


def test_resolve_marketplace_timezone_default_utc():
    assert resolve_marketplace_timezone(None).key == "UTC"
    assert resolve_marketplace_timezone("XX", region="zz").key == "UTC"


def test_marketplace_today_differs_from_utc_at_late_utc():
    # 2026-04-28 23:30 UTC → 2026-04-29 00:30 London / 08:30 Tokyo / 16:30 LA prev day
    fixed_utc = datetime(2026, 4, 28, 23, 30, tzinfo=timezone.utc)

    class _FrozenNow:
        @staticmethod
        def __call__(tz):
            return fixed_utc.astimezone(tz)

    with patch("app.utils.datetime") as dt_mock:
        dt_mock.now = _FrozenNow()
        dt_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)

        assert marketplace_today().isoformat() == "2026-04-28"  # UTC
        assert marketplace_today("GB").isoformat() == "2026-04-29"  # London +1h on BST
        assert marketplace_today("JP").isoformat() == "2026-04-29"  # Tokyo +9h
        assert marketplace_today("US").isoformat() == "2026-04-28"  # LA -7h on PDT


def test_marketplace_now_returns_aware_datetime():
    now_us = marketplace_now("US")
    assert now_us.tzinfo is not None
    assert now_us.tzinfo.key == "America/Los_Angeles"


def test_all_mapped_marketplace_timezones_are_valid():
    for code, tz in MARKETPLACE_TIMEZONES.items():
        assert ZoneInfo(tz), f"{code} mapped to invalid TZ {tz}"
    for region, tz in REGION_FALLBACK_TIMEZONES.items():
        assert ZoneInfo(tz), f"{region} mapped to invalid TZ {tz}"
