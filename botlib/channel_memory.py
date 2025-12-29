from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from firebase_admin import firestore

from .firestore_keys import _init_firebase


@dataclass(frozen=True)
class ChannelMemory:
    summary: str
    # Recent messages (chronological), each: {message_id, role, content}
    recent_messages: list[dict[str, Any]]
    recent_count: int


class FirestoreChannelMemoryStore:
    """Stores target-channel chat history in Firestore in an optimized form.

    Design:
    - One document per channel holds a rolling summary and counters.
    - A subcollection stores recent messages (bounded window).

    This keeps prompts stable (summary + recent window) while avoiding unbounded growth.
    """

    def __init__(
        self,
        *,
        credentials_path: Path,
        collection: str,
        prefix: str = "channel_memory_",
        recent_subcollection: str = "recent_messages",
    ) -> None:
        _init_firebase(credentials_path=credentials_path)
        self._db = firestore.client()
        self._collection = collection
        self._prefix = prefix
        self._recent_subcollection = recent_subcollection

    def _doc_id(self, *, guild_id: int, channel_id: int) -> str:
        return f"{self._prefix}{guild_id}_{channel_id}"

    def _doc_ref(self, *, guild_id: int, channel_id: int):
        return self._db.collection(self._collection).document(self._doc_id(guild_id=guild_id, channel_id=channel_id))

    def _recent_ref(self, *, guild_id: int, channel_id: int):
        return self._doc_ref(guild_id=guild_id, channel_id=channel_id).collection(self._recent_subcollection)

    def append_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        message_id: int,
        author_id: int,
        author_is_bot: bool,
        content: str,
    ) -> None:
        content = (content or "").strip()
        if not content:
            return

        role = "assistant" if author_is_bot else "user"
        doc = {
            "message_id": message_id,
            "author_id": author_id,
            "role": role,
            "content": content,
            "created_at": firestore.SERVER_TIMESTAMP,
        }

        # Store message doc keyed by message_id for stable ordering.
        doc_ref = self._recent_ref(guild_id=guild_id, channel_id=channel_id).document(str(message_id))
        doc_ref.set(doc, merge=True)

        # Update channel-level metadata.
        self._doc_ref(guild_id=guild_id, channel_id=channel_id).set(
            {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "recent_count": firestore.Increment(1),
                "last_message_id": message_id,
            },
            merge=True,
        )

    def get_memory(
        self,
        *,
        guild_id: int,
        channel_id: int,
        recent_limit: int = 30,
    ) -> Optional[ChannelMemory]:
        # Read summary first.
        snap = self._doc_ref(guild_id=guild_id, channel_id=channel_id).get()
        if not snap.exists:
            return None

        data = snap.to_dict() or {}
        summary = data.get("summary") if isinstance(data.get("summary"), str) else ""

        # Fetch recent messages ordered by id.
        query = (
            self._recent_ref(guild_id=guild_id, channel_id=channel_id)
            .order_by("message_id", direction=firestore.Query.ASCENDING)
            .limit_to_last(int(recent_limit))
        )
        docs = list(query.stream())

        recent: list[dict[str, Any]] = []
        for d in docs:
            row = d.to_dict() or {}
            role = row.get("role")
            content = row.get("content")
            mid = row.get("message_id")
            if isinstance(role, str) and isinstance(content, str) and content.strip() and isinstance(mid, int):
                recent.append({"message_id": mid, "role": role, "content": content.strip()})

        recent_count = int(data.get("recent_count") or 0)
        return ChannelMemory(summary=summary.strip(), recent_messages=recent, recent_count=recent_count)

    def set_summary_and_compact(
        self,
        *,
        guild_id: int,
        channel_id: int,
        new_summary: str,
        keep_last_message_ids: list[int],
    ) -> None:
        new_summary = (new_summary or "").strip()

        # Update summary.
        self._doc_ref(guild_id=guild_id, channel_id=channel_id).set(
            {
                "summary": new_summary,
                "summary_updated_at": firestore.SERVER_TIMESTAMP,
                "recent_count": len(keep_last_message_ids),
            },
            merge=True,
        )

        # Delete everything not in keep list.
        keep: set[str] = {str(mid) for mid in keep_last_message_ids}
        recent_coll = self._recent_ref(guild_id=guild_id, channel_id=channel_id)
        # Stream IDs only; do best-effort deletes.
        for doc in recent_coll.stream():
            if doc.id not in keep:
                doc.reference.delete()

    def list_recent_message_ids(self, *, guild_id: int, channel_id: int, limit: int = 200) -> list[int]:
        query = (
            self._recent_ref(guild_id=guild_id, channel_id=channel_id)
            .order_by("message_id", direction=firestore.Query.ASCENDING)
            .limit_to_last(int(limit))
        )
        out: list[int] = []
        for d in query.stream():
            row = d.to_dict() or {}
            mid = row.get("message_id")
            if isinstance(mid, int):
                out.append(mid)
            elif isinstance(mid, str) and mid.isdigit():
                out.append(int(mid))
        return out

    def clear_memory(self, *, guild_id: int, channel_id: int) -> None:
        """Delete channel memory summary and stored recent messages.

        This removes the channel memory document and best-effort deletes all docs in the
        recent_messages subcollection.
        """

        recent_coll = self._recent_ref(guild_id=guild_id, channel_id=channel_id)
        for doc in recent_coll.stream():
            doc.reference.delete()

        self._doc_ref(guild_id=guild_id, channel_id=channel_id).delete()

    def get_recent_messages_for_summary(
        self,
        *,
        guild_id: int,
        channel_id: int,
        limit: int = 120,
    ) -> list[dict[str, str]]:
        query = (
            self._recent_ref(guild_id=guild_id, channel_id=channel_id)
            .order_by("message_id", direction=firestore.Query.ASCENDING)
            .limit_to_last(int(limit))
        )
        docs = list(query.stream())
        out: list[dict[str, str]] = []
        for d in docs:
            row = d.to_dict() or {}
            role = row.get("role")
            content = row.get("content")
            if isinstance(role, str) and isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content.strip()})
        return out
