"""LLM tool definitions for content operations."""

import re
from datetime import datetime, timezone
from typing import Any

from convocation.content.store import ContentStore

# Tool definitions in OpenAI function-calling format (also works with Anthropic tool_use)
TOOL_DEFINITIONS = [
    {
        "name": "create_announcement",
        "description": "Create a new announcement post. Use this when someone wants to post news, updates, or notifications to the community.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The announcement title"},
                "body": {"type": "string", "description": "The announcement body in markdown"},
                "pinned": {"type": "boolean", "description": "Whether to pin this announcement", "default": False},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "edit_announcement",
        "description": "Edit an existing announcement. Use this to update the title or body of a previously posted announcement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The announcement slug (URL-safe identifier)"},
                "title": {"type": "string", "description": "New title (optional)"},
                "body": {"type": "string", "description": "New body in markdown (optional)"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "delete_announcement",
        "description": "Delete an announcement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The announcement slug to delete"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "list_announcements",
        "description": "List all current announcements. Use this to see what's already posted.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_event",
        "description": "Create a new event with a date/time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "body": {"type": "string", "description": "Event description in markdown"},
                "event_date": {"type": "string", "description": "Event date/time in ISO format (e.g. 2026-03-25T20:00:00)"},
                "location": {"type": "string", "description": "Event location or platform"},
            },
            "required": ["title", "body", "event_date"],
        },
    },
    {
        "name": "edit_event",
        "description": "Edit an existing event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The event slug"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "event_date": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "delete_event",
        "description": "Delete an event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The event slug to delete"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "create_page",
        "description": "Create a new static page (e.g. About, Rules, FAQ).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Page title"},
                "slug": {"type": "string", "description": "URL slug for the page"},
                "body": {"type": "string", "description": "Page content in markdown"},
                "nav_order": {"type": "integer", "description": "Order in navigation menu", "default": 99},
            },
            "required": ["title", "slug", "body"],
        },
    },
    {
        "name": "edit_page",
        "description": "Edit an existing page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The page slug"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "nav_order": {"type": "integer"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "add_member",
        "description": "Add a member to the roster.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Member's display name"},
                "role": {"type": "string", "description": "Member's role in the organization"},
                "bio": {"type": "string", "description": "Short bio"},
                "joined_date": {"type": "string", "description": "Date joined (ISO format)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "remove_member",
        "description": "Remove a member from the roster.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "The member slug to remove"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "list_content",
        "description": "List all content of a given type. Use this to see what exists before making changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content_type": {
                    "type": "string",
                    "enum": ["announcements", "pages", "events", "members"],
                    "description": "Type of content to list",
                },
            },
            "required": ["content_type"],
        },
    },
]


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:80]


def execute_tool(tool_name: str, args: dict[str, Any], store: ContentStore, author: str) -> dict[str, Any]:
    """Execute a tool call and return the result. Does NOT commit — returns a pending change for diff review."""

    if tool_name == "create_announcement":
        slug = slugify(args["title"])
        meta = {"title": args["title"], "pinned": args.get("pinned", False)}
        return {
            "action": "create",
            "content_type": "announcements",
            "slug": slug,
            "metadata": meta,
            "body": args["body"],
            "preview": f"New announcement: {args['title']}",
        }

    elif tool_name == "edit_announcement":
        item = store.get("announcements", args["slug"])
        if not item:
            return {"error": f"Announcement '{args['slug']}' not found"}
        meta = {}
        if "title" in args and args["title"]:
            meta["title"] = args["title"]
        return {
            "action": "update",
            "content_type": "announcements",
            "slug": args["slug"],
            "metadata": meta or None,
            "body": args.get("body"),
            "preview": f"Edit announcement: {args['slug']}",
        }

    elif tool_name == "delete_announcement":
        return {
            "action": "delete",
            "content_type": "announcements",
            "slug": args["slug"],
            "preview": f"Delete announcement: {args['slug']}",
        }

    elif tool_name in ("list_announcements", "list_content"):
        ct = args.get("content_type", "announcements")
        items = store.list_content(ct)
        summaries = [
            f"- **{i['metadata'].get('title', i['slug'])}** (`{i['slug']}`) — {i['body'][:100]}..."
            for i in items
        ]
        return {
            "action": "read",
            "result": f"Found {len(items)} {ct}:\n" + "\n".join(summaries) if items else f"No {ct} found.",
        }

    elif tool_name == "create_event":
        slug = slugify(args["title"])
        meta = {
            "title": args["title"],
            "event_date": args["event_date"],
            "location": args.get("location", ""),
        }
        return {
            "action": "create",
            "content_type": "events",
            "slug": slug,
            "metadata": meta,
            "body": args["body"],
            "preview": f"New event: {args['title']} on {args['event_date']}",
        }

    elif tool_name == "edit_event":
        meta = {}
        for key in ("title", "event_date", "location"):
            if key in args and args[key]:
                meta[key] = args[key]
        return {
            "action": "update",
            "content_type": "events",
            "slug": args["slug"],
            "metadata": meta or None,
            "body": args.get("body"),
            "preview": f"Edit event: {args['slug']}",
        }

    elif tool_name == "delete_event":
        return {
            "action": "delete",
            "content_type": "events",
            "slug": args["slug"],
            "preview": f"Delete event: {args['slug']}",
        }

    elif tool_name == "create_page":
        meta = {
            "title": args["title"],
            "nav_order": args.get("nav_order", 99),
        }
        return {
            "action": "create",
            "content_type": "pages",
            "slug": args["slug"],
            "metadata": meta,
            "body": args["body"],
            "preview": f"New page: {args['title']}",
        }

    elif tool_name == "edit_page":
        meta = {}
        if "title" in args and args["title"]:
            meta["title"] = args["title"]
        if "nav_order" in args:
            meta["nav_order"] = args["nav_order"]
        return {
            "action": "update",
            "content_type": "pages",
            "slug": args["slug"],
            "metadata": meta or None,
            "body": args.get("body"),
            "preview": f"Edit page: {args['slug']}",
        }

    elif tool_name == "add_member":
        slug = slugify(args["name"])
        meta = {
            "title": args["name"],
            "member_role": args.get("role", "Member"),
            "joined_date": args.get("joined_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        }
        return {
            "action": "create",
            "content_type": "members",
            "slug": slug,
            "metadata": meta,
            "body": args.get("bio", ""),
            "preview": f"Add member: {args['name']}",
        }

    elif tool_name == "remove_member":
        return {
            "action": "delete",
            "content_type": "members",
            "slug": args["slug"],
            "preview": f"Remove member: {args['slug']}",
        }

    return {"error": f"Unknown tool: {tool_name}"}
