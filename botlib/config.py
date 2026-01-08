from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    bot_name: str

    discord_token: str
    guild_id: int
    target_channel_id: int
    energy_channel_id: int

    ollama_api_url: str
    ollama_model: str

    firebase_credentials_path: Path
    firestore_collection: str
    firestore_admin_keys_doc: str

    env_path: Path


def load_config(
    *,
    bot_name: str,
    token_env: str = "BOT_TOKEN",
    env_path: Optional[Path] = None,
) -> BotConfig:
    import os

    env_path = env_path or Path(__file__).resolve().parents[1] / ".env"

    # When running under process managers (e.g. pm2), stale environment variables can
    # persist across restarts. Allow opting into `.env` overriding the process env to
    # keep deployments consistent with local runs.
    dotenv_override = os.getenv("DOTENV_OVERRIDE", "").strip().lower() in {"1", "true", "yes", "on"}
    load_dotenv(dotenv_path=env_path, override=dotenv_override)

    discord_token = os.getenv(token_env, "").strip()
    if not discord_token:
        raise RuntimeError(f"Missing Discord token env var: {token_env}")

    def req_int(name: str) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            raise RuntimeError(f"Missing required env var: {name}")
        try:
            return int(raw)
        except ValueError as exc:
            raise RuntimeError(f"Env var {name} must be an int, got: {raw!r}") from exc

    # Your existing .env uses these names.
    guild_id = req_int("GUILD_ID")
    target_channel_id = req_int("TARGET_CHANNEL_ID")
    energy_channel_id = req_int("CHANNEL_ID")

    ollama_api_url = os.getenv("OLLAMA_API_URL", "https://ollama.com/api/chat").strip()
    ollama_model = os.getenv("OLLAMA_MODEL", "gpt-oss:120b").strip()

    firebase_credentials_raw = os.getenv("FIREBASE_CREDENTIALS_PATH", "service.json").strip() or "service.json"
    firebase_credentials_path = (env_path.parent / firebase_credentials_raw).resolve()
    firestore_collection = os.getenv("FIRESTORE_COLLECTION", "wuwa-gang").strip() or "wuwa-gang"
    firestore_admin_keys_doc = os.getenv("FIRESTORE_ADMIN_KEYS_DOC", "admin_keys").strip() or "admin_keys"

    return BotConfig(
        bot_name=bot_name,
        discord_token=discord_token,
        guild_id=guild_id,
        target_channel_id=target_channel_id,
        energy_channel_id=energy_channel_id,
        ollama_api_url=ollama_api_url,
        ollama_model=ollama_model,

        firebase_credentials_path=firebase_credentials_path,
        firestore_collection=firestore_collection,
        firestore_admin_keys_doc=firestore_admin_keys_doc,
        env_path=env_path,
    )
