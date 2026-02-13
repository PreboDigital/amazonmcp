"""
Authentication & Authorization — JWT (user login) and API-key (programmatic) auth.

- Web/frontend: JWT from login/register. Include: Authorization: Bearer <jwt>
- Programmatic/cron: API_KEY. Include: Authorization: Bearer <API_KEY>

In development with no API_KEY set, auth is skipped for local dev.
"""

import logging
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.services.auth_service import decode_access_token

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """
    Accept either JWT (user login) or API_KEY (programmatic).
    Returns "jwt" if JWT valid, or the API key string if API_KEY matched.
    """
    settings = get_settings()
    api_key = settings.api_key

    # Dev convenience: skip auth when no key is configured
    if not api_key:
        if settings.is_production:
            raise HTTPException(
                status_code=500,
                detail="Server misconfiguration: API_KEY must be set in production.",
            )
        return "dev-no-auth"

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization. Include header: Authorization: Bearer <token>",
        )

    token = credentials.credentials

    # Try JWT first (user login)
    payload = decode_access_token(token)
    if payload and payload.get("sub"):
        return "jwt"

    # Fall back to API_KEY
    if token == api_key:
        return token

    raise HTTPException(
        status_code=401,
        detail="Invalid or expired token. Please log in again.",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Require JWT and return the User from DB.
    Use for endpoints that need user context (whoami, user-specific data).
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization")

    payload = decode_access_token(credentials.credentials)
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid or expired token. Please log in again.")

    user_id = payload["sub"]
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require current user to be admin."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
