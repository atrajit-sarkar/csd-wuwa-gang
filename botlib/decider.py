from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Decision:
    should_reply: bool
    reply: str


def _looks_low_signal(content: str) -> bool:
    c = content.strip()
    if not c:
        return True
    if len(c) < 3:
        return True
    # Only emojis / punctuation / whitespace
    if re.fullmatch(r"[\W_]+", c):
        return True
    return False


def _extract_json_object(text: str) -> Optional[dict]:
    # Extract first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    blob = text[start : end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


async def decide_and_generate(
    *,
    ollama_chat,  # async callable(messages)->str
    system_prompt: str,
    channel_context: list[dict[str, str]],
    user_message: str,
    force_reply: bool,
) -> Decision:
    if not force_reply and _looks_low_signal(user_message):
        return Decision(should_reply=False, reply="")

    router_system = (
        "You are a message router for a Discord bot. "
        "Decide whether the assistant should reply to the USER's message. "
        "Only reply if it would be helpful or the user is addressing the bot. "
        "If replying, generate the reply IN CHARACTER using the character profile. "
        "The reply should feel human and natural (casual Discord tone), and avoid stiff assistant phrasing. "
        "Return ONLY valid JSON in this exact schema:\n"
        "{\"should_reply\": true/false, \"reply\": \"...\"}\n"
        "If should_reply is false, reply must be an empty string."
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": router_system},
        {"role": "system", "content": system_prompt},
    ]

    # Provide a small amount of recent context.
    messages.extend(channel_context[-12:])
    messages.append({"role": "user", "content": user_message})

    raw = await ollama_chat(messages)
    parsed = _extract_json_object(raw) or {}

    should_reply = bool(parsed.get("should_reply", False))
    reply = parsed.get("reply") if isinstance(parsed.get("reply"), str) else ""

    # Safety: trim overly long replies.
    reply = reply.strip()
    if not should_reply:
        return Decision(should_reply=False, reply="")

    if not reply:
        # If model said should_reply but produced nothing, fall back to a short in-character acknowledgement.
        reply = "Got you. Give me a second—what exactly do you want to do next?"

    if len(reply) > 1800:
        reply = reply[:1800].rstrip() + "…"

    return Decision(should_reply=True, reply=reply)
