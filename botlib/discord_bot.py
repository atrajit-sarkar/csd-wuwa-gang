from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque
import re

import discord
from discord import app_commands

from .config import BotConfig
from .env_store import add_api_keys
from .keyring import KeyRing
from .ollama_client import chat_with_key_rotation
from .persona import load_character_persona, make_system_prompt


@dataclass
class BotRuntime:
    channel_history: dict[int, Deque[dict[str, str]]]


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


async def run_character_bot(*, bot_name: str, character_name: str, token_env: str = "BOT_TOKEN") -> None:
    cfg = BotConfig  # for type checkers

    from .config import load_config

    config = load_config(bot_name=bot_name, token_env=token_env)
    keyring = KeyRing.from_env(config.env_path)

    characters_md_path = Path(__file__).resolve().parents[1] / "characters.md"
    character_block = load_character_persona(characters_md_path, character_name=character_name)
    system_prompt = make_system_prompt(character_block=character_block)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    runtime = BotRuntime(channel_history=defaultdict(lambda: deque(maxlen=30)))

    async def ollama_chat(messages: list[dict[str, str]]) -> str:
        # Reload keys each call so `/add_more_energy` takes effect immediately.
        nonlocal keyring
        keyring = KeyRing.from_env(config.env_path)
        if keyring.is_empty():
            raise RuntimeError("No Ollama API keys configured")

        resp = await chat_with_key_rotation(
            api_url=config.ollama_api_url,
            model=config.ollama_model,
            messages=messages,
            api_keys=keyring.iter_keys(),
        )
        return resp.content

    @tree.command(name="add_more_energy", description="Add Ollama API keys (comma separated)")
    @app_commands.describe(keys="Comma separated API keys")
    async def add_more_energy(interaction: discord.Interaction, keys: str):
        # Only allow in the ENERGY channel.
        if interaction.channel_id != config.energy_channel_id:
            await interaction.response.send_message(
                "This command can only be used in the configured energy channel.",
                ephemeral=True,
            )
            return

        new_keys = [k.strip() for k in keys.split(",") if k.strip()]
        if not new_keys:
            await interaction.response.send_message("No keys provided.", ephemeral=True)
            return

        merged = add_api_keys(config.env_path, new_keys=new_keys)
        await interaction.response.send_message(
            f"Stored {len(new_keys)} key(s). Total keys now: {len(merged)}.",
            ephemeral=True,
        )

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

        # Update rolling context (as user).
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
                messages: list[dict[str, str]] = [
                    {"role": "system", "content": system_prompt},
                ]
                messages.extend(list(history)[-12:])
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
