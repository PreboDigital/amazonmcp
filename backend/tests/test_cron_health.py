"""Tests for app.routers.cron staleness helpers + cron_sync non-blocking behaviour.

The full async DB integration tests live in the e2e suite. Here we exercise:

* :func:`cron._staleness_label` / :func:`cron._staleness_label_from_iso_date`
  — pure logic, easy to assert against fabricated timestamps.
* The cron router exposes a /health route + the non-blocking /sync route
  (smoke-check via FastAPI's route registry — no network).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.routers import cron as cron_router  # noqa: E402


def _now_naive() -> datetime:
    """Match utils.utcnow which returns naive UTC datetimes in this codebase."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_staleness_label_buckets():
    now = _now_naive()
    assert cron_router._staleness_label(None, warn_hours=24, crit_hours=72) == "never"
    assert cron_router._staleness_label(now - timedelta(hours=1), warn_hours=24, crit_hours=72) == "fresh"
    assert cron_router._staleness_label(now - timedelta(hours=30), warn_hours=24, crit_hours=72) == "warn"
    assert cron_router._staleness_label(now - timedelta(hours=96), warn_hours=24, crit_hours=72) == "stale"


def test_staleness_label_from_iso_date_buckets():
    today = _now_naive().date().isoformat()
    yesterday = (_now_naive().date() - timedelta(days=1)).isoformat()
    week_ago = (_now_naive().date() - timedelta(days=7)).isoformat()
    long_ago = (_now_naive().date() - timedelta(days=30)).isoformat()

    assert cron_router._staleness_label_from_iso_date(None, warn_days=2, crit_days=4) == "never"
    assert cron_router._staleness_label_from_iso_date(today, warn_days=2, crit_days=4) == "fresh"
    assert cron_router._staleness_label_from_iso_date(yesterday, warn_days=2, crit_days=4) == "fresh"
    assert cron_router._staleness_label_from_iso_date(week_ago, warn_days=2, crit_days=4) == "stale"
    assert cron_router._staleness_label_from_iso_date(long_ago, warn_days=2, crit_days=4) == "stale"


def test_staleness_label_from_iso_date_handles_garbage():
    # Bad input shouldn't crash the health endpoint — just label it unknown.
    assert cron_router._staleness_label_from_iso_date("not-a-date", warn_days=2, crit_days=4) == "unknown"


def test_health_route_registered():
    paths = {getattr(r, "path", "") for r in cron_router.router.routes}
    # Router is mounted with prefix="/cron"; the path includes the prefix.
    assert "/cron/health" in paths
    assert "/cron/sync" in paths
    assert "/cron/reports" in paths
    assert "/cron/search-terms" in paths
    assert "/cron/products" in paths
    assert "/cron/trigger/sync" in paths
    assert "/cron/trigger/reports" in paths


def test_cron_sync_uses_async_create_task(monkeypatch):
    """Guardrail — make sure cron_sync stays non-blocking.

    Previously this awaited ``run_full_sync`` inline, which exceeded QStash's
    delivery timeout on big accounts and triggered duplicate retries. Test
    fails if anyone re-introduces the blocking version.
    """
    src = Path(BACKEND_DIR / "app" / "routers" / "cron.py").read_text()
    # Locate the cron_sync function body and assert it spawns a task.
    fn_start = src.find('async def cron_sync(')
    assert fn_start != -1
    next_def = src.find('\n@router.', fn_start + 1)
    body = src[fn_start:next_def] if next_def != -1 else src[fn_start:]
    assert 'asyncio.create_task' in body, "cron_sync must remain non-blocking"
    assert '"queued"' in body or "'queued'" in body, "cron_sync must return queued status"
    assert 'await run_full_sync(' not in body, (
        "cron_sync must not await run_full_sync inline — that's what blocked QStash. "
        "Use _run_sync_background via asyncio.create_task instead."
    )


def test_trigger_sync_is_non_blocking_too():
    src = Path(BACKEND_DIR / "app" / "routers" / "cron.py").read_text()
    fn_start = src.find('async def trigger_sync(')
    assert fn_start != -1
    next_def = src.find('\n@router.', fn_start + 1)
    body = src[fn_start:next_def] if next_def != -1 else src[fn_start:]
    assert 'asyncio.create_task' in body, "trigger_sync must be non-blocking too"
    assert 'await run_full_sync(' not in body
