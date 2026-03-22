"""Chat routes — the core admin interface for AI-driven content management."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from convocation.auth.deps import require_officer
from convocation.auth.models import AuditLog, User
from convocation.chat.llm import chat_with_llm
from convocation.chat.tools import execute_tool
from convocation.content.store import ContentStore
from convocation.content.renderer import render_site
from convocation.db import get_db
from convocation.discord.webhook import notify_content_change
from convocation.notifications.push import send_push_notification

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    message: str
    conversation: list[dict[str, Any]] = []


class ApproveChange(BaseModel):
    change: dict[str, Any]
    conversation: list[dict[str, Any]] = []


def get_store() -> ContentStore:
    return ContentStore()


@router.post("/send")
async def send_message(
    req: ChatMessage,
    user: User = Depends(require_officer),
    store: ContentStore = Depends(get_store),
):
    """Send a message to the AI assistant. Returns response and any pending changes."""

    messages = req.conversation + [{"role": "user", "content": req.message}]
    response = await chat_with_llm(messages)

    # Process any tool calls
    pending_changes = []
    tool_results = []

    if response.get("tool_calls"):
        for tc in response["tool_calls"]:
            result = execute_tool(tc["name"], tc["arguments"], store, user.display_name)

            if result.get("action") == "read":
                # Read-only operations — feed result back to LLM
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
                # Mutating operations — generate diff and require approval
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

        # If we had read-only results, get a follow-up from the LLM
        if tool_results and not pending_changes:
            follow_up_messages = messages + [
                {"role": "assistant", "content": response.get("content", ""), "tool_calls": response["tool_calls"]},
                *tool_results,
            ]
            response = await chat_with_llm(follow_up_messages)

    return {
        "response": response.get("content", ""),
        "pending_changes": pending_changes,
        "conversation": messages + [
            {"role": "assistant", "content": response.get("content", ""), "tool_calls": response.get("tool_calls", [])},
            *tool_results,
        ],
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
