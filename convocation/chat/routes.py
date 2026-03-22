"""Chat routes — the core admin interface for AI-driven content management."""

import base64
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from convocation.auth.deps import require_officer, require_owner
from convocation.auth.models import AuditLog, Conversation, Role, User
from convocation.chat.llm import chat_with_llm
from convocation.chat.tools import QUICK_TOOL_NAMES, execute_tool
from convocation.content.store import ContentStore
from convocation.content.renderer import render_site
from convocation.db import get_db
from convocation.discord.webhook import notify_content_change
from convocation.notifications.push import send_push_notification

router = APIRouter(prefix="/api/chat", tags=["chat"])


MAX_CONVERSATION_MESSAGES = 30
LOCK_TIMEOUT_SECONDS = 300  # 5 minutes of inactivity

# In-memory edit lock: {"user_id": str, "display_name": str, "acquired_at": float, "last_active": float}
_edit_lock: dict[str, Any] | None = None


def _check_lock_expired() -> None:
    """Clear the lock if it has expired."""
    global _edit_lock
    if _edit_lock and time.time() - _edit_lock["last_active"] > LOCK_TIMEOUT_SECONDS:
        _edit_lock = None


def _acquire_lock(user: User) -> None:
    """Acquire or refresh the edit lock for a user."""
    global _edit_lock
    _check_lock_expired()
    if _edit_lock and _edit_lock["user_id"] != user.id:
        raise HTTPException(
            status_code=423,
            detail=f"{_edit_lock['display_name']} is currently editing. Ask an owner to release their session if needed.",
        )
    _edit_lock = {
        "user_id": user.id,
        "display_name": user.display_name,
        "acquired_at": _edit_lock["acquired_at"] if _edit_lock else time.time(),
        "last_active": time.time(),
    }


class ChatMessage(BaseModel):
    message: str | list[dict[str, Any]]  # string or multimodal content blocks
    conversation_id: str | None = None
    mode: str = "quick"


class ApproveChange(BaseModel):
    change: dict[str, Any]
    conversation_id: str | None = None


UPLOAD_DIR = Path(__file__).parent.parent / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_store() -> ContentStore:
    return ContentStore()


