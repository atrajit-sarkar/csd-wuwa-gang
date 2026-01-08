from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import firebase_admin
from firebase_admin import credentials, firestore


_init_lock = threading.Lock()
_app_inited = False


def _init_firebase(*, credentials_path: Path) -> None:
    global _app_inited
    if _app_inited:
        return
    with _init_lock:
        if _app_inited:
            return
        cred = credentials.Certificate(str(credentials_path))
        firebase_admin.initialize_app(cred)
        _app_inited = True


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KeyMeta:
    api_key: str
    key_id: str
    added_by_id: int
    added_by_name: str
    source: str  # "guild" | "dm"


class FirestoreKeyStore:
    def __init__(
        self,
        *,
        credentials_path: Path,
        collection: str,
        doc_id: str = "admin_keys",
    ) -> None:
        _init_firebase(credentials_path=credentials_path)
        self._db = firestore.client()
        self._doc_ref = self._db.collection(collection).document(doc_id)

    def list_api_keys(self) -> list[str]:
        snap = self._doc_ref.get()
        if not snap.exists:
            return []
        data = snap.to_dict() or {}
        keys = data.get("keys")
        if not isinstance(keys, dict):
            return []

        out: list[str] = []
        for _, entry in keys.items():
            if isinstance(entry, dict):
                api_key = entry.get("api_key")
                if isinstance(api_key, str) and api_key.strip():
                    out.append(api_key.strip())

        # De-dup preserve order
        seen: set[str] = set()
        deduped: list[str] = []
        for k in out:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        return deduped

    def list_elevenlabs_api_keys(self) -> list[str]:
        """List ElevenLabs API keys stored in Firestore.

        Stored separately from Ollama keys to avoid mixing providers.
        """

        snap = self._doc_ref.get()
        if not snap.exists:
            return []

        data = snap.to_dict() or {}
        keys = data.get("elevenlabs_keys")
        if not isinstance(keys, dict):
            return []

        out: list[str] = []
        for _, entry in keys.items():
            if isinstance(entry, dict):
                api_key = entry.get("api_key")
                if isinstance(api_key, str) and api_key.strip():
                    out.append(api_key.strip())

        seen: set[str] = set()
        deduped: list[str] = []
        for k in out:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        return deduped

    def add_api_keys(
        self,
        *,
        new_keys: list[str],
        added_by_id: int,
        added_by_name: str,
        source: str,
    ) -> dict[str, Any]:
        cleaned = [k.strip() for k in new_keys if k and k.strip()]
        if not cleaned:
            return {"added": 0, "skipped": 0, "total": len(self.list_api_keys())}

        # Read existing keys once so we can skip true duplicates.
        existing_snap = self._doc_ref.get()
        existing_data = existing_snap.to_dict() if existing_snap.exists else {}
        existing_keys = existing_data.get("keys") if isinstance(existing_data, dict) else None
        existing_key_ids: set[str] = set(existing_keys.keys()) if isinstance(existing_keys, dict) else set()

        # Build update payload with deterministic IDs.
        # If the ID already exists, skip (prevents needless rewrites and duplicates).
        update: dict[str, Any] = {}
        skipped = 0
        for api_key in cleaned:
            kid = _sha256_hex(api_key)[:24]
            if kid in existing_key_ids:
                skipped += 1
                continue
            update[f"keys.{kid}"] = {
                "api_key": api_key,
                "key_id": kid,
                "added_by": {"id": added_by_id, "name": added_by_name},
                "added_at": firestore.SERVER_TIMESTAMP,
                "source": source,
            }

        if update:
            # Ensure doc exists; set merge also works.
            self._doc_ref.set({}, merge=True)
            self._doc_ref.update(update)

        total = len(self.list_api_keys())
        return {"added": len(update), "skipped": skipped, "total": total}

    def add_elevenlabs_api_keys(
        self,
        *,
        new_keys: list[str],
        added_by_id: int,
        added_by_name: str,
        source: str,
    ) -> dict[str, Any]:
        cleaned = [k.strip() for k in new_keys if k and k.strip()]
        if not cleaned:
            return {"added": 0, "skipped": 0, "total": len(self.list_elevenlabs_api_keys())}

        existing_snap = self._doc_ref.get()
        existing_data = existing_snap.to_dict() if existing_snap.exists else {}
        existing_keys = existing_data.get("elevenlabs_keys") if isinstance(existing_data, dict) else None
        existing_key_ids: set[str] = set(existing_keys.keys()) if isinstance(existing_keys, dict) else set()

        update: dict[str, Any] = {}
        skipped = 0
        for api_key in cleaned:
            kid = _sha256_hex(api_key)[:24]
            if kid in existing_key_ids:
                skipped += 1
                continue
            update[f"elevenlabs_keys.{kid}"] = {
                "api_key": api_key,
                "key_id": kid,
                "added_by": {"id": added_by_id, "name": added_by_name},
                "added_at": firestore.SERVER_TIMESTAMP,
                "source": source,
            }

        if update:
            self._doc_ref.set({}, merge=True)
            self._doc_ref.update(update)

        total = len(self.list_elevenlabs_api_keys())
        return {"added": len(update), "skipped": skipped, "total": total}

    def get_ollama_model(self) -> Optional[str]:
        """Return the runtime Ollama model override, if configured."""

        snap = self._doc_ref.get()
        if not snap.exists:
            return None

        data = snap.to_dict() or {}
        runtime = data.get("runtime") if isinstance(data, dict) else None
        if not isinstance(runtime, dict):
            return None

        model = runtime.get("ollama_model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        return None

    def set_ollama_model(self, *, model: str, updated_by_id: int, updated_by_name: str, source: str) -> None:
        cleaned = (model or "").strip()
        if not cleaned:
            raise ValueError("Model must be a non-empty string")

        update: dict[str, Any] = {
            "runtime.ollama_model": cleaned,
            "runtime.ollama_model_updated_by": {"id": updated_by_id, "name": updated_by_name},
            "runtime.ollama_model_updated_at": firestore.SERVER_TIMESTAMP,
            "runtime.ollama_model_source": source,
        }

        # Ensure doc exists; set merge also works.
        self._doc_ref.set({}, merge=True)
        self._doc_ref.update(update)

    def clear_ollama_model(self, *, cleared_by_id: int, cleared_by_name: str, source: str) -> None:
        """Remove the runtime Ollama model override.

        After clearing, bots will fall back to their configured default model.
        """

        update: dict[str, Any] = {
            "runtime.ollama_model": firestore.DELETE_FIELD,
            "runtime.ollama_model_updated_by": firestore.DELETE_FIELD,
            "runtime.ollama_model_updated_at": firestore.DELETE_FIELD,
            "runtime.ollama_model_source": firestore.DELETE_FIELD,
            "runtime.ollama_model_cleared_by": {"id": cleared_by_id, "name": cleared_by_name},
            "runtime.ollama_model_cleared_at": firestore.SERVER_TIMESTAMP,
            "runtime.ollama_model_cleared_source": source,
        }

        self._doc_ref.set({}, merge=True)
        self._doc_ref.update(update)
