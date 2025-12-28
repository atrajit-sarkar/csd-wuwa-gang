from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class KeyRing:
    keys: list[str]
    index: int = 0

    @classmethod
    def from_env(cls, env_path) -> "KeyRing":
        from dotenv import dotenv_values

        from .env_store import load_key_list_from_env

        env = {k: (v or "") for k, v in dotenv_values(env_path).items() if k}
        keys = load_key_list_from_env(env)
        return cls(keys=keys)

    def is_empty(self) -> bool:
        return not self.keys

    def iter_keys(self) -> Iterable[str]:
        # Always try in fixed order; caller can keep metrics.
        for key in self.keys:
            yield key

    def set_keys(self, keys: list[str]) -> None:
        self.keys = keys
        self.index = 0