@router.post("/upload-image")
async def upload_image(
    file: UploadFile,
    user: User = Depends(require_officer),
):
    """Upload an image for use in chat messages."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are accepted")

    ext = file.filename.rsplit(".", 1)[-1] if "." in (file.filename or "") else "png"
    name = f"{uuid.uuid4().hex[:12]}.{ext}"
    path = UPLOAD_DIR / name

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image must be under 10MB")

    path.write_bytes(content)
    b64 = base64.b64encode(content).decode()

    return {
        "url": f"/static/uploads/{name}",
        "base64": b64,
        "media_type": file.content_type,
    }


async def _get_or_create_conversation(
    db: AsyncSession, user: User, conversation_id: str | None, mode: str,
) -> Conversation:
    """Load existing conversation or create a new one."""
    if conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user.id
            )
        )
        conv = result.scalar_one_or_none()
        if conv:
            return conv

    conv = Conversation(user_id=user.id, mode=mode)
    db.add(conv)
    await db.flush()
    return conv


@router.post("/send")
async def send_message(
    req: ChatMessage,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    """Send a message to the AI assistant. Returns response and any pending changes."""

    mode = req.mode if req.mode in ("quick", "super") else "quick"
    if mode == "super" and user.role != Role.owner:
        raise HTTPException(status_code=403, detail="Super mode is restricted to site owners")

    _acquire_lock(user)

    conv = await _get_or_create_conversation(db, user, req.conversation_id, mode)
    prev_messages = json.loads(conv.messages)

    # Auto-close if conversation is too large
    if len(prev_messages) >= MAX_CONVERSATION_MESSAGES:
        conv.is_active = False
        await db.commit()
        conv = Conversation(user_id=user.id, mode=mode)
        db.add(conv)
        await db.flush()
        prev_messages = []

    messages = prev_messages + [{"role": "user", "content": req.message}]
    response = await chat_with_llm(messages, mode=mode)

    # Process any tool calls
    pending_changes = []
    tool_results = []

    if response.get("tool_calls"):
        for tc in response["tool_calls"]:
            if mode == "quick" and tc["name"] not in QUICK_TOOL_NAMES:
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "Error: This tool is not available in Quick mode. Switch to Super mode to make structural changes.",
                })
                continue
            result = execute_tool(tc["name"], tc["arguments"], store, user.display_name)

            if result.get("action") == "read":
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result["result"],
                })
            elif result.get("error"):
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": f"Error: {result['error']}",
                })
            else:
                if result["action"] in ("create", "update"):
                    diff = store.diff_preview(
                        result["content_type"],
                        result["slug"],
                        result.get("metadata"),
                        result.get("body"),
                    )
                    result["diff"] = diff

                pending_changes.append(result)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": f"Pending approval: {result['preview']}",
                })

        if tool_results and not pending_changes:
            follow_up_messages = messages + [
                {"role": "assistant", "content": response.get("content", ""), "tool_calls": response["tool_calls"]},
                *tool_results,
            ]
            response = await chat_with_llm(follow_up_messages, mode=mode)

    # Save conversation state
    updated_messages = messages + [
        {"role": "assistant", "content": response.get("content", ""), "tool_calls": response.get("tool_calls", [])},
        *tool_results,
    ]
    conv.messages = json.dumps(updated_messages)

    # Auto-title from first user message
    if conv.title == "New conversation":
        if isinstance(req.message, str):
            conv.title = req.message[:100].strip() or "New conversation"
        elif isinstance(req.message, list):
            text_parts = [b["text"] for b in req.message if b.get("type") == "text"]
            conv.title = (text_parts[0][:100].strip() if text_parts else "Image conversation")

    await db.commit()

    return {
        "response": response.get("content", ""),
        "pending_changes": pending_changes,
        "conversation_id": conv.id,
    }


@router.post("/approve")
async def approve_change(
    req: ApproveChange,
    db=Depends(get_db),
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    """Approve a pending change and commit it."""

    change = req.change
    action = change.get("action")
    content_type = change.get("content_type")
    slug = change.get("slug")

    try:
        if action == "create":
            sha = store.create(content_type, slug, change.get("metadata", {}), change.get("body", ""), user.display_name)
        elif action == "update":
            sha = store.update(content_type, slug, change.get("metadata"), change.get("body"), user.display_name)
        elif action == "delete":
            sha = store.delete(content_type, slug, user.display_name)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Audit log
    audit = AuditLog(
        user_id=user.id,
        action=f"content.{action}",
        target=f"{content_type}/{slug}",
        commit_sha=sha,
        detail=change.get("preview", ""),
    )
    db.add(audit)
    await db.commit()

    # Regenerate static site
    try:
        render_site(store)
    except Exception:
        pass  # Don't fail the approval if rendering fails

    # Discord notification
    try:
        await notify_content_change(action, content_type, slug, change.get("preview", ""), user.display_name)
    except Exception:
        pass

    # Push notification for announcements and events
    if content_type in ("announcements", "events") and action == "create":
        try:
            title = change.get("metadata", {}).get("title", slug)
            await send_push_notification(
                title=f"New {content_type[:-1]}: {title}",
                body=change.get("body", "")[:200],
                db_session=db,
            )
        except Exception:
            pass

    return {
        "ok": True,
        "commit_sha": sha,
        "message": f"Change approved and committed: {change.get('preview', '')}",
    }


@router.post("/reject")
async def reject_change(
    req: ApproveChange,
    user: User = Depends(require_officer),
):
    """Reject a pending change. Nothing is committed."""
    return {
        "ok": True,
        "message": f"Change rejected: {req.change.get('preview', '')}",
    }


@router.get("/conversations")
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_officer),
):
    """List user's conversations, most recent first."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    convos = result.scalars().all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "mode": c.mode,
            "is_active": c.is_active,
            "message_count": len(json.loads(c.messages)),
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in convos
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_officer),
):
    """Get a conversation's messages."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user.id
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "id": conv.id,
        "title": conv.title,
        "mode": conv.mode,
        "is_active": conv.is_active,
        "messages": json.loads(conv.messages),
    }


@router.get("/edit-lock")
async def get_edit_lock(user: User = Depends(require_officer)):
    """Check who holds the edit lock."""
    _check_lock_expired()
    if not _edit_lock:
        return {"locked": False}
    return {
        "locked": True,
        "user_id": _edit_lock["user_id"],
        "display_name": _edit_lock["display_name"],
        "is_me": _edit_lock["user_id"] == user.id,
        "seconds_held": int(time.time() - _edit_lock["acquired_at"]),
    }


@router.post("/edit-lock/release")
async def release_edit_lock(user: User = Depends(require_officer)):
    """Release your own edit lock."""
    global _edit_lock
    _check_lock_expired()
    if _edit_lock and _edit_lock["user_id"] == user.id:
        _edit_lock = None
        return {"ok": True}
    return {"ok": False, "detail": "You don't hold the lock"}


@router.post("/edit-lock/kill")
async def kill_edit_lock(user: User = Depends(require_owner)):
    """Force-release another user's edit lock. Owner only."""
    global _edit_lock
    _check_lock_expired()
    if not _edit_lock:
        return {"ok": True, "detail": "No active lock"}
    killed_user = _edit_lock["display_name"]
    _edit_lock = None
    return {"ok": True, "detail": f"Released {killed_user}'s edit session"}


@router.get("/llm-status")
async def llm_status(user: User = Depends(require_officer)):
    """Get current LLM server status from vLLM metrics."""
    from convocation.config import settings

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.llm_base_url.rstrip('/v1')}/metrics")
            text = resp.text

        def get_gauge(name: str) -> float:
            m = re.search(rf'^{re.escape(name)}\{{[^}}]*\}}\s+(\S+)', text, re.MULTILINE)
            return float(m.group(1)) if m else 0.0

        running = int(get_gauge("vllm:num_requests_running"))
        waiting = int(get_gauge("vllm:num_requests_waiting"))

        return {
            "online": True,
            "running": running,
            "waiting": waiting,
            "model": settings.llm_model.split("/")[-1],
        }
    except Exception:
        return {"online": False, "running": 0, "waiting": 0, "model": ""}
