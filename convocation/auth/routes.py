"""Auth routes — login, invite, signup, user management."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from convocation.auth.deps import get_current_user, require_officer, require_owner
from convocation.auth.models import AuditLog, Invite, Role, User
from convocation.auth.security import create_access_token, hash_password, verify_password
from convocation.config import settings
from convocation.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class InviteRequest(BaseModel):
    email: EmailStr
    role: Role = Role.member


class SignupRequest(BaseModel):
    token: str
    display_name: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/login")
async def login(req: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    token = create_access_token(user.id, user.role.value)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="strict",
        max_age=settings.jwt_expire_minutes * 60,
        secure=not settings.debug,
    )
    return {"ok": True, "user": {"id": user.id, "email": user.email, "role": user.role.value, "display_name": user.display_name}}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role.value,
        "display_name": user.display_name,
    }


@router.post("/invite")
async def invite_user(
    req: InviteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_officer),
):
    # Officers can invite members; only owners can invite officers
    if req.role == Role.owner:
        raise HTTPException(status_code=400, detail="Cannot invite owners")
    if req.role == Role.officer and user.role != Role.owner:
        raise HTTPException(status_code=403, detail="Only owners can invite officers")

    # Check if user already exists
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User with this email already exists")

    token = secrets.token_urlsafe(48)
    invite = Invite(
        email=req.email,
        role=req.role,
        token=token,
        invited_by=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.invite_expire_hours),
    )
    db.add(invite)

    audit = AuditLog(user_id=user.id, action="user.invite", target=req.email, detail=f"role={req.role.value}")
    db.add(audit)
    await db.commit()

    invite_url = f"{settings.site_url}/signup?token={token}"
    return {"ok": True, "invite_url": invite_url, "expires_in_hours": settings.invite_expire_hours}


@router.post("/signup")
async def signup(req: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Invite).where(Invite.token == req.token, Invite.accepted == False))
    invite = result.scalar_one_or_none()

    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invite")

    if invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invite has expired")

    user = User(
        email=invite.email,
        display_name=req.display_name,
        password_hash=hash_password(req.password),
        role=invite.role,
    )
    db.add(user)

    invite.accepted = True

    audit = AuditLog(user_id=user.id, action="user.signup", target=invite.email)
    db.add(audit)
    await db.commit()

    token = create_access_token(user.id, user.role.value)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="strict",
        max_age=settings.jwt_expire_minutes * 60,
        secure=not settings.debug,
    )
    return {"ok": True, "user": {"id": user.id, "email": user.email, "role": user.role.value, "display_name": user.display_name}}


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.password_hash = hash_password(req.new_password)
    audit = AuditLog(user_id=user.id, action="user.password_change", target=user.email)
    db.add(audit)
    await db.commit()
    return {"ok": True}


@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_officer),
):
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "role": u.role.value,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ]
