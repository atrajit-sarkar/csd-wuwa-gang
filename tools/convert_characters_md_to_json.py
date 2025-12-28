from __future__ import annotations

import json
import re
from pathlib import Path


HEADER_RE = re.compile(r"^###\s+\*\*(.+?)\*\*\s*$")


def parse_characters_md(text: str) -> dict[str, str]:
    characters: dict[str, list[str]] = {}
    current_name: str | None = None

    for line in text.splitlines():
        m = HEADER_RE.match(line.strip())
        if m:
            current_name = m.group(1).strip()
            characters.setdefault(current_name, [])
            continue

        if current_name is None:
            continue

        characters[current_name].append(line)

    # Join blocks
    out: dict[str, str] = {}
    for name, lines in characters.items():
        block = "\n".join(lines).strip()
        if block:
            out[name] = block
    return out


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    md_path = root / "characters.md"
    json_path = root / "characters.json"

    if not md_path.exists():
        raise SystemExit("characters.md not found")

    text = md_path.read_text(encoding="utf-8")
    blocks = parse_characters_md(text)

    # Minimal aliases to keep compatibility with prior spelling.
    aliases = {
        "Linae": "Lynae",
        "linae": "Lynae",
    }

    payload = {
        "version": 1,
        "source": "characters.md",
        "characters": {name: {"name": name, "prompt_block": block} for name, block in blocks.items()},
        "aliases": aliases,
    }

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {json_path} with {len(blocks)} characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
