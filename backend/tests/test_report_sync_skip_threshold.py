"""Tests for _should_abort_skipped_sync — the day-skip safety net.

This is the helper that decides whether a multi-day report sync should
continue past per-day failures (the 2026-03-28 stuck-date pattern) or
hard-fail because too many days are dying (systemic auth/scope issue).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.routers.reporting import _should_abort_skipped_sync  # noqa: E402


def test_zero_skips_never_aborts():
    assert _should_abort_skipped_sync(0, 30) is False


def test_small_number_of_skips_below_floor_never_aborts():
    # The 2026-03-28 prod scenario: 1 stuck day in a 30-day window
    assert _should_abort_skipped_sync(1, 30) is False
    assert _should_abort_skipped_sync(2, 30) is False


def test_majority_failures_abort():
    assert _should_abort_skipped_sync(20, 30) is True
    assert _should_abort_skipped_sync(16, 30) is True


def test_exactly_at_threshold_does_not_abort():
    # 50% of 30 = 15. Threshold is "> 15" → 15 should NOT abort, 16 should
    assert _should_abort_skipped_sync(15, 30) is False
    assert _should_abort_skipped_sync(16, 30) is True


def test_short_window_uses_floor_not_ratio():
    # In a 4-day window, 50% would be 2. Floor is also 2 → 2 skips OK
    assert _should_abort_skipped_sync(2, 4) is False
    # 3 skips out of 4 days IS catastrophic and should abort
    assert _should_abort_skipped_sync(3, 4) is True


def test_single_day_window_never_aborts_on_floor():
    # Edge case: 1-day catch-up run; even 1 failure is below floor
    assert _should_abort_skipped_sync(1, 1) is False


def test_custom_max_skip_ratio_respected():
    # If we tighten to 20%, 7 of 30 should abort but 6 should not
    assert _should_abort_skipped_sync(6, 30, max_skip_ratio=0.2) is False
    assert _should_abort_skipped_sync(7, 30, max_skip_ratio=0.2) is True


def test_custom_floor_respected():
    # With floor=5, 5 skips is still OK even on a 6-day window
    assert _should_abort_skipped_sync(5, 6, floor=5) is False
    assert _should_abort_skipped_sync(6, 6, floor=5) is True
