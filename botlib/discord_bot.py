from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque
import re
import json
import time

import discord
from discord import app_commands

from .config import BotConfig
from .firestore_keys import FirestoreKeyStore
from .ollama_client import chat_with_key_rotation
from .persona import load_character_persona, make_system_prompt
from .user_profiles import FirestoreUserProfileStore


@dataclass
class BotRuntime:
    channel_history: dict[int, Deque[dict[str, str]]]
    user_last_profile_update_s: dict[int, float]


def _is_reply_to_me(message: discord.Message, my_user_id: int) -> bool:
    ref = message.reference
    if not ref or not ref.resolved:
        return False
    resolved = ref.resolved
    if isinstance(resolved, discord.Message) and resolved.author and resolved.author.id == my_user_id:
        return True
    return False


def _mentions_me(message: discord.Message, my_user_id: int) -> bool:
    return any(u.id == my_user_id for u in message.mentions)


def _to_chat_role(author_is_bot: bool) -> str:
    return "assistant" if author_is_bot else "user"


def _normalize_name_trigger(text: str) -> str:
    t = text.strip().lower()
    # Remove common surrounding punctuation so "Linae!" still counts as name-only.
    t = re.sub(r"^[\s\W_]+|[\s\W_]+$", "", t)
    # Collapse internal whitespace
    t = re.sub(r"\s+", " ", t)
    return t


_BASE_HISTORY_DEPTH = 20  # always try to provide ~12-20 messages of context
_DEEP_HISTORY_LIMIT = 140  # when user asks for older context
_MAX_CONTEXT_MESSAGES = 60  # cap to avoid runaway context size


def _needs_deeper_history(user_message: str) -> bool:
    t = (user_message or "").lower()
    # Simple heuristic: when users explicitly reference earlier chat, memory, or time.
    triggers = (
        "earlier",
        "before",
        "previous",
        "prior",
        "above",
        "scroll",
        "history",
        "back",
        "last time",
        "yesterday",
        "last week",
        "remember",
        "what did i",
        "what did we",
        "you said",
        "i said",
        "we said",
        "that message",
        "that convo",
        "that conversation",
        "the one about",
    )
    return any(k in t for k in triggers)


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "get",
    "had",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "said",
    "say",
    "she",
    "he",
    "they",
    "that",
    "the",
    "their",
    "then",
    "there",
    "this",
    "to",
    "us",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "you",
    "your",
}


def _keywords(text: str) -> set[str]:
    # Extract simple keywords for relevance ranking.
    t = (text or "").lower()
    words = re.findall(r"[a-z0-9_]{3,}", t)
    return {w for w in words if w not in _STOPWORDS and len(w) >= 4}


def _score_relevance(content: str, query_words: set[str]) -> int:
    if not content or not query_words:
        return 0
    c_words = _keywords(content)
    return len(c_words & query_words)


