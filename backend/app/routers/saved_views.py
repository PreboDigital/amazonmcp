"""Saved filter presets (Reports / Dashboard) per user."""

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Literal

from app.auth import get_current_user
from app.database import get_db
from app.models import User, SavedView, Credential
from app.utils import parse_uuid

router = APIRouter(prefix="/saved-views", tags=["Saved views"])


class SavedViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    page: Literal["reports", "dashboard"]
    credential_id: Optional[str] = None
    profile_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)


class SavedViewResponse(BaseModel):
    id: str
    name: str
    page: str
    credential_id: Optional[str] = None
    profile_id: Optional[str] = None
    payload: dict
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[SavedViewResponse])
async def list_saved_views(
    page: Optional[str] = Query(None, description="Filter by page: reports | dashboard"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(SavedView).where(SavedView.user_id == user.id).order_by(SavedView.updated_at.desc())
    if page:
        if page not in ("reports", "dashboard"):
            raise HTTPException(400, "page must be reports or dashboard")
        q = q.where(SavedView.page == page)
    rows = (await db.execute(q)).scalars().all()
    return [
        SavedViewResponse(
            id=str(r.id),
            name=r.name,
            page=r.page,
            credential_id=str(r.credential_id) if r.credential_id else None,
            profile_id=r.profile_id,
            payload=r.payload or {},
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("", response_model=SavedViewResponse)
async def create_saved_view(
    body: SavedViewCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cid = None
    if body.credential_id:
        cid = parse_uuid(body.credential_id, "credential_id")
        exists = await db.execute(select(Credential).where(Credential.id == cid))
        if not exists.scalar_one_or_none():
            raise HTTPException(404, "Credential not found")
    sv = SavedView(
        id=uuid.uuid4(),
        user_id=user.id,
        name=body.name.strip(),
        page=body.page,
        credential_id=cid,
        profile_id=body.profile_id,
        payload=body.payload or {},
    )
    db.add(sv)
    await db.flush()
    await db.commit()
    await db.refresh(sv)
    return SavedViewResponse(
        id=str(sv.id),
        name=sv.name,
        page=sv.page,
        credential_id=str(sv.credential_id) if sv.credential_id else None,
        profile_id=sv.profile_id,
        payload=sv.payload or {},
        created_at=sv.created_at,
        updated_at=sv.updated_at,
    )


@router.delete("/{view_id}")
async def delete_saved_view(
    view_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vid = parse_uuid(view_id, "view_id")
    r = await db.execute(select(SavedView).where(SavedView.id == vid, SavedView.user_id == user.id))
    row = r.scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Saved view not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
