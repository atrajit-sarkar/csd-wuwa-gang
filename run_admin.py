from __future__ import annotations

import argparse

from botlib.admin_bot import main


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the utility-only admin bot")
    p.add_argument("--bot-name", default="Admin")
    p.add_argument("--token-env", default="ADMIN_BOT_TOKEN")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(bot_name=args.bot_name, token_env=args.token_env)
