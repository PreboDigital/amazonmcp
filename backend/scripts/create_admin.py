#!/usr/bin/env python3
"""
Create the first admin user from FIRST_ADMIN_EMAIL and FIRST_ADMIN_PASSWORD in .env.
Run from backend/: python -m scripts.create_admin
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    from app.config import get_settings
    from app.database import async_session
    from app.models import User
    from app.services.auth_service import hash_password
    from sqlalchemy import select, func

    settings = get_settings()
    if not settings.first_admin_email or not settings.first_admin_password:
        print("Error: Set FIRST_ADMIN_EMAIL and FIRST_ADMIN_PASSWORD in .env")
        sys.exit(1)

    async with async_session() as db:
        r = await db.execute(select(func.count()).select_from(User))
        count = r.scalar() or 0
        if count > 0:
            print(f"Users already exist ({count}). Bootstrap only creates first admin when no users exist.")
            sys.exit(0)

        admin = User(
            email=settings.first_admin_email.lower(),
            password_hash=hash_password(settings.first_admin_password),
            name="Admin",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        print(f"Created admin user: {admin.email}")


if __name__ == "__main__":
    asyncio.run(main())
