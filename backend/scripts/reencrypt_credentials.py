#!/usr/bin/env python3
"""Re-encrypt legacy plaintext credential secrets in the DB.

Background
----------
Earlier deployments stored ``Credential.client_secret``, ``access_token``,
and ``refresh_token`` either in plaintext (no ``ENCRYPTION_KEY`` set) or
under a different ``ENCRYPTION_KEY``. ``app.crypto.decrypt_value`` masks
these failures by returning the raw stored value, which silently sends
garbage to Amazon LwA.

This script walks every Credential row, decrypts each secret with the
**current** key, and re-encrypts any that look like plaintext (Fernet
tokens start with ``gAAAAA``). Rows that decrypt cleanly are left alone.

Usage
-----
Run from ``backend/`` after ``ENCRYPTION_KEY`` is set in the environment::

    python -m scripts.reencrypt_credentials              # dry-run
    python -m scripts.reencrypt_credentials --apply      # actually write
    python -m scripts.reencrypt_credentials --apply --include-expired

Flip ``REQUIRE_ENCRYPTED_SECRETS=1`` in production once the dry-run shows
zero remaining plaintext rows so future decrypt failures raise instead of
silently degrading.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("reencrypt_credentials")


SECRET_FIELDS = ("client_secret", "access_token", "refresh_token")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--include-expired",
        action="store_true",
        help="Also re-encrypt credentials whose status is 'expired' (skipped by default).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose per-row logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from sqlalchemy import select

    from app.crypto import _get_fernet, encrypt_value, looks_encrypted
    from app.database import async_session
    from app.models import Credential

    if _get_fernet() is None:
        logger.error(
            "ENCRYPTION_KEY is not configured — nothing to migrate. "
            "Set ENCRYPTION_KEY in the environment and re-run."
        )
        return 2

    summary = {
        "credentials_scanned": 0,
        "fields_already_encrypted": 0,
        "fields_reencrypted": 0,
        "fields_unreadable": 0,
        "skipped_expired": 0,
    }

    async with async_session() as db:
        result = await db.execute(select(Credential).order_by(Credential.created_at.asc()))
        credentials = result.scalars().all()
        summary["credentials_scanned"] = len(credentials)

        for cred in credentials:
            if cred.status == "expired" and not args.include_expired:
                summary["skipped_expired"] += 1
                logger.debug("Skipping expired credential %s (%s)", cred.id, cred.name)
                continue

            for field in SECRET_FIELDS:
                value = getattr(cred, field, None)
                if value is None:
                    continue

                if looks_encrypted(value):
                    summary["fields_already_encrypted"] += 1
                    continue

                # Treat the stored value as plaintext and re-encrypt it.
                try:
                    new_value = encrypt_value(value)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.error(
                        "Failed to re-encrypt credential %s field %s: %s",
                        cred.id,
                        field,
                        exc,
                    )
                    summary["fields_unreadable"] += 1
                    continue

                logger.info(
                    "%s credential %s field %s",
                    "Would re-encrypt" if not args.apply else "Re-encrypting",
                    cred.id,
                    field,
                )
                if args.apply:
                    setattr(cred, field, new_value)
                summary["fields_reencrypted"] += 1

        if args.apply:
            await db.commit()
            logger.info("Committed %s re-encryption updates.", summary["fields_reencrypted"])
        else:
            logger.info("Dry run complete. Re-run with --apply to persist changes.")

    logger.info("Summary: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
