"""Discord integration — incoming webhooks and outgoing notifications."""

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from convocation.config import settings
from convocation.content.store import ContentStore

router = APIRouter(prefix="/api/discord", tags=["discord"])


async def notify_content_change(
    action: str, content_type: str, slug: str, preview: str, author: str
):
    """Send a notification to Discord when content changes."""
    if not settings.discord_webhook_url:
        return

    action_emoji = {"create": "+", "update": "~", "delete": "-", "revert": "<<"}
    emoji = action_emoji.get(action, "?")

    embed = {
        "title": f"[{emoji}] {preview}",
        "description": f"**{action.title()}** `{content_type}/{slug}` by {author}",
        "color": {"create": 0x2ECC71, "update": 0x3498DB, "delete": 0xE74C3C, "revert": 0xF39C12}.get(action, 0x95A5A6),
        "footer": {"text": f"ConvocAItion | {settings.site_title}"},
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            settings.discord_webhook_url,
            json={"embeds": [embed]},
        )


@router.post("/incoming")
async def discord_incoming(request: Request):
    """Handle incoming Discord webhook — allows a bot to trigger site updates.

    Expected payload:
    {
        "command": "announce",
        "title": "...",
        "body": "...",
        "author": "DiscordBot"
    }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    command = payload.get("command")
    if not command:
        raise HTTPException(status_code=400, detail="Missing 'command' field")

    store = ContentStore()
    author = payload.get("author", "DiscordBot")

    if command == "announce":
        title = payload.get("title")
        body = payload.get("body", "")
        if not title:
            raise HTTPException(status_code=400, detail="Missing 'title'")

        from convocation.chat.tools import slugify
        slug = slugify(title)
        meta = {"title": title, "pinned": payload.get("pinned", False)}
        sha = store.create("announcements", slug, meta, body, author)

        # Regenerate site
        from convocation.content.renderer import render_site
        try:
            render_site(store)
        except Exception:
            pass

        return {"ok": True, "slug": slug, "commit_sha": sha}

    elif command == "event":
        title = payload.get("title")
        body = payload.get("body", "")
        event_date = payload.get("event_date")
        if not title or not event_date:
            raise HTTPException(status_code=400, detail="Missing 'title' or 'event_date'")

        from convocation.chat.tools import slugify
        slug = slugify(title)
        meta = {"title": title, "event_date": event_date, "location": payload.get("location", "")}
        sha = store.create("events", slug, meta, body, author)

        from convocation.content.renderer import render_site
        try:
            render_site(store)
        except Exception:
            pass

        return {"ok": True, "slug": slug, "commit_sha": sha}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown command: {command}")
