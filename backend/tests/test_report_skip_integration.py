"""Integration-flavoured tests for the report-skip flow.

These tests stitch together :mod:`app.services.report_skip_service` to
verify the **end-to-end Phase 5.1 contract**:

1. Three consecutive syncs each report ``"2026-04-01"`` as a failure.
2. After the third failure the date moves to the permanent skip list.
3. A fourth sync run *filters that date out* before it ever hits MCP.
4. A successful sync of the date later re-enables it.

This complements the unit tests in ``test_report_skip_service.py`` —
those pin the state machine; this test pins the behaviour the cron
loop expects to depend on.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import report_skip_service as svc  # noqa: E402


def _cred() -> SimpleNamespace:
    return SimpleNamespace(id="cred-int-1", credential_metadata=None)


def _run(coro):
    return asyncio.run(coro)


def test_three_failures_then_filter_then_recovery():
    cred = _cred()
    db = MagicMock()
    profile = "P-INT"
    bad_day = "2026-04-01"
    window = ["2026-03-31", "2026-04-01", "2026-04-02"]

    async def _run_sync_attempt(failures: list[str], successes: list[str]):
        return await svc.update_after_sync(
            db,
            cred,
            profile,
            skipped_days=[{"date": d, "error": "boom"} for d in failures],
            synced_day_strs=successes,
        )

    # Run 1 — first failure
    eligible, perm = svc.filter_skipped(window, cred, profile)
    assert bad_day in eligible
    assert perm == []
    r1 = _run(_run_sync_attempt([bad_day], ["2026-03-31", "2026-04-02"]))
    assert r1["promoted_to_permanent"] == []

    # Run 2 — second failure, still not promoted
    r2 = _run(_run_sync_attempt([bad_day], ["2026-03-31", "2026-04-02"]))
    assert r2["promoted_to_permanent"] == []
    assert bad_day not in svc.get_permanent_skip_dates(cred, profile)

    # Run 3 — third strike → promoted
    r3 = _run(_run_sync_attempt([bad_day], ["2026-03-31", "2026-04-02"]))
    assert r3["promoted_to_permanent"] == [bad_day]
    assert bad_day in svc.get_permanent_skip_dates(cred, profile)

    # Run 4 — cron filters the doomed day before MCP
    eligible, perm = svc.filter_skipped(window, cred, profile)
    assert bad_day in perm
    assert bad_day not in eligible
    assert eligible == ["2026-03-31", "2026-04-02"]

    # Recovery — Amazon catches up; one successful sync clears the flag
    r5 = _run(_run_sync_attempt([], [bad_day]))
    assert r5["cleared_after_success"] == [bad_day]
    assert bad_day not in svc.get_permanent_skip_dates(cred, profile)
    eligible, perm = svc.filter_skipped(window, cred, profile)
    assert bad_day in eligible


def test_promotion_threshold_isolated_per_profile():
    """A bad date on profile A must not cross-contaminate profile B."""
    cred = _cred()
    db = MagicMock()

    async def _attempt(profile: str, day: str):
        return await svc.update_after_sync(
            db, cred, profile,
            skipped_days=[{"date": day, "error": "boom"}],
            synced_day_strs=[],
        )

    for _ in range(svc.PROMOTE_THRESHOLD):
        _run(_attempt("PA", "2026-04-05"))

    assert "2026-04-05" in svc.get_permanent_skip_dates(cred, "PA")
    assert "2026-04-05" not in svc.get_permanent_skip_dates(cred, "PB")


def test_already_permanent_skipped_days_dont_double_count():
    """Re-skipping a date that's already permanent shouldn't grow the counter."""
    cred = _cred()
    db = MagicMock()

    async def _attempt(skipped):
        return await svc.update_after_sync(
            db, cred, "P1",
            skipped_days=skipped, synced_day_strs=[],
        )

    for _ in range(svc.PROMOTE_THRESHOLD):
        _run(_attempt([{"date": "2026-04-10", "error": "boom"}]))

    permanent_size = len(svc.get_permanent_skip_dates(cred, "P1"))

    # Five more attempted skips with 'permanent' marker — caller already
    # filtered, so update_after_sync receives them as plain failures.
    for _ in range(5):
        _run(_attempt([{"date": "2026-04-10", "error": "boom"}]))

    # List shouldn't grow beyond a single entry for the same date.
    assert len(svc.get_permanent_skip_dates(cred, "P1")) == permanent_size == 1


def test_caller_filters_permanent_marked_entries_before_update():
    """Reporting router excludes 'permanent' entries from update_after_sync.

    Pinned: a sync run with 4 permanent-flagged skips and 1 fresh failure
    must only increment the counter once.
    """
    cred = _cred()
    db = MagicMock()
    skipped = [
        {"date": "2026-03-28", "error": "permanent_skip", "permanent": True},
        {"date": "2026-03-29", "error": "permanent_skip", "permanent": True},
        {"date": "2026-03-30", "error": "permanent_skip", "permanent": True},
        {"date": "2026-03-31", "error": "permanent_skip", "permanent": True},
        {"date": "2026-04-01", "error": "fresh boom"},  # only this one counts
    ]
    new_skipped_for_counter = [s for s in skipped if not s.get("permanent")]

    report = _run(svc.update_after_sync(
        db, cred, "P1",
        skipped_days=new_skipped_for_counter,
        synced_day_strs=[],
    ))
    assert report["promoted_to_permanent"] == []
    state = cred.credential_metadata["report_skip"]["P1"]
    # Only the fresh failure has a counter entry
    assert list(state["counters"].keys()) == ["2026-04-01"]
    assert state["counters"]["2026-04-01"]["count"] == 1
