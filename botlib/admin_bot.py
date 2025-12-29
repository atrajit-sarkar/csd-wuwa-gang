from __future__ import annotations

import asyncio

import discord
from discord import app_commands

from .config import load_config
from .firestore_keys import FirestoreKeyStore
from .channel_memory import FirestoreChannelMemoryStore


def _parse_keys_arg(text: str) -> list[str]:
    # Accept comma-separated keys, ignoring empties.
    return [k.strip() for k in text.split(",") if k and k.strip()]


async def run_admin_bot(*, bot_name: str = "Admin", token_env: str = "ADMIN_BOT_TOKEN") -> None:
    config = load_config(bot_name=bot_name, token_env=token_env)

    key_store = FirestoreKeyStore(
        credentials_path=config.firebase_credentials_path,
        collection=config.firestore_collection,
        doc_id=config.firestore_admin_keys_doc,
    )

    channel_memory_store = FirestoreChannelMemoryStore(
        credentials_path=config.firebase_credentials_path,
        collection=config.firestore_collection,
    )

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    intents.dm_messages = True

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    guild_obj = discord.Object(id=config.guild_id)

    async def _is_admin(interaction: discord.Interaction) -> bool:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        return bool(member and (member.guild_permissions.administrator or member.guild_permissions.manage_guild))

    # Guild-scoped so it shows up immediately in the server (no global propagation delay).
    @tree.command(
        name="add_more_energy",
        description="Add Ollama API keys to Firestore (comma separated)",
        guild=guild_obj,
    )
    @app_commands.describe(keys="Comma separated API keys")
    async def add_more_energy(interaction: discord.Interaction, keys: str):
        # Only allow in the configured energy channel.
        if interaction.channel_id != config.energy_channel_id:
            await interaction.response.send_message(
                "This command can only be used in the configured energy channel.",
                ephemeral=True,
            )
            return

        if not await _is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        new_keys = _parse_keys_arg(keys)
        if not new_keys:
            await interaction.response.send_message("No keys provided.", ephemeral=True)
            return

        stats = await asyncio.to_thread(
            key_store.add_api_keys,
            new_keys=new_keys,
            added_by_id=interaction.user.id,
            added_by_name=str(interaction.user),
            source="guild",
        )

        await interaction.response.send_message(
            f"Stored {stats.get('added', 0)} key(s) (skipped {stats.get('skipped', 0)} duplicate(s)). Total keys now: {stats.get('total', 0)}.",
            ephemeral=True,
        )

    @tree.command(
        name="set_ollama_model",
        description="Set the Ollama model used by all chatbots",
        guild=guild_obj,
    )
    @app_commands.describe(model="Model name, e.g. llama3.1:70b")
    async def set_ollama_model(interaction: discord.Interaction, model: str):
        # Only allow in the configured energy channel.
        if interaction.channel_id != config.energy_channel_id:
            await interaction.response.send_message(
                "This command can only be used in the configured energy channel.",
                ephemeral=True,
            )
            return

        if not await _is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        cleaned = (model or "").strip()
        if not cleaned:
            await interaction.response.send_message("Model cannot be empty.", ephemeral=True)
            return

        try:
            await asyncio.to_thread(
                key_store.set_ollama_model,
                model=cleaned,
                updated_by_id=interaction.user.id,
                updated_by_name=str(interaction.user),
                source="guild",
            )
        except Exception as exc:
            await interaction.response.send_message(
                f"Failed to set model: {type(exc).__name__}: {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Updated runtime model for all chatbots to: {cleaned}",
            ephemeral=True,
        )

    @tree.command(
        name="show_ollama_model",
        description="Show the Ollama model currently used by chatbots",
        guild=guild_obj,
    )
    async def show_ollama_model(interaction: discord.Interaction):
        # Only allow in the configured energy channel.
        if interaction.channel_id != config.energy_channel_id:
            await interaction.response.send_message(
                "This command can only be used in the configured energy channel.",
                ephemeral=True,
            )
            return

        if not await _is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        runtime_model = await asyncio.to_thread(key_store.get_ollama_model)
        if runtime_model:
            msg = f"Runtime override model is set to: {runtime_model}"
        else:
            msg = f"No runtime override model set. Chatbots will use default from .env: {config.ollama_model}"

        await interaction.response.send_message(msg, ephemeral=True)

    @tree.command(
        name="clear_ollama_model",
        description="Clear the runtime Ollama model override (revert to .env default)",
        guild=guild_obj,
    )
    async def clear_ollama_model(interaction: discord.Interaction):
        # Only allow in the configured energy channel.
        if interaction.channel_id != config.energy_channel_id:
            await interaction.response.send_message(
                "This command can only be used in the configured energy channel.",
                ephemeral=True,
            )
            return

        if not await _is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        try:
            await asyncio.to_thread(
                key_store.clear_ollama_model,
                cleared_by_id=interaction.user.id,
                cleared_by_name=str(interaction.user),
                source="guild",
            )
        except Exception as exc:
            await interaction.response.send_message(
                f"Failed to clear model override: {type(exc).__name__}: {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Cleared runtime model override. Chatbots will now use default from .env: {config.ollama_model}",
            ephemeral=True,
        )

    @tree.command(
        name="clear_channel_memory",
        description="Clear stored channel memory (summary + recent messages) for the configured target channel",
        guild=guild_obj,
    )
    async def clear_channel_memory(interaction: discord.Interaction):
        # Only allow in the configured energy channel.
        if interaction.channel_id != config.energy_channel_id:
            await interaction.response.send_message(
                "This command can only be used in the configured energy channel.",
                ephemeral=True,
            )
            return

        if not await _is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        try:
            await asyncio.to_thread(
                channel_memory_store.clear_memory,
                guild_id=config.guild_id,
                channel_id=config.target_channel_id,
            )
        except Exception as exc:
            await interaction.response.send_message(
                f"Failed to clear channel memory: {type(exc).__name__}: {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Cleared stored channel memory for target channel id: {config.target_channel_id}",
            ephemeral=True,
        )

    @tree.command(name="submit_energy", description="Submit your Ollama API keys via DM (comma separated)")
    @app_commands.describe(keys="Comma separated API keys")
    async def submit_energy(interaction: discord.Interaction, keys: str):
        # This is intended for DMs; in guilds, direct users to the energy channel.
        if interaction.guild is not None:
            await interaction.response.send_message(
                "Please DM me this command, or ask an admin to use /add_more_energy in the energy channel.",
                ephemeral=True,
            )
            return

        new_keys = _parse_keys_arg(keys)
        if not new_keys:
            await interaction.response.send_message("No keys provided.")
            return

        stats = await asyncio.to_thread(
            key_store.add_api_keys,
            new_keys=new_keys,
            added_by_id=interaction.user.id,
            added_by_name=str(interaction.user),
            source="dm",
        )

        await interaction.response.send_message(
            f"Thanks. Stored {stats.get('added', 0)} key(s) (skipped {stats.get('skipped', 0)} duplicate(s))."
        )

    @client.event
    async def on_ready():
        try:
            guild_cmds = await tree.sync(guild=guild_obj)
            print(f"[{bot_name}] Synced {len(guild_cmds)} guild command(s)")
        except Exception as exc:
            print(f"[{bot_name}] Guild command sync failed: {type(exc).__name__}: {exc}")

        # Global commands are required for slash commands to appear in DMs.
        # Note: global propagation can take some time on Discord's side.
        try:
            global_cmds = await tree.sync()
            print(f"[{bot_name}] Synced {len(global_cmds)} global command(s) (DM-capable)")
        except Exception as exc:
            print(f"[{bot_name}] Global command sync failed: {type(exc).__name__}: {exc}")

        print(f"[{bot_name}] Logged in as {client.user} (guild={config.guild_id})")

    @client.event
    async def on_message(message: discord.Message):
        # Utility-only bot: only reacts to DMs for user submissions.
        if message.author.bot:
            return

        if message.guild is not None:
            return

        content = (message.content or "").strip()
        if not content:
            return

        # DM command formats supported:
        # - add_more_energy key1,key2,key3
        # - /add_more_energy key1,key2,key3
        lowered = content.lower()
        if lowered.startswith("add_more_energy"):
            rest = content[len("add_more_energy") :].strip()
        elif lowered.startswith("/add_more_energy"):
            rest = content[len("/add_more_energy") :].strip()
        else:
            return

        new_keys = _parse_keys_arg(rest)
        if not new_keys:
            await message.channel.send("Send keys like: add_more_energy key1,key2,key3")
            return

        stats = await asyncio.to_thread(
            key_store.add_api_keys,
            new_keys=new_keys,
            added_by_id=message.author.id,
            added_by_name=str(message.author),
            source="dm",
        )

        await message.channel.send(
            f"Thanks. Stored {stats.get('added', 0)} key(s) (skipped {stats.get('skipped', 0)} duplicate(s))."
        )

    await client.start(config.discord_token)


def main(*, bot_name: str = "Admin", token_env: str = "ADMIN_BOT_TOKEN") -> None:
    asyncio.run(run_admin_bot(bot_name=bot_name, token_env=token_env))
