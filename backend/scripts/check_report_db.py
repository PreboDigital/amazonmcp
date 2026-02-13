#!/usr/bin/env python3
"""
Diagnostic script to inspect campaign_performance_daily and account_performance_daily.
Helps debug report data not updating issues.

Run from backend directory:
  python scripts/check_report_db.py
"""

import asyncio
import sys
from pathlib import Path

# Ensure backend root is on path
backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))

from sqlalchemy import text
from app.database import engine


async def main():
    async with engine.begin() as conn:
        # Credentials
        cred_result = await conn.execute(text("SELECT id, name, profile_id FROM credentials LIMIT 5"))
        creds = cred_result.fetchall()
        print("=== CREDENTIALS ===")
        for c in creds:
            print(f"  id={c[0]}, name={c[1]}, profile_id={c[2]}")
        if not creds:
            print("  (no credentials)")
            return

        cred_id = str(creds[0][0])

        # Campaign performance daily
        cpd_result = await conn.execute(text("""
            SELECT date, profile_id, COUNT(*) as rows, SUM(spend)::numeric(12,2) as total_spend, SUM(sales)::numeric(12,2) as total_sales
            FROM campaign_performance_daily
            WHERE credential_id = :cid
            GROUP BY date, profile_id
            ORDER BY date DESC
            LIMIT 20
        """), {"cid": cred_id})
        rows = cpd_result.fetchall()
        print("\n=== CAMPAIGN_PERFORMANCE_DAILY (by date range) ===")
        if rows:
            for r in rows:
                print(f"  date={r[0]}, profile_id={r[1]}, rows={r[2]}, spend={r[3]}, sales={r[4]}")
        else:
            print("  (no rows for this credential)")

        # Account performance daily
        apd_result = await conn.execute(text("""
            SELECT date, profile_id, total_spend, total_sales, total_clicks, total_impressions, source
            FROM account_performance_daily
            WHERE credential_id = :cid
            ORDER BY date DESC
            LIMIT 20
        """), {"cid": cred_id})
        apd_rows = apd_result.fetchall()
        print("\n=== ACCOUNT_PERFORMANCE_DAILY ===")
        if apd_rows:
            for r in apd_rows:
                print(f"  date={r[0]}, profile_id={r[1]}, spend={r[2]}, sales={r[3]}, clicks={r[4]}, impressions={r[5]}, source={r[6]}")
        else:
            print("  (no rows for this credential)")

        # Reports table (recent)
        rep_result = await conn.execute(text("""
            SELECT id, date_range_start, date_range_end, status, created_at
            FROM reports
            WHERE credential_id = :cid
            ORDER BY created_at DESC
            LIMIT 10
        """), {"cid": cred_id})
        rep_rows = rep_result.fetchall()
        print("\n=== RECENT REPORTS ===")
        if rep_rows:
            for r in rep_rows:
                print(f"  id={r[0]}, range={r[1]} to {r[2]}, status={r[3]}, created={r[4]}")
        else:
            print("  (no reports)")

        # Campaign table (fallback source)
        camp_result = await conn.execute(text("""
            SELECT COUNT(*), SUM(spend)::numeric(12,2), SUM(sales)::numeric(12,2)
            FROM campaigns
            WHERE credential_id = :cid
        """), {"cid": cred_id})
        camp = camp_result.fetchone()
        print("\n=== CAMPAIGNS TABLE (fallback) ===")
        print(f"  campaigns={camp[0]}, total_spend={camp[1]}, total_sales={camp[2]}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
