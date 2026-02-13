#!/usr/bin/env python3
"""
Clear old performance data (stored with incorrect profile_id) and optionally
trigger a fresh sync.

Run from backend directory:
  python scripts/clear_and_resync.py

Or with refetch instructions:
  python scripts/clear_and_resync.py --refetch
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Ensure backend root is on path
backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))

from sqlalchemy import text
from app.database import engine


async def clear_old_data():
    """Clear tables that may have been populated with wrong profile_id."""
    tables = [
        "campaign_performance_daily",
        "account_performance_daily",
        "campaigns",
        "search_term_performance",
    ]
    await _clear_tables(tables)


async def clear_stale_data():
    """Clear stale/orphaned data (targets, reports, audit, activity, etc.)."""
    tables = [
        "targets",
        "ad_groups",
        "ads",
        "ad_associations",
        "reports",
        "audit_issues",
        "audit_opportunities",
        "audit_snapshots",
        "activity_log",
        "pending_changes",
        "ai_conversations",
    ]
    await _clear_tables(tables)


async def _clear_tables(tables):
    async with engine.begin() as conn:
        for table in tables:
            result = await conn.execute(text(f"DELETE FROM {table}"))
            deleted = result.rowcount
            print(f"  Cleared {table}: {deleted} rows")


async def main():
    parser = argparse.ArgumentParser(description="Clear old performance data and optionally refetch")
    parser.add_argument("--refetch", action="store_true", help="Print instructions for refetching (run Audit + Generate Report in UI)")
    parser.add_argument("--stale", action="store_true", help="Also clear stale data (targets, reports, audit, activity, etc.)")
    args = parser.parse_args()

    print("Clearing old performance data (profile_id mismatch fix)...")
    await clear_old_data()
    print("Done clearing performance data.")

    if args.stale:
        print("\nClearing stale data...")
        await clear_stale_data()
        print("Done clearing stale data.")

    if args.refetch:
        print("\n" + "=" * 60)
        print("Next steps — run a fresh fetch:")
        print("  1. In the app, go to Dashboard or Settings")
        print("  2. Select your account (e.g. The Stingray Group (GB))")
        print("  3. Go to Audit → Run Audit")
        print("  4. Go to Reports → Generate Report (or click 'Generate Report')")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
