#!/usr/bin/env python3
"""Read-only diagnostic for prod DB.

Snapshots state relevant to Phase 0–3 fixes:
- Credential row count + how many secrets look encrypted vs plaintext
- Account / profile distribution
- Report job status mix + failure repetition
- Last-sync ages by credential
- Pending change queue depth

NEVER runs INSERT/UPDATE/DELETE. Safe to run any time.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from sqlalchemy import func, select

    from app.crypto import looks_encrypted
    from app.database import async_session
    from app.models import (
        Account,
        Campaign,
        Credential,
        PendingChange,
        Report,
    )
    from app.utils import utcnow

    print("=" * 72)
    print("PROD DIAGNOSTIC — read-only")
    print("=" * 72)

    async with async_session() as db:
        # ── Credentials ─────────────────────────────────────────────
        creds_result = await db.execute(select(Credential))
        creds = creds_result.scalars().all()
        print(f"\n[CREDENTIALS] {len(creds)} total")
        statuses = Counter(c.status for c in creds)
        print(f"  by status: {dict(statuses)}")

        plaintext_fields = {"client_secret": 0, "access_token": 0, "refresh_token": 0}
        encrypted_fields = {"client_secret": 0, "access_token": 0, "refresh_token": 0}
        none_fields = {"client_secret": 0, "access_token": 0, "refresh_token": 0}
        for c in creds:
            for field in plaintext_fields:
                v = getattr(c, field, None)
                if v is None:
                    none_fields[field] += 1
                elif looks_encrypted(v):
                    encrypted_fields[field] += 1
                else:
                    plaintext_fields[field] += 1

        print("  secret-field encryption status:")
        for field in plaintext_fields:
            print(
                f"    {field:>14}: encrypted={encrypted_fields[field]:>3}  "
                f"plaintext={plaintext_fields[field]:>3}  null={none_fields[field]:>3}"
            )

        # ── Accounts / profiles ─────────────────────────────────────
        acct_count_result = await db.execute(select(func.count()).select_from(Account))
        acct_count = acct_count_result.scalar() or 0
        marketplace_q = await db.execute(
            select(Account.marketplace, func.count())
            .group_by(Account.marketplace)
        )
        mp_counts = dict(marketplace_q.all())
        print(f"\n[ACCOUNTS] {acct_count} discovered, marketplaces: {mp_counts}")

        per_cred = await db.execute(
            select(Account.credential_id, func.count())
            .group_by(Account.credential_id)
        )
        per_cred_counts = [n for _, n in per_cred.all()]
        if per_cred_counts:
            print(
                f"  profiles per credential: min={min(per_cred_counts)} "
                f"max={max(per_cred_counts)} avg={sum(per_cred_counts)/len(per_cred_counts):.1f}"
            )
        multi = sum(1 for n in per_cred_counts if n > 1)
        print(f"  multi-profile credentials (benefit from Phase 1 TZ fix): {multi}")

        # ── Reports ────────────────────────────────────────────────
        report_count_result = await db.execute(select(func.count()).select_from(Report))
        report_count = report_count_result.scalar() or 0
        status_q = await db.execute(
            select(Report.status, func.count()).group_by(Report.status)
        )
        status_mix = dict(status_q.all())
        print(f"\n[REPORTS] {report_count} total, by status: {status_mix}")

        # Report failures clustered by date_range_end — Phase 0 loop signature
        failed_q = await db.execute(
            select(Report.date_range_end, Report.report_type, func.count())
            .where(Report.status == "failed")
            .group_by(Report.date_range_end, Report.report_type)
            .order_by(func.count().desc())
            .limit(20)
        )
        failed_rows = failed_q.all()
        if failed_rows:
            print("  top failure (date_range_end, type) — Phase 0 loop signature:")
            for d, rtype, n in failed_rows:
                marker = " ⚠️" if n >= 2 else ""
                print(f"    {d}  type={rtype:<12}  n={n}{marker}")

        # Show 10 most recent failed reports with raw error context
        recent_failed = await db.execute(
            select(
                Report.created_at,
                Report.date_range_start,
                Report.date_range_end,
                Report.report_type,
                Report.raw_response,
            )
            .where(Report.status == "failed")
            .order_by(Report.created_at.desc())
            .limit(10)
        )
        print("\n  most recent 10 failures:")
        for created, ds, de, rtype, raw in recent_failed.all():
            err = ""
            if isinstance(raw, dict):
                err = (
                    raw.get("error")
                    or raw.get("error_message")
                    or raw.get("message")
                    or ""
                )
            err_excerpt = str(err)[:140].replace("\n", " ")
            print(f"    {created}  {ds}→{de}  {rtype:<12}  {err_excerpt}")

        # ── Campaign sync recency ──────────────────────────────────
        camp_count_result = await db.execute(select(func.count()).select_from(Campaign))
        camp_count = camp_count_result.scalar() or 0
        latest_q = await db.execute(select(func.max(Campaign.synced_at)))
        latest = latest_q.scalar()
        oldest_q = await db.execute(select(func.min(Campaign.synced_at)))
        oldest = oldest_q.scalar()
        print(f"\n[CAMPAIGNS] {camp_count} cached")
        if latest:
            age = (utcnow() - latest).total_seconds() / 3600
            print(f"  most recent sync: {latest}  ({age:.1f}h ago)")
        if oldest:
            print(f"  oldest sync:      {oldest}")

        # ── Pending changes ────────────────────────────────────────
        pc_count_result = await db.execute(
            select(PendingChange.status, func.count()).group_by(PendingChange.status)
        )
        pc_mix = dict(pc_count_result.all())
        print(f"\n[PENDING CHANGES] {pc_mix}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
