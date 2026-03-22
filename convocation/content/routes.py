"""Content API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from convocation.auth.deps import get_current_user, require_officer
from convocation.auth.models import AuditLog, User
from convocation.content.store import ContentStore
from convocation.db import get_db

router = APIRouter(prefix="/api/content", tags=["content"])


def get_store() -> ContentStore:
    return ContentStore()


class ContentCreate(BaseModel):
    content_type: str
    slug: str
    title: str
    body: str
    metadata: dict = {}


class ContentUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    metadata: dict | None = None


class DiffPreview(BaseModel):
    content_type: str
    slug: str
    title: str | None = None
    body: str | None = None
    metadata: dict | None = None


class RevertRequest(BaseModel):
    commit_sha: str


@router.get("/{content_type}")
async def list_content(content_type: str, store: ContentStore = Depends(get_store)):
    try:
        return store.list_content(content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{content_type}/{slug}")
async def get_content(content_type: str, slug: str, store: ContentStore = Depends(get_store)):
    item = store.get(content_type, slug)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item


@router.post("/{content_type}")
async def create_content(
    req: ContentCreate,
    db=Depends(get_db),
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    meta = {**req.metadata, "title": req.title}
    try:
        sha = store.create(req.content_type, req.slug, meta, req.body, user.display_name)
    except (ValueError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit = AuditLog(
        user_id=user.id,
        action="content.create",
        target=f"{req.content_type}/{req.slug}",
        commit_sha=sha,
    )
    db.add(audit)
    await db.commit()
    return {"ok": True, "commit_sha": sha}


@router.put("/{content_type}/{slug}")
async def update_content(
    content_type: str,
    slug: str,
    req: ContentUpdate,
    db=Depends(get_db),
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    meta = req.metadata or {}
    if req.title:
        meta["title"] = req.title
    try:
        sha = store.update(content_type, slug, meta or None, req.body, user.display_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    audit = AuditLog(
        user_id=user.id,
        action="content.update",
        target=f"{content_type}/{slug}",
        commit_sha=sha,
    )
    db.add(audit)
    await db.commit()
    return {"ok": True, "commit_sha": sha}


@router.delete("/{content_type}/{slug}")
async def delete_content(
    content_type: str,
    slug: str,
    db=Depends(get_db),
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    try:
        sha = store.delete(content_type, slug, user.display_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    audit = AuditLog(
        user_id=user.id,
        action="content.delete",
        target=f"{content_type}/{slug}",
        commit_sha=sha,
    )
    db.add(audit)
    await db.commit()
    return {"ok": True, "commit_sha": sha}


@router.post("/preview-diff")
async def preview_diff(
    req: DiffPreview,
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    meta = req.metadata or {}
    if req.title:
        meta["title"] = req.title
    diff = store.diff_preview(req.content_type, req.slug, meta or None, req.body)
    return {"diff": diff}


@router.get("/history/{content_type}")
async def content_history(
    content_type: str,
    limit: int = 50,
    store: ContentStore = Depends(get_store),
):
    return store.get_history(content_type=content_type, limit=limit)


@router.get("/history/{content_type}/{slug}")
async def content_item_history(
    content_type: str,
    slug: str,
    limit: int = 50,
    store: ContentStore = Depends(get_store),
):
    return store.get_history(content_type=content_type, slug=slug, limit=limit)


@router.get("/commit/{commit_sha}")
async def commit_diff(commit_sha: str, store: ContentStore = Depends(get_store)):
    try:
        diff = store.get_commit_diff(commit_sha)
        return {"sha": commit_sha, "diff": diff}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/revert")
async def revert_commit(
    req: RevertRequest,
    db=Depends(get_db),
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    try:
        new_sha = store.revert(req.commit_sha, user.display_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit = AuditLog(
        user_id=user.id,
        action="content.revert",
        target=req.commit_sha,
        commit_sha=new_sha,
        detail=f"Reverted commit {req.commit_sha[:8]}",
    )
    db.add(audit)
    await db.commit()
    return {"ok": True, "new_commit_sha": new_sha}
