from __future__ import annotations

from pathlib import Path
from typing import Iterable


def _split_csv(value: str) -> list[str]:
    items: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            items.append(part)
    return items


def load_key_list_from_env(env: dict[str, str]) -> list[str]:
    # Support either OLLAMA_API_KEYS (CSV) or legacy OLLAMA_API_KEY (single).
    keys: list[str] = []

    if "OLLAMA_API_KEYS" in env and env["OLLAMA_API_KEYS"]:
        keys.extend(_split_csv(env["OLLAMA_API_KEYS"]))

    if "OLLAMA_API_KEY" in env and env["OLLAMA_API_KEY"]:
        single = env["OLLAMA_API_KEY"].strip()
        if single:
            keys.append(single)

    # De-dup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def upsert_env_var(env_path: Path, key: str, value: str) -> None:
    """Update or append `key=value` in .env.

    Preserves unrelated lines; replaces the first matching assignment.
    """

    env_path.parent.mkdir(parents=True, exist_ok=True)

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []

    def is_assignment_line(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith(f"{key}=") or stripped.startswith(f"{key} =")

    replacement = f"{key}={value}\n"

    for i, line in enumerate(lines):
        if is_assignment_line(line):
            lines[i] = replacement
            env_path.write_text("".join(lines), encoding="utf-8")
            return

    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"

    lines.append(replacement)
    env_path.write_text("".join(lines), encoding="utf-8")


def add_api_keys(env_path: Path, *, new_keys: Iterable[str]) -> list[str]:
    from dotenv import dotenv_values

    env = {k: (v or "") for k, v in dotenv_values(env_path).items() if k}
    existing = load_key_list_from_env(env)

    merged: list[str] = []
    seen: set[str] = set()

    for key in list(existing) + [k.strip() for k in new_keys if k and k.strip()]:
        if key not in seen:
            seen.add(key)
            merged.append(key)

    upsert_env_var(env_path, "OLLAMA_API_KEYS", ",".join(merged))
    return merged
