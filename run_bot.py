from __future__ import annotations

import argparse

from botlib.admin_bot import main as admin_main
from botlib.discord_bot import main as character_main


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one bot (character or admin)")
    p.add_argument("--type", choices=["character", "admin"], default="character")
    p.add_argument("--bot-name", required=True)
    p.add_argument("--token-env", default="BOT_TOKEN")
    p.add_argument(
        "--character-name",
        help="Required when --type=character",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.type == "admin":
        admin_main(bot_name=args.bot_name, token_env=args.token_env)
    else:
        if not args.character_name:
            raise SystemExit("--character-name is required when --type=character")
        character_main(bot_name=args.bot_name, character_name=args.character_name, token_env=args.token_env)
