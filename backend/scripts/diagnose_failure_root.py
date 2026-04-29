#!/usr/bin/env python3
"""Drill into the recurring report failures + campaign sync absence.

Read-only. Prints:
- Raw error payload of the most recent 5 failed reports
- Stuck-date histogram (how many days the same failure date has been retried)
- Account marketplace breakdown (to understand Phase 1 TZ blast radius)
- Whether campaign cache is empty because of scope-error vs. just never-synced
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from sqlalchemy import select, func, desc

    from app.database import async_session
    from app.models import Account, ActivityLog, Credential, Report

    print("=" * 72)
    print("FAILURE ROOT-CAUSE DIAGNOSTIC — read-only")
    print("=" * 72)

    async with async_session() as db:
        # ── Stuck failure dates: how many days has the same date_range_end
        #    been the failing date?
        cred_result = await db.execute(select(Credential))
        cred = cred_result.scalars().first()
        if not cred:
            print("No credentials.")
            return 1

        # Each row's "stuck date" lives inside raw_response.error per the
        # earlier diagnostic ("Amazon report fetch failed for 2026-03-28").
        all_failed = await db.execute(
            select(Report.created_at, Report.date_range_start, Report.date_range_end, Report.raw_response)
            .where(Report.status == "failed")
            .order_by(Report.created_at.asc())
        )
        rows = all_failed.all()

        stuck_counter: Counter[str] = Counter()
        for created, ds, de, raw in rows:
            if not isinstance(raw, dict):
                continue
            err = raw.get("error") or raw.get("message") or ""
            # Pull "for YYYY-MM-DD" out of the message
            for token in str(err).split():
                if token.count("-") == 2 and len(token) == 10:
                    stuck_counter[token] += 1
                    break

        print("\n[STUCK-DATE HISTOGRAM] failures by the date Amazon refuses:")
        for d, n in sorted(stuck_counter.items(), key=lambda x: -x[1])[:15]:
            marker = "  ⚠️  recurring" if n >= 5 else ""
            print(f"  {d}: {n} failures{marker}")

        # ── Most recent 5 failures: full raw payload ────────────────
        print("\n[RAW PAYLOADS — most recent 5 failures]")
        recent = await db.execute(
            select(Report.created_at, Report.report_type, Report.raw_response, Report.report_data)
            .where(Report.status == "failed")
            .order_by(desc(Report.created_at))
            .limit(5)
        )
        for created, rtype, raw, data in recent.all():
            print(f"\n  --- {created} type={rtype} ---")
            payload = raw or data or {}
            try:
                pretty = json.dumps(payload, default=str, indent=2)
            except Exception:
                pretty = str(payload)
            for line in pretty.splitlines()[:25]:
                print(f"    {line}")

        # ── Account scope diagnostic ────────────────────────────────
        print("\n[ACCOUNTS] scope details")
        active_profile_id = cred.profile_id
        print(f"  active credential profile_id: {active_profile_id}")
        if active_profile_id:
            ap_result = await db.execute(
                select(Account)
                .where(Account.credential_id == cred.id)
                .where(Account.profile_id == active_profile_id)
            )
            ap = ap_result.scalar_one_or_none()
            if ap:
                from app.services.account_scope import (
                    get_campaign_sync_scope_error,
                    is_global_root_account,
                    is_marketplace_child_account,
                )
                print(f"  account: {ap.account_name}")
                print(f"  marketplace: {ap.marketplace}")
                print(f"  account_type: {ap.account_type}")
                raw = ap.raw_data or {}
                print(f"  raw_data.isGlobalAccount: {raw.get('isGlobalAccount')}")
                print(f"  raw_data has marketplace_alt: {bool(raw.get('marketplace_alt'))}")
                print(f"  is_marketplace_child_account: {is_marketplace_child_account(ap)}")
                print(f"  is_global_root_account: {is_global_root_account(ap)}")
                err = get_campaign_sync_scope_error(ap, active_profile_id)
                print(f"  campaign-sync scope error: {err or '(none — sync allowed)'}")
            else:
                print("  active profile_id has no Account row")

        # ── Recent activity: report_sync_failed entries ─────────────
        print("\n[ACTIVITY LOG] recent 10 'reporting' / 'failed' rows")
        act = await db.execute(
            select(ActivityLog.created_at, ActivityLog.action, ActivityLog.description, ActivityLog.status)
            .where(ActivityLog.category == "reporting")
            .order_by(desc(ActivityLog.created_at))
            .limit(10)
        )
        for created, action, desc_text, status in act.all():
            print(f"  {created} [{status}] {action}: {desc_text[:120] if desc_text else ''}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
