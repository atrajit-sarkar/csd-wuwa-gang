from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    cfg_path = root / "bots.json"
    if not cfg_path.exists():
        print("Missing bots.json")
        return 2

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    bots = cfg.get("bots")
    if not isinstance(bots, list) or not bots:
        print("bots.json has no bots")
        return 2

    procs: list[subprocess.Popen] = []

    python_exe = sys.executable
    env = os.environ.copy()

    for bot in bots:
        bot_type = (bot.get("type") or "character").strip().lower() if isinstance(bot.get("type"), str) else "character"
        name = bot.get("name")
        token_env = bot.get("token_env") or "BOT_TOKEN"
        if not name:
            continue

        if bot_type == "admin":
            print(f"Starting admin bot: {name}")
            procs.append(
                subprocess.Popen(
                    [
                        python_exe,
                        str(root / "run_admin.py"),
                        "--bot-name",
                        str(name),
                        "--token-env",
                        str(token_env),
                    ],
                    cwd=str(root),
                    env=env,
                )
            )
            continue

        character = bot.get("character")
        if not character:
            continue

        print(f"Starting bot: {name} ({character})")
        procs.append(
            subprocess.Popen(
                [
                    python_exe,
                    str(root / "run_bot.py"),
                    "--bot-name",
                    str(name),
                    "--character-name",
                    str(character),
                    "--token-env",
                    str(token_env),
                ],
                cwd=str(root),
                env=env,
            )
        )

    if not procs:
        print("No valid bots in bots.json")
        return 2

    try:
        # Wait for any bot to exit; keep manager alive.
        while True:
            for p in procs:
                rc = p.poll()
                if rc is not None:
                    print(f"A bot exited with code {rc}. Stopping others...")
                    for other in procs:
                        if other.poll() is None:
                            other.terminate()
                    return rc
    except KeyboardInterrupt:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