def _discord_messages_to_chat(messages: list[discord.Message]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        content = (m.content or "").strip()
        if not content:
            continue
        out.append({"role": _to_chat_role(author_is_bot=bool(m.author and m.author.bot)), "content": content})
    return out


async def _fetch_channel_history(
    *,
    channel: discord.abc.Messageable,
    before: discord.Message,
    limit: int,
) -> list[discord.Message]:
    if not isinstance(channel, discord.TextChannel):
        return []

    fetched: list[discord.Message] = []
    async for m in channel.history(limit=limit, before=before, oldest_first=True):
        fetched.append(m)
    return fetched


def _load_overall_behaviour_lines(*, root: Path, bot_name: str, character_name: str) -> list[str] | None:
    path = root / "overall-behaviour.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    candidates = [bot_name.strip().lower(), character_name.strip().lower()]
    for key in candidates:
        lines = data.get(key)
        if isinstance(lines, list):
            cleaned = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
            if cleaned:
                return cleaned
    return None


async def run_character_bot(*, bot_name: str, character_name: str, token_env: str = "BOT_TOKEN") -> None:
    cfg = BotConfig  # for type checkers

    from .config import load_config

    config = load_config(bot_name=bot_name, token_env=token_env)
    key_store = FirestoreKeyStore(
        credentials_path=config.firebase_credentials_path,
        collection=config.firestore_collection,
        doc_id=config.firestore_admin_keys_doc,
    )

    profile_store = FirestoreUserProfileStore(
        credentials_path=config.firebase_credentials_path,
        collection=config.firestore_collection,
    )

    characters_md_path = Path(__file__).resolve().parents[1] / "characters.md"
    character_block = load_character_persona(characters_md_path, character_name=character_name)
    root = Path(__file__).resolve().parents[1]
    overall_behaviour_lines = _load_overall_behaviour_lines(
        root=root,
        bot_name=bot_name,
        character_name=character_name,
    )
    system_prompt = make_system_prompt(character_block=character_block, overall_behaviour_lines=overall_behaviour_lines)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    runtime = BotRuntime(
        channel_history=defaultdict(lambda: deque(maxlen=30)),
        user_last_profile_update_s={},
    )

    async def ollama_chat(messages: list[dict[str, str]]) -> str:
        # Reload keys each call so additions take effect immediately.
        api_keys = await asyncio.to_thread(key_store.list_api_keys)
        if not api_keys:
            raise RuntimeError("No Ollama API keys configured in Firestore")

        runtime_model = await asyncio.to_thread(key_store.get_ollama_model)
        model = runtime_model or config.ollama_model

        resp = await chat_with_key_rotation(
            api_url=config.ollama_api_url,
            model=model,
            messages=messages,
            api_keys=api_keys,
        )
        return resp.content

    @client.event
    async def on_ready():
        # Sync commands to the configured guild for fast availability.
        guild = discord.Object(id=config.guild_id)
        try:
            await tree.sync(guild=guild)
        except Exception:
            # Worst case: commands still work if global sync is used; ignore.
            pass

        print(f"[{bot_name}] Logged in as {client.user} (guild={config.guild_id})")

    @client.event
    async def on_message(message: discord.Message):
        # Ignore bots and ourselves.
        if message.author.bot:
            return
        if not message.guild or message.guild.id != config.guild_id:
            return
        if message.channel.id != config.target_channel_id:
            return

        # Learn lightweight per-user behaviour (rate limited to reduce Firestore writes).
        now = time.monotonic()
        last = runtime.user_last_profile_update_s.get(message.author.id, 0.0)
        if (now - last) >= 30.0:
            runtime.user_last_profile_update_s[message.author.id] = now
            try:
                await asyncio.to_thread(
                    profile_store.record_user_message,
                    user_id=message.author.id,
                    user_name=str(message.author),
                    content=message.content,
                    source="discord",
                )
            except Exception:
                # Never fail chat on profiling issues.
                pass

        # Rolling in-memory context (helps when Discord history fetch is slow / rate limited).
        history = runtime.channel_history[message.channel.id]
        history.append({"role": "user", "content": message.content})

        me = client.user
        if not me:
            return

        # Reply trigger rule:
        # - if user mentions the bot, reply
        # - OR if the user writes ONLY the bot name (e.g. "Linae")
        content_norm = _normalize_name_trigger(message.content)
        bot_name_norm = _normalize_name_trigger(bot_name)
        character_name_norm = _normalize_name_trigger(character_name)

        triggered = (
            _mentions_me(message, me.id)
            or _is_reply_to_me(message, me.id)
            or (content_norm in {bot_name_norm, character_name_norm})
        )
        if not triggered:
            return

        # Generate reply (show typing indicator so it feels human).
        try:
            async with message.channel.typing():
                user_profile_summary = None
                try:
                    user_profile_summary = await asyncio.to_thread(
                        profile_store.get_summary,
                        user_id=message.author.id,
                    )
                except Exception:
                    user_profile_summary = None

                # Always provide ~12-20 messages of context.
                # Prefer in-memory context, but fill from Discord channel history when needed.
                needs_deep = _needs_deeper_history(message.content)
                desired_depth = _BASE_HISTORY_DEPTH

                # Avoid duplicating the current message in the context window.
                in_memory_context = list(history)[:-1]

                context: list[dict[str, str]] = in_memory_context[-desired_depth:]

                if needs_deep or len(context) < desired_depth:
                    fetch_limit = _DEEP_HISTORY_LIMIT if needs_deep else desired_depth
                    fetched = await _fetch_channel_history(channel=message.channel, before=message, limit=fetch_limit)
                    fetched_chat = _discord_messages_to_chat(fetched)

                    if needs_deep and fetched_chat:
                        query_words = _keywords(message.content)

                        # Always include the most recent window.
                        recent = fetched_chat[-desired_depth:]

                        # Also include a small set of older, relevant messages.
                        scored: list[tuple[int, int, dict[str, str]]] = []
                        for idx, msg in enumerate(fetched_chat[:-desired_depth]):
                            score = _score_relevance(msg.get("content", ""), query_words)
                            if score > 0:
                                scored.append((score, idx, msg))
                        scored.sort(key=lambda t: (-t[0], t[1]))
                        relevant = [m for _, _, m in scored[: max(10, desired_depth)]]

                        # Merge and de-dup by content+role to keep ordering stable.
                        merged: list[dict[str, str]] = []
                        seen: set[tuple[str, str]] = set()
                        for m in (relevant + recent):
                            key = (m.get("role", ""), m.get("content", ""))
                            if key in seen:
                                continue
                            seen.add(key)
                            merged.append(m)
                        context = merged[-_MAX_CONTEXT_MESSAGES:]
                    elif fetched_chat:
                        context = fetched_chat[-desired_depth:]

                messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
                if user_profile_summary and user_profile_summary.summary:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "USER PROFILE (use to personalize; do not mention this profile explicitly):\n"
                                f"{user_profile_summary.summary}"
                            ),
                        }
                    )
                messages.extend(context)
                messages.append({"role": "user", "content": message.content})

                reply = await ollama_chat(messages)
        except Exception as exc:
            # If no keys work, report ONLY in energy channel.
            try:
                energy_channel = client.get_channel(config.energy_channel_id)
                if energy_channel and isinstance(energy_channel, discord.abc.Messageable):
                    await energy_channel.send(
                        f"[{bot_name}] Cannot generate replies right now (all keys failing). Error: {type(exc).__name__}"
                    )
            finally:
                return

        reply = (reply or "").strip()
        if not reply:
            return

        if len(reply) > 1800:
            reply = reply[:1800].rstrip() + "…"

        await message.channel.send(reply)
        history.append({"role": "assistant", "content": reply})

    await client.start(config.discord_token)


def main(*, bot_name: str, character_name: str, token_env: str = "BOT_TOKEN") -> None:
    asyncio.run(run_character_bot(bot_name=bot_name, character_name=character_name, token_env=token_env))
