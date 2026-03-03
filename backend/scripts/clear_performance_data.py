#!/usr/bin/env python3
"""
Clear only performance data tables (campaign_performance_daily, account_performance_daily,
product_performance_daily) and zero Campaign table metrics.
Reports page falls back to Campaign when daily is empty.

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
    """Clear campaign_performance_daily, account_performance_daily, product_performance_daily,
    optionally reports,
    and zero out Campaign table metrics (Reports page fallback uses campaigns when daily is empty).
    """
    tables = [
        ("campaign_performance_daily", "credential_id"),
        ("account_performance_daily", "credential_id"),
        ("product_performance_daily", "credential_id"),
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

        # Zero out Campaign table metrics — Reports page fallback uses campaigns when daily is empty
        has_metrics_where = (
            "(spend IS NOT NULL AND spend != 0) OR (sales IS NOT NULL AND sales != 0) "
            "OR (impressions IS NOT NULL AND impressions != 0) OR (clicks IS NOT NULL AND clicks != 0) "
            "OR (orders IS NOT NULL AND orders != 0)"
        )
        if dry_run:
            if credential_id:
                result = await conn.execute(
                    text(f"SELECT COUNT(*) FROM campaigns WHERE ({has_metrics_where}) AND credential_id = :cid"),
                    {"cid": credential_id},
                )
            else:
                result = await conn.execute(text(f"SELECT COUNT(*) FROM campaigns WHERE {has_metrics_where}"))
            count = result.scalar()
            scope = f"credential_id={credential_id}" if credential_id else "all"
            print(f"  [DRY-RUN] Would zero campaigns metrics: {count} rows ({scope})")
        else:
            if credential_id:
                zero_sql = text(
                    f"UPDATE campaigns SET spend = 0, sales = 0, impressions = 0, clicks = 0, orders = 0, acos = NULL, roas = NULL "
                    f"WHERE ({has_metrics_where}) AND credential_id = :cid"
                )
                result = await conn.execute(zero_sql, {"cid": credential_id})
            else:
                zero_sql = text(
                    f"UPDATE campaigns SET spend = 0, sales = 0, impressions = 0, clicks = 0, orders = 0, acos = NULL, roas = NULL "
                    f"WHERE {has_metrics_where}"
                )
                result = await conn.execute(zero_sql)
            updated = result.rowcount
            scope = f"credential_id={credential_id}" if credential_id else "all"
            print(f"  Zeroed campaigns metrics: {updated} rows ({scope})")


async def main():
    parser = argparse.ArgumentParser(
        description="Clear only performance data (campaign/account/product daily tables)"
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
