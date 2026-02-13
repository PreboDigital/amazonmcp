"""
Credentials Router — Manage Amazon Ads API credentials
All credential data stored in PostgreSQL.
Supports automatic token refresh via client_secret + refresh_token.
"""

from uuid import UUID
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from pydantic import BaseModel, ConfigDict
from typing import Optional
from app.database import get_db
from app.models import Credential, ActivityLog
from app.mcp_client import create_mcp_client
from app.services.token_service import get_mcp_client_with_fresh_token
from app.crypto import encrypt_value, decrypt_value
from app.utils import utcnow

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────
class CredentialCreate(BaseModel):
    name: str
    client_id: str
    client_secret: Optional[str] = None
    access_token: str
    refresh_token: Optional[str] = None
    profile_id: Optional[str] = None
    account_id: Optional[str] = None
    region: str = "na"


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    client_secret: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    profile_id: Optional[str] = None
    account_id: Optional[str] = None
    region: Optional[str] = None


class CredentialResponse(BaseModel):
    id: UUID
    name: str
    client_id: str
    has_client_secret: Optional[bool] = None
    has_refresh_token: Optional[bool] = None
    auto_refresh_enabled: Optional[bool] = None
    token_expires_at: Optional[datetime] = None
    profile_id: Optional[str]
    account_id: Optional[str]
    region: str
    status: str
    is_default: bool
    last_tested_at: Optional[datetime]
    tools_available: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Helpers ───────────────────────────────────────────────────────────
def _cred_to_response(cred: Credential) -> dict:
    """Convert a Credential model to a response dict with computed fields."""
    return {
        "id": cred.id,
        "name": cred.name,
        "client_id": cred.client_id,
        "has_client_secret": bool(cred.client_secret),
        "has_refresh_token": bool(cred.refresh_token),
        "auto_refresh_enabled": bool(cred.client_secret and cred.refresh_token),
        "token_expires_at": cred.token_expires_at,
        "profile_id": cred.profile_id,
        "account_id": cred.account_id,
        "region": cred.region,
        "status": cred.status,
        "is_default": cred.is_default,
        "last_tested_at": cred.last_tested_at,
        "tools_available": cred.tools_available,
        "created_at": cred.created_at,
        "updated_at": cred.updated_at,
    }


# ── Endpoints ────────────────────────────────────────────────────────
@router.get("")
async def list_credentials(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credential).order_by(Credential.created_at.desc()))
    creds = result.scalars().all()
    return [_cred_to_response(c) for c in creds]


@router.post("")
async def create_credential(payload: CredentialCreate, db: AsyncSession = Depends(get_db)):
    # Check if this is the first credential — make it default
    count_result = await db.execute(select(func.count()).select_from(Credential))
    is_first = count_result.scalar() == 0

    cred = Credential(
        name=payload.name,
        client_id=payload.client_id,
        client_secret=encrypt_value(payload.client_secret),
        access_token=encrypt_value(payload.access_token),
        refresh_token=encrypt_value(payload.refresh_token),
        profile_id=payload.profile_id,
        account_id=payload.account_id,
        region=payload.region,
        is_default=is_first,
        # Set initial expiry to 1 hour from now (standard Amazon token lifetime)
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1) if payload.client_secret and payload.refresh_token else None,
    )
    db.add(cred)
    await db.flush()

    # Log activity
    auto_refresh = "with auto-refresh" if payload.client_secret and payload.refresh_token else "manual tokens"
    db.add(ActivityLog(
        credential_id=cred.id,
        action="credential_created",
        category="settings",
        description=f"Added credential: {payload.name} ({auto_refresh})",
        entity_type="credential",
        entity_id=str(cred.id),
    ))

    await db.flush()
    await db.refresh(cred)
    return _cred_to_response(cred)


@router.put("/{cred_id}")
async def update_credential(cred_id: UUID, payload: CredentialUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credential).where(Credential.id == cred_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    update_data = payload.model_dump(exclude_none=True)
    # Encrypt sensitive fields before persisting
    _encrypted_fields = {"client_secret", "access_token", "refresh_token"}
    for key, value in update_data.items():
        if key in _encrypted_fields:
            value = encrypt_value(value)
        setattr(cred, key, value)
    cred.updated_at = datetime.now(timezone.utc)

    db.add(ActivityLog(
        credential_id=cred.id,
        action="credential_updated",
        category="settings",
        description=f"Updated credential: {cred.name}",
        entity_type="credential",
        entity_id=str(cred.id),
        details={"updated_fields": list(update_data.keys())},
    ))

    await db.flush()
    await db.refresh(cred)
    return _cred_to_response(cred)


@router.delete("/{cred_id}")
async def delete_credential(cred_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credential).where(Credential.id == cred_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    cred_name = cred.name

    # Log before deletion (with SET NULL FK)
    db.add(ActivityLog(
        credential_id=None,
        action="credential_deleted",
        category="settings",
        description=f"Deleted credential: {cred_name}",
        entity_type="credential",
        entity_id=str(cred_id),
    ))

    await db.delete(cred)
    return {"status": "deleted"}


@router.post("/{cred_id}/set-default")
async def set_default_credential(cred_id: UUID, db: AsyncSession = Depends(get_db)):
    # Unset all defaults
    await db.execute(update(Credential).values(is_default=False))
    # Set this one as default
    result = await db.execute(select(Credential).where(Credential.id == cred_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    cred.is_default = True

    db.add(ActivityLog(
        credential_id=cred.id,
        action="credential_set_default",
        category="settings",
        description=f"Set default credential: {cred.name}",
        entity_type="credential",
        entity_id=str(cred.id),
    ))

    await db.flush()
    return {"status": "ok", "default_id": str(cred_id)}


@router.post("/{cred_id}/test")
async def test_credential(cred_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Credential).where(Credential.id == cred_id))
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Use auto-refresh client — will refresh token if expired
    client = await get_mcp_client_with_fresh_token(cred, db)
    test_result = await client.test_connection()

    # Persist test results to DB
    cred.status = "active" if test_result["status"] == "connected" else "error"
    cred.last_tested_at = utcnow()
    cred.tools_available = test_result.get("tools_available")
    cred.updated_at = utcnow()

    # Add auto-refresh info to response
    test_result["auto_refresh_enabled"] = bool(cred.client_secret and cred.refresh_token)
    if cred.token_expires_at:
        test_result["token_expires_at"] = cred.token_expires_at.isoformat()

    db.add(ActivityLog(
        credential_id=cred.id,
        action="credential_tested",
        category="settings",
        description=f"Tested credential: {cred.name} — {test_result['status']}",
        entity_type="credential",
        entity_id=str(cred.id),
        details=test_result,
        status="success" if test_result["status"] == "connected" else "error",
    ))

    return test_result
