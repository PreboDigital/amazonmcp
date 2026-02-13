"""
Users Router — User management and invitations (admin only).
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import User, Invitation
from app.services.auth_service import hash_password, generate_invite_token, INVITATION_EXPIRE_DAYS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


# ── Schemas ────────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None
    role: str = "user"


class UserUpdateRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = "user"


class InvitationResponse(BaseModel):
    id: str
    email: str
    token: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime
    invite_link: str


class InvitationListResponse(BaseModel):
    id: str
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime
    invite_link: str | None = None  # Only for pending; built from token


# ── Endpoints ───────────────────────────────────────────────────────────

@router.get("", response_model=list[UserResponse])
async def list_users(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users. Admin only."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [
        UserResponse(
            id=str(u.id),
            email=u.email,
            name=u.name,
            role=u.role,
            is_active=u.is_active,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u in users
    ]


@router.post("", response_model=UserResponse)
async def create_user(
    payload: UserCreateRequest,
    current: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a user directly. Admin only."""
    existing = await db.execute(select(User).where(User.email == payload.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        name=payload.name or payload.email.split("@")[0],
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    current: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update user. Admin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.name is not None:
        user.name = payload.name
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active

    await db.flush()
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete user. Admin only. Cannot delete self."""
    if str(current.id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.flush()
    return {"ok": True}


# ── Invitations ────────────────────────────────────────────────────────

@router.post("/invitations", response_model=InvitationResponse)
async def create_invitation(
    payload: InviteRequest,
    current: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create invitation. Admin only."""
    # Check user doesn't already exist
    existing_user = await db.execute(select(User).where(User.email == payload.email.lower()))
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User already registered")

    # Check no pending invitation
    pending = await db.execute(
        select(Invitation).where(
            Invitation.email == payload.email.lower(),
            Invitation.status == "pending",
        )
    )
    if pending.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Pending invitation already exists for this email")

    token = generate_invite_token()
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=INVITATION_EXPIRE_DAYS)

    inv = Invitation(
        email=payload.email.lower(),
        token=token,
        role=payload.role,
        invited_by_id=current.id,
        status="pending",
        expires_at=expires_at,
    )
    db.add(inv)
    await db.flush()

    # Build invite link (frontend will use this)
    from app.config import get_settings
    settings = get_settings()
    # Use first CORS origin as base URL for invite link, or a placeholder
    base = settings.cors_origin_list[0] if settings.cors_origin_list else "https://amazonmcp-frontend-production.up.railway.app"
    invite_link = f"{base}/register?token={token}"

    # Send invite email via Resend (non-blocking; does not fail the request if email fails)
    from app.services.email_service import send_invite_email
    inviter_name = current.name or current.email
    asyncio.create_task(asyncio.to_thread(send_invite_email, inv.email, invite_link, inviter_name))

    return InvitationResponse(
        id=str(inv.id),
        email=inv.email,
        token=token,
        role=inv.role,
        status=inv.status,
        expires_at=inv.expires_at,
        created_at=inv.created_at,
        invite_link=invite_link,
    )


@router.get("/invitations", response_model=list[InvitationListResponse])
async def list_invitations(
    current: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List invitations. Admin only."""
    from app.config import get_settings
    settings = get_settings()
    base = settings.cors_origin_list[0] if settings.cors_origin_list else "https://amazonmcp-frontend-production.up.railway.app"

    result = await db.execute(
        select(Invitation).order_by(Invitation.created_at.desc())
    )
    invs = result.scalars().all()
    return [
        InvitationListResponse(
            id=str(i.id),
            email=i.email,
            role=i.role,
            status=i.status,
            expires_at=i.expires_at,
            created_at=i.created_at,
            invite_link=f"{base}/register?token={i.token}" if i.status == "pending" else None,
        )
        for i in invs
    ]


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(
    invitation_id: str,
    current: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revoke (delete) invitation. Admin only."""
    result = await db.execute(select(Invitation).where(Invitation.id == invitation_id))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found")

    await db.delete(inv)
    await db.flush()
    return {"ok": True}
