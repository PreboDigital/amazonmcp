"""Report-date skip service.

Background
==========

Amazon's ``reporting-create_report`` endpoint occasionally refuses to
produce data for a specific historical date. Once a date enters that
state, *every* subsequent attempt fails with the same error. The daily
cron used to retry the same dead day forever, so the entire 30-day
sliding window never advanced — see the 2026-03-28 prod incident.

Phase 4 added per-day skip tolerance inside a single sync run (so the
window can finish with N skips). Phase 5 promotes a date to a
**permanent** skip list once it has failed across multiple consecutive
sync attempts. Future syncs filter that date out *before* hitting MCP,
so we stop paying the latency / log-noise cost of doomed retries.

State storage
=============

Per-credential JSON state lives on ``Credential.credential_metadata``::

    {
        "report_skip": {
            "<profile_id_or__none__>": {
                "permanent": ["2026-03-28", ...],
                "counters": {
                    "2026-03-30": {"count": 2, "last_error": "...",
                                    "last_seen_at": "..."}
                }
            }
        }
    }

When a date's counter reaches ``PROMOTE_THRESHOLD`` it moves to
``permanent`` and the counter is dropped. Successful syncs always clear
the counter (and pop the date from ``permanent`` — once Amazon catches
up, the date should re-enter the rotation).

This module is intentionally synchronous-shaped: callers pass an
``AsyncSession`` and ``Credential`` and we mutate the column in place.
The caller commits.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Credential

logger = logging.getLogger(__name__)


PROMOTE_THRESHOLD = 3
MAX_PERMANENT_DATES = 90
MAX_COUNTER_DATES = 200

_METADATA_KEY = "report_skip"
_NONE_PROFILE_KEY = "__none__"


def _profile_key(profile_id: Optional[str]) -> str:
    return profile_id if profile_id else _NONE_PROFILE_KEY


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_state(cred: Credential) -> dict:
    md = cred.credential_metadata
    if not isinstance(md, dict):
        md = {}
    state = md.get(_METADATA_KEY)
    if not isinstance(state, dict):
        state = {}
        md[_METADATA_KEY] = state
    cred.credential_metadata = md
    return state


def _ensure_profile_bucket(cred: Credential, profile_id: Optional[str]) -> dict:
    state = _ensure_state(cred)
    key = _profile_key(profile_id)
    bucket = state.get(key)
    if not isinstance(bucket, dict):
        bucket = {"permanent": [], "counters": {}}
        state[key] = bucket
    if not isinstance(bucket.get("permanent"), list):
        bucket["permanent"] = []
    if not isinstance(bucket.get("counters"), dict):
        bucket["counters"] = {}
    return bucket


def get_permanent_skip_dates(
    cred: Credential, profile_id: Optional[str]
) -> set[str]:
    """Return the set of dates the cron should never even attempt."""
    md = cred.credential_metadata
    if not isinstance(md, dict):
        return set()
    state = md.get(_METADATA_KEY)
    if not isinstance(state, dict):
        return set()
    bucket = state.get(_profile_key(profile_id))
    if not isinstance(bucket, dict):
        return set()
    permanent = bucket.get("permanent")
    if not isinstance(permanent, list):
        return set()
    return {str(d) for d in permanent if isinstance(d, str)}


def filter_skipped(
    dates: Iterable[str],
    cred: Credential,
    profile_id: Optional[str],
) -> tuple[list[str], list[str]]:
    """Split ``dates`` into ``(eligible, permanently_skipped)``."""
    permanent = get_permanent_skip_dates(cred, profile_id)
    eligible: list[str] = []
    skipped: list[str] = []
    for d in dates:
        if d in permanent:
            skipped.append(d)
        else:
            eligible.append(d)
    return eligible, skipped


def _mark_modified(cred: Credential) -> None:
    """Force SQLAlchemy to detect the in-place JSON mutation.

    Tolerates non-ORM stand-ins (used in unit tests) where
    ``flag_modified`` would raise because there is no instance state.
    """
    if hasattr(cred, "_sa_instance_state"):
        flag_modified(cred, "credential_metadata")


def record_skip(
    cred: Credential,
    profile_id: Optional[str],
    day_str: str,
    error: str,
    *,
    threshold: int = PROMOTE_THRESHOLD,
) -> Optional[str]:
    """Increment the skip counter for ``day_str``.

    Returns the *promotion reason* (e.g. ``"promoted_after_3_failures"``)
    when the date crosses the threshold and is moved to the permanent
    skip list, otherwise ``None``.

    Caller is responsible for committing the session.
    """
    bucket = _ensure_profile_bucket(cred, profile_id)
    permanent: list[str] = bucket["permanent"]
    counters: dict = bucket["counters"]

    if day_str in permanent:
        return None

    entry = counters.get(day_str)
    if not isinstance(entry, dict):
        entry = {"count": 0, "last_error": "", "last_seen_at": ""}
    entry["count"] = int(entry.get("count") or 0) + 1
    entry["last_error"] = (error or "")[:500]
    entry["last_seen_at"] = _now_iso()
    counters[day_str] = entry

    promoted: Optional[str] = None
    if entry["count"] >= threshold:
        permanent.append(day_str)
        # Cap permanent list to the most recent N entries to bound the
        # column size on an account that legitimately has lots of stuck
        # historical days.
        if len(permanent) > MAX_PERMANENT_DATES:
            del permanent[: len(permanent) - MAX_PERMANENT_DATES]
        counters.pop(day_str, None)
        promoted = f"promoted_after_{entry['count']}_failures"
        logger.warning(
            "Report date %s promoted to permanent skip list for credential=%s profile=%s after %d failures",
            day_str,
            cred.id,
            profile_id,
            entry["count"],
        )

    # Bound counters dict — drop oldest when overflowing.
    if len(counters) > MAX_COUNTER_DATES:
        sorted_keys = sorted(
            counters.keys(),
            key=lambda k: counters[k].get("last_seen_at") or "",
        )
        for k in sorted_keys[: len(counters) - MAX_COUNTER_DATES]:
            counters.pop(k, None)

    _mark_modified(cred)
    return promoted


def clear_skip(
    cred: Credential, profile_id: Optional[str], day_str: str
) -> bool:
    """Reset state for ``day_str`` after a successful sync.

    Removes any counter and pops the date from the permanent list so it
    can re-enter the rotation if Amazon catches up.

    Returns True when state changed.
    """
    md = cred.credential_metadata
    if not isinstance(md, dict):
        return False
    state = md.get(_METADATA_KEY)
    if not isinstance(state, dict):
        return False
    bucket = state.get(_profile_key(profile_id))
    if not isinstance(bucket, dict):
        return False

    changed = False
    counters = bucket.get("counters")
    if isinstance(counters, dict) and day_str in counters:
        counters.pop(day_str, None)
        changed = True

    permanent = bucket.get("permanent")
    if isinstance(permanent, list) and day_str in permanent:
        bucket["permanent"] = [d for d in permanent if d != day_str]
        changed = True
        logger.info(
            "Report date %s cleared from permanent skip list for credential=%s profile=%s (Amazon caught up)",
            day_str,
            cred.id,
            profile_id,
        )

    if changed:
        _mark_modified(cred)
    return changed


async def update_after_sync(
    db: AsyncSession,
    cred: Credential,
    profile_id: Optional[str],
    *,
    skipped_days: list[dict],
    synced_day_strs: list[str],
    threshold: int = PROMOTE_THRESHOLD,
) -> dict:
    """Apply skip / clear updates for one completed sync run.

    The signature is async-shaped so future hooks (e.g. emitting an
    ActivityLog row when a date promotes, or persisting per-skip
    metrics) can ``await`` other services. Today the body is sync —
    callers still use ``await`` because the cron loop runs inside an
    async context.

    Args:
        skipped_days: list of ``{"date": "...", "error": "..."}`` dicts
            from the run.
        synced_day_strs: ISO date strings that completed successfully —
            their counters get reset.

    Returns a small report dict the caller can attach to ``raw_response``
    so the next-run UI can show which dates were promoted.
    """
    promoted: list[str] = []
    for entry in skipped_days or []:
        day_str = entry.get("date") if isinstance(entry, dict) else None
        if not isinstance(day_str, str) or not day_str:
            continue
        promotion = record_skip(
            cred,
            profile_id,
            day_str,
            error=str(entry.get("error") or "")[:500],
            threshold=threshold,
        )
        if promotion:
            promoted.append(day_str)

    cleared: list[str] = []
    for day_str in synced_day_strs or []:
        if not isinstance(day_str, str):
            continue
        if clear_skip(cred, profile_id, day_str):
            cleared.append(day_str)

    db.add(cred)
    return {
        "promoted_to_permanent": promoted,
        "cleared_after_success": cleared,
    }
