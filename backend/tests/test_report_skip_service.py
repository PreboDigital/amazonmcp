"""Unit tests for app.services.report_skip_service.

Phase 5.1 promoted chronically failing report dates to a permanent skip
list. These tests cover the state-machine transitions:

* counter increments on each failure
* date moves to ``permanent`` after ``PROMOTE_THRESHOLD`` failures
* successful sync clears the counter
* successful sync clears a permanent flag (Amazon caught up)
* permanent list / counter dict are bounded

The service mutates ``Credential.credential_metadata`` in place — we
assert via SimpleNamespace stand-ins, so the tests don't need a DB.
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


def _make_cred(metadata=None):
    return SimpleNamespace(id="cred-1", credential_metadata=metadata)


def test_record_skip_increments_counter_below_threshold():
    cred = _make_cred()
    promoted = svc.record_skip(cred, "P1", "2026-04-01", "boom")
    assert promoted is None
    state = cred.credential_metadata["report_skip"]["P1"]
    assert state["counters"]["2026-04-01"]["count"] == 1
    assert state["permanent"] == []


def test_record_skip_promotes_after_threshold():
    cred = _make_cred()
    for _ in range(svc.PROMOTE_THRESHOLD - 1):
        assert svc.record_skip(cred, None, "2026-04-02", "boom") is None
    promoted = svc.record_skip(cred, None, "2026-04-02", "boom")
    assert promoted == f"promoted_after_{svc.PROMOTE_THRESHOLD}_failures"
    state = cred.credential_metadata["report_skip"]["__none__"]
    assert "2026-04-02" in state["permanent"]
    assert "2026-04-02" not in state["counters"]


def test_record_skip_idempotent_when_already_permanent():
    cred = _make_cred()
    for _ in range(svc.PROMOTE_THRESHOLD):
        svc.record_skip(cred, "P1", "2026-04-03", "boom")
    promoted = svc.record_skip(cred, "P1", "2026-04-03", "boom again")
    assert promoted is None
    state = cred.credential_metadata["report_skip"]["P1"]
    assert state["permanent"].count("2026-04-03") == 1


def test_clear_skip_resets_counter_and_permanent():
    cred = _make_cred()
    svc.record_skip(cred, "P1", "2026-04-04", "boom")
    assert svc.clear_skip(cred, "P1", "2026-04-04") is True
    state = cred.credential_metadata["report_skip"]["P1"]
    assert "2026-04-04" not in state["counters"]

    for _ in range(svc.PROMOTE_THRESHOLD):
        svc.record_skip(cred, "P1", "2026-04-05", "boom")
    assert "2026-04-05" in state["permanent"]
    assert svc.clear_skip(cred, "P1", "2026-04-05") is True
    assert "2026-04-05" not in state["permanent"]


def test_clear_skip_returns_false_when_no_state():
    cred = _make_cred()
    assert svc.clear_skip(cred, "P1", "2026-04-06") is False


def test_filter_skipped_separates_permanent_dates():
    cred = _make_cred()
    for _ in range(svc.PROMOTE_THRESHOLD):
        svc.record_skip(cred, "P1", "2026-04-07", "boom")
    eligible, skipped = svc.filter_skipped(
        ["2026-04-07", "2026-04-08", "2026-04-09"], cred, "P1"
    )
    assert "2026-04-07" not in eligible
    assert "2026-04-07" in skipped
    assert eligible == ["2026-04-08", "2026-04-09"]


def test_get_permanent_skip_dates_handles_missing_metadata():
    assert svc.get_permanent_skip_dates(_make_cred(), None) == set()
    assert svc.get_permanent_skip_dates(_make_cred(metadata={}), None) == set()
    assert svc.get_permanent_skip_dates(
        _make_cred(metadata={"report_skip": "garbage"}), None
    ) == set()


def test_profile_id_isolation():
    cred = _make_cred()
    for _ in range(svc.PROMOTE_THRESHOLD):
        svc.record_skip(cred, "P1", "2026-04-10", "boom")
    p1 = svc.get_permanent_skip_dates(cred, "P1")
    p2 = svc.get_permanent_skip_dates(cred, "P2")
    assert "2026-04-10" in p1
    assert "2026-04-10" not in p2


def test_permanent_list_bounded_to_max():
    cred = _make_cred()
    overflow = svc.MAX_PERMANENT_DATES + 5
    for i in range(overflow):
        day = f"2026-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}"
        for _ in range(svc.PROMOTE_THRESHOLD):
            svc.record_skip(cred, "P1", day, "boom")
    permanent = cred.credential_metadata["report_skip"]["P1"]["permanent"]
    assert len(permanent) == svc.MAX_PERMANENT_DATES


def test_update_after_sync_promotes_and_clears():
    cred = _make_cred()
    db = MagicMock()
    db.add = MagicMock()

    async def _run():
        for _ in range(svc.PROMOTE_THRESHOLD - 1):
            r = await svc.update_after_sync(
                db, cred, "P1",
                skipped_days=[{"date": "2026-04-11", "error": "boom"}],
                synced_day_strs=[],
            )
            assert r["promoted_to_permanent"] == []

        r = await svc.update_after_sync(
            db, cred, "P1",
            skipped_days=[{"date": "2026-04-11", "error": "boom"}],
            synced_day_strs=[],
        )
        assert r["promoted_to_permanent"] == ["2026-04-11"]
        assert "2026-04-11" in svc.get_permanent_skip_dates(cred, "P1")

        r = await svc.update_after_sync(
            db, cred, "P1",
            skipped_days=[],
            synced_day_strs=["2026-04-11"],
        )
        assert r["cleared_after_success"] == ["2026-04-11"]
        assert "2026-04-11" not in svc.get_permanent_skip_dates(cred, "P1")

    asyncio.run(_run())
