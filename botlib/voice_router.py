from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class VoiceDecision:
    send_mode: str  # "text" | "voice"
    reason: str


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    blob = text[start : end + 1]
    try:
        obj = json.loads(blob)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _contains_code_or_links(text: str) -> bool:
    t = text or ""
    if "```" in t:
        return True
    if re.search(r"https?://\S+", t, re.IGNORECASE):
        return True
    return False


def _user_explicitly_wants_voice(text: str) -> bool:
    t = (text or "").lower()
    triggers = (
        "voice",
        "say it",
        "say this",
        "read this",
        "read it",
        "speak",
        "talk",
        "send a voice",
        "send voice",
        "voice message",
    )
    return any(k in t for k in triggers)


def user_explicitly_wants_voice(text: str) -> bool:
    """Public wrapper used by the Discord bot for diagnostics."""

    return _user_explicitly_wants_voice(text)


def _user_explicitly_wants_text(text: str) -> bool:
    t = (text or "").lower()
    triggers = (
        "text",
        "type it",
        "write it",
        "no voice",
        "don't use voice",
        "dont use voice",
        "no audio",
    )
    return any(k in t for k in triggers)


def should_allow_voice(
    *,
    enabled: bool,
    voice_id_present: bool,
    reply_text: str,
    max_chars: int,
) -> tuple[bool, str]:
    if not enabled:
        return False, "voice_disabled"
    if not voice_id_present:
        return False, "no_voice_model"

    reply = (reply_text or "").strip()
    if not reply:
        return False, "empty_reply"
    if len(reply) > max_chars:
        return False, "too_long"
    if _contains_code_or_links(reply):
        return False, "code_or_link"

    return True, "ok"


async def decide_voice_vs_text(
    *,
    ollama_chat,  # async callable(messages)->str
    system_prompt: str,
    character_name: str,
    user_message: str,
    reply_text: str,
    cooldown_remaining_s: float,
    fun_probability: float,
    deterministic_seed: Optional[int] = None,
) -> VoiceDecision:
    """Ask the model whether to send the reply as voice or text.

    This is *only* the routing decision. Hard safety/guardrails should be enforced outside.
    """

    if _user_explicitly_wants_text(user_message):
        return VoiceDecision(send_mode="text", reason="user_requested_text")

    # If user asked for voice, let the model pick voice/text based on appropriateness;
    # but we'll strongly prefer voice by giving the model this context.
    user_wants_voice = _user_explicitly_wants_voice(user_message)

    # If the user explicitly asked for voice, do it deterministically.
    # Guardrails (enabled/keys/max length) are enforced by the caller.
    if user_wants_voice:
        return VoiceDecision(send_mode="voice", reason="user_requested_voice")

    if cooldown_remaining_s > 0:
        return VoiceDecision(send_mode="text", reason="cooldown")

    seed = deterministic_seed
    if seed is None:
        seed = int(time.time() * 1000) & 0xFFFFFFFF
    rng = random.Random(seed)

    # We include a random number so the model can choose voice "for fun" sometimes.
    roll = rng.random()

    router_system = (
        "You are a message delivery router for a Discord character bot. "
        "You will be given the character profile and a draft reply. "
        "Choose whether the bot should send the reply as TEXT or as a short VOICE message (audio attachment). "
        "VOICE should be chosen rarely and only when it makes the chat feel warmer, more human, or playful. "
        "Prefer TEXT when the reply is informational, complex, contains steps, code, links, or needs to be easily skimmable. "
        "If the user explicitly asks for voice (e.g., 'say it', 'voice', 'read this'), prefer VOICE. "
        "If the user explicitly asks for text, prefer TEXT. "
        "Return ONLY valid JSON in this schema: {\"send_mode\": \"text\"|\"voice\", \"reason\": \"short_reason\"}."
    )

    # The model gets the full persona prompt (for warmth/style cues) but must output JSON only.
    messages: list[dict[str, str]] = [
        {"role": "system", "content": router_system},
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"Character: {character_name}\n"
                f"User message: {user_message}\n"
                f"Draft reply: {reply_text}\n"
                f"Random roll: {roll:.4f} (choose voice only if roll < {fun_probability:.4f} and it fits)\n"
                f"User explicitly requested voice: {str(user_wants_voice).lower()}\n"
                "Decide send_mode now."
            ),
        },
    ]

    raw = await ollama_chat(messages)
    parsed = _extract_json_object(raw) or {}

    mode = parsed.get("send_mode")
    reason = parsed.get("reason")

    if mode not in {"text", "voice"}:
        mode = "text"
    if not isinstance(reason, str) or not reason.strip():
        reason = "model_default"

    # Enforce rarity unless user asked for voice.
    if mode == "voice" and not user_wants_voice and roll >= fun_probability:
        return VoiceDecision(send_mode="text", reason="fun_probability_gate")

    return VoiceDecision(send_mode=mode, reason=reason.strip())
