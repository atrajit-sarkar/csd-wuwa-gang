from __future__ import annotations

import argparse

from botlib.discord_bot import main


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one character bot")
    p.add_argument("--bot-name", required=True)
    p.add_argument("--character-name", required=True)
    p.add_argument("--token-env", default="BOT_TOKEN")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(bot_name=args.bot_name, character_name=args.character_name, token_env=args.token_env)
