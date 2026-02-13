"""
Authentication & Authorization — API-key based auth middleware.

All API endpoints (except /api/health) require a valid API key.
Set the API_KEY environment variable; requests must include the header:
    Authorization: Bearer <API_KEY>

In development mode (ENVIRONMENT=development) with no API_KEY set,
auth is disabled so local development is frictionless.
"""

import logging
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import get_settings

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """
    FastAPI dependency that enforces Bearer-token API-key auth.

    - In production: API_KEY must be set and every request must include
      ``Authorization: Bearer <key>``.
    - In development: if API_KEY is empty, auth is skipped so local dev
      is painless.  If API_KEY *is* set even in dev, it is enforced.

    Returns the validated API key string.
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

    # Key is configured — enforce it
    if not credentials or credentials.credentials != api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Include header: Authorization: Bearer <API_KEY>",
        )

    return credentials.credentials
