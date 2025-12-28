from __future__ import annotations

from botlib.discord_bot import main


if __name__ == "__main__":
    # The character is spelled "Lynae" in characters.md, but you asked for "Linae".
    main(bot_name="Linae", character_name="Linae", token_env="BOT_TOKEN")
