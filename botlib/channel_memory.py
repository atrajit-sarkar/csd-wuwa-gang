from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import re

from firebase_admin import firestore

from .firestore_keys import _init_firebase


@dataclass(frozen=True)
class ChannelMemory:
    summary: str
    # Recent messages (chronological), each: {message_id, role, content}
    recent_messages: list[dict[str, Any]]
    recent_count: int
    cutoff_message_id: Optional[int]


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
        bot_key: str,
        prefix: str = "channel_memory_",
        recent_subcollection: str = "recent_messages",
    ) -> None:
        _init_firebase(credentials_path=credentials_path)
        self._db = firestore.client()
        self._collection = collection
        self._bot_key = self._sanitize_bot_key(bot_key)
        self._prefix = prefix
        self._recent_subcollection = recent_subcollection

    @staticmethod
    def _sanitize_bot_key(value: str) -> str:
        t = (value or "").strip().lower()
        t = re.sub(r"\s+", "_", t)
        t = re.sub(r"[^a-z0-9_\-]", "", t)
        return t or "bot"

    def _doc_id(self, *, guild_id: int, channel_id: int) -> str:
        # NOTE: kept for backwards compatibility of internal helpers.
        return f"{self._prefix}{self._bot_key}_{guild_id}_{channel_id}"

    def _doc_id_for_user(self, *, guild_id: int, channel_id: int, user_id: int) -> str:
        # Per-bot-per-user memory isolation: each bot keeps separate memory per user conversation.
        return f"{self._prefix}{self._bot_key}_{guild_id}_{channel_id}_{user_id}"

    def _doc_ref(self, *, guild_id: int, channel_id: int, user_id: int):
        return self._db.collection(self._collection).document(
            self._doc_id_for_user(guild_id=guild_id, channel_id=channel_id, user_id=user_id)
        )

    def _recent_ref(self, *, guild_id: int, channel_id: int, user_id: int):
        return self._doc_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id).collection(self._recent_subcollection)

    def append_message(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        message_id: int,
        author_id: int,
        author_is_bot: bool,
        author_name: str,
        content: str,
    ) -> None:
        content = (content or "").strip()
        if not content:
            return

        role = "assistant" if author_is_bot else "user"  # for backward compat; real role is decided at read-time
        doc = {
            "message_id": message_id,
            "author_id": author_id,
            "author_is_bot": bool(author_is_bot),
            "author_name": (author_name or "").strip(),
            "role": role,
            "content": content,
            "created_at": firestore.SERVER_TIMESTAMP,
        }

        # Store message doc keyed by message_id for stable ordering.
        doc_ref = self._recent_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id).document(str(message_id))
        doc_ref.set(doc, merge=True)

        # Update channel-level metadata.
        self._doc_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id).set(
            {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "scope_user_id": user_id,
                "bot_key": self._bot_key,
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
        user_id: int,
        recent_limit: int = 30,
    ) -> Optional[ChannelMemory]:
        # Read summary first.
        snap = self._doc_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id).get()
        if not snap.exists:
            return None

        data = snap.to_dict() or {}
        summary = data.get("summary") if isinstance(data.get("summary"), str) else ""
        cutoff_message_id = data.get("cutoff_message_id") if isinstance(data.get("cutoff_message_id"), int) else None

        # Fetch recent messages ordered by id.
        query = (
            self._recent_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id)
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
            author_id = row.get("author_id")
            author_is_bot = row.get("author_is_bot")
            author_name = row.get("author_name")

            if isinstance(content, str) and content.strip() and isinstance(mid, int):
                recent.append(
                    {
                        "message_id": mid,
                        "role": role if isinstance(role, str) else "",
                        "content": content.strip(),
                        "author_id": author_id if isinstance(author_id, int) else None,
                        "author_is_bot": bool(author_is_bot) if isinstance(author_is_bot, bool) else None,
                        "author_name": author_name if isinstance(author_name, str) else "",
                    }
                )

        recent_count = int(data.get("recent_count") or 0)
        return ChannelMemory(
            summary=summary.strip(),
            recent_messages=recent,
            recent_count=recent_count,
            cutoff_message_id=cutoff_message_id,
        )

    def set_summary_and_compact(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        new_summary: str,
        keep_last_message_ids: list[int],
    ) -> None:
        new_summary = (new_summary or "").strip()

        # Update summary.
        self._doc_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id).set(
            {
                "summary": new_summary,
                "summary_updated_at": firestore.SERVER_TIMESTAMP,
                "recent_count": len(keep_last_message_ids),
            },
            merge=True,
        )

        # Delete everything not in keep list.
        keep: set[str] = {str(mid) for mid in keep_last_message_ids}
        recent_coll = self._recent_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id)
        # Stream IDs only; do best-effort deletes.
        for doc in recent_coll.stream():
            if doc.id not in keep:
                doc.reference.delete()

    def list_recent_message_ids(self, *, guild_id: int, channel_id: int, user_id: int, limit: int = 200) -> list[int]:
        query = (
            self._recent_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id)
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

    def clear_memory(self, *, guild_id: int, channel_id: int, user_id: int, cutoff_message_id: int | None = None) -> None:
        """Delete channel memory summary and stored recent messages.

        This removes the channel memory document and best-effort deletes all docs in the
        recent_messages subcollection.
        """

        recent_coll = self._recent_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id)
        for doc in recent_coll.stream():
            doc.reference.delete()

        # Keep the doc so we can persist a cutoff marker (prevents bots from re-reading pre-reset Discord history).
        update: dict[str, Any] = {
            "summary": "",
            "recent_count": 0,
            "updated_at": firestore.SERVER_TIMESTAMP,
            "cleared_at": firestore.SERVER_TIMESTAMP,
        }
        if isinstance(cutoff_message_id, int) and cutoff_message_id > 0:
            update["cutoff_message_id"] = cutoff_message_id

        self._doc_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id).set(update, merge=True)

    def clear_all_user_memories(self, *, guild_id: int, channel_id: int, cutoff_message_id: int | None = None) -> int:
        """Clear all per-user memory docs for this bot in a given channel.

        This is used by admin tooling. It best-effort clears each user's subcollection and resets summary.
        Returns the number of user memory docs processed.
        """

        prefix = f"{self._prefix}{self._bot_key}_{guild_id}_{channel_id}_"
        processed = 0

        # Firestore doesn't support an efficient prefix query on document id without extra indexing.
        # Given expected small scale (per server/channel), we stream and filter.
        for doc in self._db.collection(self._collection).stream():
            if not doc.id.startswith(prefix):
                continue
            processed += 1

            # Delete recent messages subcollection.
            try:
                recent_coll = doc.reference.collection(self._recent_subcollection)
                for m in recent_coll.stream():
                    m.reference.delete()
            except Exception:
                pass

            # Reset summary + store cutoff marker.
            update: dict[str, Any] = {
                "summary": "",
                "recent_count": 0,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "cleared_at": firestore.SERVER_TIMESTAMP,
            }
            if isinstance(cutoff_message_id, int) and cutoff_message_id > 0:
                update["cutoff_message_id"] = cutoff_message_id
            try:
                doc.reference.set(update, merge=True)
            except Exception:
                pass

        return processed

    def get_recent_messages_for_summary(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        limit: int = 120,
    ) -> list[dict[str, str]]:
        query = (
            self._recent_ref(guild_id=guild_id, channel_id=channel_id, user_id=user_id)
            .order_by("message_id", direction=firestore.Query.ASCENDING)
            .limit_to_last(int(limit))
        )
        docs = list(query.stream())
        out: list[dict[str, str]] = []
        for d in docs:
            row = d.to_dict() or {}
            content = row.get("content")
            role = row.get("role")
            author_name = row.get("author_name")

            if isinstance(content, str) and content.strip():
                # For summarization, keep a simple role + speaker label to avoid confusing multiple bots.
                speaker = (author_name or "").strip()
                prefix = f"[{speaker}] " if speaker else ""
                out.append({"role": role if isinstance(role, str) and role else "user", "content": prefix + content.strip()})
        return out
