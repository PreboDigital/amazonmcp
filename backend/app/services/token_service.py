"""
Token Service — Automatic OAuth token refresh for Amazon Ads.
Checks token expiry before every MCP call and refreshes if needed.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Credential
from app.mcp_client import create_mcp_client, AmazonAdsMCP
from app.crypto import decrypt_value

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.amazon.com/auth/o2/token"

# Refresh 5 minutes before actual expiry to avoid race conditions
REFRESH_BUFFER = timedelta(minutes=5)


async def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """
    Exchange a refresh token for a new access token via Amazon LwA.
    Returns dict with access_token, expires_in, token_type.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()


def _make_aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (UTC). DB may return naive datetimes."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _token_is_expired(cred: Credential) -> bool:
    """Check if the access token is expired or about to expire."""
    if not cred.token_expires_at:
        # No expiry tracked — assume it might be expired, try refresh if we can
        return cred.client_secret is not None and cred.refresh_token is not None
    expires_at = _make_aware(cred.token_expires_at)
    return datetime.now(timezone.utc) >= (expires_at - REFRESH_BUFFER)


async def ensure_fresh_token(cred: Credential, db: AsyncSession) -> Credential:
    """
    Check if token is expired and refresh if needed.
    Updates the credential in the database with the new token.
    Returns the credential (possibly updated).
    """
    # Can't auto-refresh without client_secret and refresh_token
    if not cred.client_secret or not cred.refresh_token:
        return cred

    if not _token_is_expired(cred):
        return cred

    logger.info(f"Token expired for credential '{cred.name}', refreshing...")

    try:
        token_data = await refresh_access_token(
            client_id=cred.client_id,
            client_secret=decrypt_value(cred.client_secret),
            refresh_token=decrypt_value(cred.refresh_token),
        )

        # Update credential with new token (encrypted)
        from app.crypto import encrypt_value as _encrypt
        cred.access_token = _encrypt(token_data["access_token"])
        expires_in = token_data.get("expires_in", 3600)
        cred.token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_in)
        cred.status = "active"
        cred.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # If Amazon returned a new refresh token, save it too (encrypted)
        if "refresh_token" in token_data:
            cred.refresh_token = _encrypt(token_data["refresh_token"])

        await db.flush()
        logger.info(f"Token refreshed for '{cred.name}', expires in {expires_in}s")

    except httpx.HTTPStatusError as e:
        logger.error(f"Token refresh failed for '{cred.name}': {e.response.status_code} — {e.response.text}")
        cred.status = "expired"
        await db.flush()
    except Exception as e:
        logger.error(f"Token refresh failed for '{cred.name}': {e}")

    return cred


async def get_mcp_client_with_fresh_token(
    cred: Credential,
    db: AsyncSession,
    profile_id_override: Optional[str] = None,
) -> AmazonAdsMCP:
    """
    Get an MCP client with a guaranteed fresh access token.
    This is the main entry point — use this instead of create_mcp_client directly.
    profile_id_override: Use this profile_id instead of cred.profile_id (e.g. for approvals apply).
    """
    cred = await ensure_fresh_token(cred, db)

    profile_id = profile_id_override if profile_id_override is not None else cred.profile_id
    return create_mcp_client(
        client_id=cred.client_id,
        access_token=decrypt_value(cred.access_token),
        region=cred.region,
        profile_id=profile_id,
        account_id=cred.account_id,
    )
