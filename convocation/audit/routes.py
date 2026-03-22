"""Audit log routes — full activity tracking."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from convocation.auth.deps import require_officer
from convocation.auth.models import AuditLog, User
from convocation.db import get_db

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/log")
async def get_audit_log(
    limit: int = Query(50, le=200),
    offset: int = 0,
    action: str | None = None,
    user_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_officer),
):
    """Get paginated audit log entries."""
    query = select(AuditLog).options(selectinload(AuditLog.user)).order_by(AuditLog.timestamp.desc())

    if action:
        query = query.where(AuditLog.action.startswith(action))
    if user_id:
        query = query.where(AuditLog.user_id == user_id)

    # Get total count
    count_query = select(func.count()).select_from(AuditLog)
    if action:
        count_query = count_query.where(AuditLog.action.startswith(action))
    if user_id:
        count_query = count_query.where(AuditLog.user_id == user_id)
    total = (await db.execute(count_query)).scalar()

    result = await db.execute(query.offset(offset).limit(limit))
    entries = result.scalars().all()

    return {
        "total": total,
        "entries": [
            {
                "id": e.id,
                "user": {"id": e.user.id, "display_name": e.user.display_name, "email": e.user.email},
                "action": e.action,
                "target": e.target,
                "detail": e.detail,
                "commit_sha": e.commit_sha,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in entries
        ],
    }
