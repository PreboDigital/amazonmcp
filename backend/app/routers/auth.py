"""
Auth Router — Login, register (via invitation), whoami.
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import get_current_user
from app.database import get_db
from app.models import User, Invitation
from app.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    generate_invite_token,
    INVITATION_EXPIRE_DAYS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


# ── Schemas ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    token: str | None = None  # Optional for dev bootstrap (first user)
    email: EmailStr
    password: str
    name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class WhoAmIResponse(BaseModel):
    id: str
    email: str
    name: str | None
    role: str
    is_active: bool


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with email and password. Returns JWT."""
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()

    token = create_access_token(str(user.id), user.email, user.role)
    return TokenResponse(
        access_token=token,
        user={
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "is_active": user.is_active,
        },
    )


@router.post("/register", response_model=TokenResponse)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register with invitation token. Creates user and returns JWT.
    In development with no users exist, allows first-user bootstrap (becomes admin)."""
    from app.config import get_settings
    settings = get_settings()

    # Bootstrap: if no users exist and dev mode, allow first registration without token
    if not settings.is_production and not payload.token:
        from sqlalchemy import func
        count_result = await db.execute(select(func.count()).select_from(User))
        if count_result.scalar() == 0:
            existing = await db.execute(select(User).where(User.email == payload.email.lower()))
            if existing.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Email already registered")
            user = User(
                email=payload.email.lower(),
                password_hash=hash_password(payload.password),
                name=payload.name or payload.email.split("@")[0],
                role="admin",
                is_active=True,
            )
            db.add(user)
            await db.flush()
            token = create_access_token(str(user.id), user.email, user.role)
            return TokenResponse(
                access_token=token,
                user={
                    "id": str(user.id),
                    "email": user.email,
                    "name": user.name,
                    "role": user.role,
                    "is_active": user.is_active,
                },
            )

    if not payload.token:
        raise HTTPException(status_code=400, detail="Invitation token required")

    result = await db.execute(select(Invitation).where(Invitation.token == payload.token))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")
    if inv.status != "pending":
        raise HTTPException(status_code=400, detail="Invitation already used")
    if inv.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        inv.status = "expired"
        await db.flush()
        raise HTTPException(status_code=400, detail="Invitation has expired")
    if inv.email.lower() != payload.email.lower():
        raise HTTPException(status_code=400, detail="Email does not match invitation")

    # Check user doesn't already exist
    existing = await db.execute(select(User).where(User.email == payload.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        name=payload.name or payload.email.split("@")[0],
        role=inv.role,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    inv.status = "accepted"
    inv.accepted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()

    token = create_access_token(str(user.id), user.email, user.role)
    return TokenResponse(
        access_token=token,
        user={
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "is_active": user.is_active,
        },
    )


@router.get("/whoami", response_model=WhoAmIResponse)
async def whoami(user: User = Depends(get_current_user)):
    """Return current user. Requires JWT auth."""
    return WhoAmIResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
    )
