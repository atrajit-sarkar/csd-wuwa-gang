from __future__ import annotations

import json
import re
from pathlib import Path


def load_character_persona(characters_md_path: Path, *, character_name: str) -> str:
    """Load a character persona block.

    Prefers `characters.json` (more reliable) and falls back to parsing `characters.md`.
    """

    normalized = character_name.strip()

    # 1) Prefer JSON if present.
    json_path = characters_md_path.with_suffix(".json")
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8"))
        aliases = data.get("aliases") if isinstance(data, dict) else {}
        if isinstance(aliases, dict):
            normalized = aliases.get(normalized, aliases.get(normalized.lower(), normalized))

        characters = data.get("characters") if isinstance(data, dict) else None
        if isinstance(characters, dict):
            entry = characters.get(normalized)
            if isinstance(entry, dict) and isinstance(entry.get("prompt_block"), str):
                return entry["prompt_block"].strip()

        raise RuntimeError(f"Character {normalized!r} not found in characters.json")

    # 2) Fallback: parse markdown.
    text = characters_md_path.read_text(encoding="utf-8")

    # Normalize common misspelling: Linae -> Lynae
    if normalized.lower() == "linae":
        normalized = "Lynae"

    pattern = re.compile(
        rf"^###\s+\*\*{re.escape(normalized)}\*\*\s*$\n(.*?)(?=^###\s+\*\*|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        raise RuntimeError(f"Character {normalized!r} not found in characters.md")

    return m.group(1).strip()


def make_system_prompt(*, character_block: str) -> str:
    # Keep it short and directive; the markdown block already contains style.
    return (
        "You are a Discord chat character roleplaying exactly as described below. "
        "Stay in-character, be helpful, and sound like a real person chatting (natural, not robotic). "
        "Keep replies concise unless asked for detail.\n\n"
        "CHARACTER PROFILE:\n"
        f"{character_block}\n"
    )
