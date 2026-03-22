"""Authentication dependencies for FastAPI routes."""

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from convocation.auth.models import Role, User
from convocation.auth.security import decode_access_token
from convocation.db import get_db


async def get_current_user(
    session: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_access_token(access_token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await session.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def get_current_user_or_none(
    session: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
) -> User | None:
    """Return the current user if authenticated, or None. Never raises."""
    if not access_token:
        return None
    payload = decode_access_token(access_token)
    if payload is None:
        return None
    result = await session.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def require_officer(user: User = Depends(get_current_user)) -> User:
    if user.role not in (Role.owner, Role.officer):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Officers only")
    return user


async def require_owner(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner only")
    return user
