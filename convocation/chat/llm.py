"""LLM client — supports Anthropic (default) and OpenAI-compatible endpoints."""

import json
from typing import Any

import httpx

from convocation.chat.tools import TOOL_DEFINITIONS
from convocation.config import settings

SYSTEM_PROMPT = """You are the site manager for "{site_title}", powered by ConvocAItion.

YOUR ROLE: You are a professional, efficient assistant that helps community administrators manage their website. You handle announcements, events, pages, and member rosters through the tools provided to you.

CRITICAL RULES:
1. When an admin asks you to create, edit, or delete content — USE THE APPROPRIATE TOOL IMMEDIATELY. Do not just describe what you would do. Call the tool.
2. For read/list requests, use the list_content or list_announcements tool to show current content.
3. Be concise. Respond in 1-3 sentences maximum unless the admin asks for detail.
4. If a request is ambiguous (missing title, unclear date, etc.), ask ONE clarifying question, then act.
5. Dates should be ISO format (YYYY-MM-DDTHH:MM:SS). If the admin says "Friday at 8pm", convert it to the next upcoming Friday.
6. For announcement/event titles, write them cleanly — capitalize properly, no excessive punctuation.
7. Write announcement and event bodies in clean markdown. Keep them informative but not verbose.

WHAT YOU CAN DO:
- Create, edit, delete announcements (news, updates, notifications)
- Create, edit, delete events (with date, time, location)
- Create, edit pages (About, Rules, FAQ, etc.)
- Add/remove members from the roster
- List any content type to see what currently exists

WHAT YOU CANNOT DO:
- Change site settings, themes, or configuration
- Manage user accounts or permissions
- Access anything outside the content management tools

TODAY'S DATE: Use this as reference for relative dates the admin mentions.

Respond naturally but stay focused on the task. You are a tool operator, not a conversationalist.
"""


async def chat_with_llm(
    messages: list[dict[str, Any]],
    pending_tool_results: list[dict] | None = None,
) -> dict[str, Any]:
    """Send messages to the LLM and get a response, potentially with tool calls."""

    system = SYSTEM_PROMPT.format(site_title=settings.site_title)

    if settings.llm_provider == "anthropic":
        return await _anthropic_chat(system, messages, pending_tool_results)
    else:
        return await _openai_chat(system, messages, pending_tool_results)


async def _anthropic_chat(
    system: str,
    messages: list[dict[str, Any]],
    pending_tool_results: list[dict] | None = None,
) -> dict[str, Any]:
    """Call Anthropic Messages API with tool use."""

    # Convert tool definitions to Anthropic format
    tools = []
    for td in TOOL_DEFINITIONS:
        tools.append({
            "name": td["name"],
            "description": td["description"],
            "input_schema": td["input_schema"],
        })

    # Build request
    api_messages = []
    for msg in messages:
        if msg["role"] == "user":
            api_messages.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "assistant":
            if "tool_calls" in msg:
                # Convert tool calls to Anthropic content blocks
                content = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["arguments"],
                    })
                api_messages.append({"role": "assistant", "content": content})
            else:
                api_messages.append({"role": "assistant", "content": msg["content"]})
        elif msg["role"] == "tool":
            api_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg["content"],
                }],
            })

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.llm_base_url}/v1/messages" if "/v1" not in settings.llm_base_url else f"{settings.llm_base_url}/messages",
            headers={
                "x-api-key": settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "max_tokens": 4096,
                "system": system,
                "tools": tools,
                "messages": api_messages,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # Parse response
    result: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": []}

    for block in data.get("content", []):
        if block["type"] == "text":
            result["content"] += block["text"]
        elif block["type"] == "tool_use":
            result["tool_calls"].append({
                "id": block["id"],
                "name": block["name"],
                "arguments": block["input"],
            })

    result["stop_reason"] = data.get("stop_reason", "end_turn")
    return result


async def _openai_chat(
    system: str,
    messages: list[dict[str, Any]],
    pending_tool_results: list[dict] | None = None,
) -> dict[str, Any]:
    """Call OpenAI-compatible API with function calling."""

    # Convert tool definitions to OpenAI format
    tools = []
    for td in TOOL_DEFINITIONS:
        tools.append({
            "type": "function",
            "function": {
                "name": td["name"],
                "description": td["description"],
                "parameters": td["input_schema"],
            },
        })

    api_messages = [{"role": "system", "content": system}]
    for msg in messages:
        if msg["role"] in ("user", "assistant"):
            api_msg = {"role": msg["role"], "content": msg.get("content", "")}
            if msg.get("tool_calls"):
                api_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                    }
                    for tc in msg["tool_calls"]
                ]
            api_messages.append(api_msg)
        elif msg["role"] == "tool":
            api_messages.append({
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": msg["content"],
            })

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llm_model,
                "messages": api_messages,
                "tools": tools,
                "max_tokens": 4096,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    choice = data["choices"][0]
    msg = choice["message"]

    result: dict[str, Any] = {
        "role": "assistant",
        "content": msg.get("content", "") or "",
        "tool_calls": [],
    }

    if msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            result["tool_calls"].append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"],
            })

    result["stop_reason"] = choice.get("finish_reason", "stop")
    return result
