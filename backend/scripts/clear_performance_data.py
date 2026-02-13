#!/usr/bin/env python3
"""
Clear only performance data tables (campaign_performance_daily, account_performance_daily).
Use this to remove stale or incorrect report data before re-generating.

Run from backend directory:
  python scripts/clear_performance_data.py

Options:
  --credential-id ID   Clear only for this credential (UUID)
  --include-reports    Also clear reports table (default: True)
  --no-reports         Do NOT clear reports table
  --dry-run            Show what would be deleted without deleting
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


async def clear_performance_data(
    credential_id: str | None = None,
    include_reports: bool = True,
    dry_run: bool = False,
):
    """Clear campaign_performance_daily, account_performance_daily, and optionally reports."""
    tables = [
        ("campaign_performance_daily", "credential_id"),
        ("account_performance_daily", "credential_id"),
    ]
    if include_reports:
        tables.append(("reports", "credential_id"))

    async with engine.begin() as conn:
        for table, id_col in tables:
            if dry_run:
                if credential_id:
                    result = await conn.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE {id_col} = :cid"),
                        {"cid": credential_id},
                    )
                else:
                    result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.scalar()
                scope = f"credential_id={credential_id}" if credential_id else "all"
                print(f"  [DRY-RUN] Would clear {table}: {count} rows ({scope})")
            else:
                if credential_id:
                    result = await conn.execute(
                        text(f"DELETE FROM {table} WHERE {id_col} = :cid"),
                        {"cid": credential_id},
                    )
                else:
                    result = await conn.execute(text(f"DELETE FROM {table}"))
                deleted = result.rowcount
                scope = f"credential_id={credential_id}" if credential_id else "all"
                print(f"  Cleared {table}: {deleted} rows ({scope})")


async def main():
    parser = argparse.ArgumentParser(
        description="Clear only performance data (campaign_performance_daily, account_performance_daily)"
    )
    parser.add_argument(
        "--credential-id",
        type=str,
        help="Clear only for this credential UUID",
    )
    parser.add_argument(
        "--include-reports",
        action="store_true",
        default=True,
        help="Also clear reports table (default)",
    )
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="Do NOT clear reports table",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting",
    )
    args = parser.parse_args()

    include_reports = args.include_reports and not args.no_reports

    print("Clearing performance data...")
    if args.credential_id:
        print(f"  Scope: credential_id={args.credential_id}")
    else:
        print("  Scope: all credentials")
    if args.dry_run:
        print("  Mode: DRY RUN (no changes will be made)")

    await clear_performance_data(
        credential_id=args.credential_id,
        include_reports=include_reports,
        dry_run=args.dry_run,
    )

    print("\nDone. Run Audit + Generate Report in the app to repopulate.")


if __name__ == "__main__":
    asyncio.run(main())
