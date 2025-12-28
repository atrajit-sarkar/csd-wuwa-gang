from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from firebase_admin import firestore

from .firestore_keys import _init_firebase


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F700-\U0001F77F"  # alchemical symbols
    "\U0001F780-\U0001F7FF"  # geometric shapes extended
    "\U0001F800-\U0001F8FF"  # supplemental arrows-C
    "\U0001F900-\U0001F9FF"  # supplemental symbols and pictographs
    "\U0001FA00-\U0001FA6F"  # chess symbols etc.
    "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-A
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "]+",
    flags=re.UNICODE,
)

_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "but",
    "can",
    "could",
    "did",
    "does",
    "don",
    "for",
    "from",
    "have",
    "help",
    "here",
    "how",
    "i",
    "if",
    "im",
    "in",
    "is",
    "it",
    "just",
    "like",
    "me",
    "my",
    "need",
    "now",
    "of",
    "ok",
    "okay",
    "on",
    "or",
    "pls",
    "please",
    "so",
    "that",
    "the",
    "then",
    "this",
    "to",
    "u",
    "us",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "ya",
    "yeah",
    "you",
    "your",
}


def _extract_keywords(text: str) -> list[str]:
    t = (text or "").lower()
    words = re.findall(r"[a-z0-9_]{4,}", t)
    cleaned = [w for w in words if w not in _STOPWORDS]
    # De-dup preserve order
    seen: set[str] = set()
    out: list[str] = []
    for w in cleaned:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:12]


def _looks_like_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if "?" in t:
        return True
    return bool(re.match(r"^(why|how|what|when|where|who|which)\b", t.lower()))


def _is_non_english_heavy(text: str) -> bool:
    # Very rough heuristic: if a large fraction of letters are non-ascii.
    t = text or ""
    letters = [ch for ch in t if ch.isalpha()]
    if len(letters) < 8:
        return False
    non_ascii = sum(1 for ch in letters if ord(ch) > 127)
    return (non_ascii / max(1, len(letters))) >= 0.30


@dataclass(frozen=True)
class UserProfileSummary:
    summary: str
    stats: dict[str, Any]


class FirestoreUserProfileStore:
    """Stores lightweight, per-user behavioural preferences for personalization."""

    def __init__(
        self,
        *,
        credentials_path: Path,
        collection: str,
        prefix: str = "user_profile_",
    ) -> None:
        _init_firebase(credentials_path=credentials_path)
        self._db = firestore.client()
        self._collection = collection
        self._prefix = prefix

    def _doc_ref(self, user_id: int):
        return self._db.collection(self._collection).document(f"{self._prefix}{user_id}")

    def record_user_message(self, *, user_id: int, user_name: str, content: str, source: str = "discord") -> None:
        content = (content or "").strip()
        if not content:
            return

        emoji_count = len(_EMOJI_RE.findall(content))
        is_question = _looks_like_question(content)
        non_english_heavy = _is_non_english_heavy(content)
        keywords = _extract_keywords(content)

        # Keep only a small rolling set of recent keywords.
        update: dict[str, Any] = {
            "user_id": user_id,
            "user_name": user_name,
            "source": source,
            "stats.message_count": firestore.Increment(1),
            "stats.total_chars": firestore.Increment(len(content)),
            "stats.question_count": firestore.Increment(1 if is_question else 0),
            "stats.emoji_message_count": firestore.Increment(1 if emoji_count > 0 else 0),
            "stats.non_english_heavy_count": firestore.Increment(1 if non_english_heavy else 0),
            "stats.last_seen_at": firestore.SERVER_TIMESTAMP,
            "stats.last_keywords": keywords,
        }

        self._doc_ref(user_id).set({}, merge=True)
        self._doc_ref(user_id).update(update)

    def get_summary(self, *, user_id: int) -> Optional[UserProfileSummary]:
        snap = self._doc_ref(user_id).get()
        if not snap.exists:
            return None

        data = snap.to_dict() or {}
        stats = data.get("stats") if isinstance(data, dict) else None
        if not isinstance(stats, dict):
            stats = {}

        message_count = int(stats.get("message_count") or 0)
        total_chars = int(stats.get("total_chars") or 0)
        question_count = int(stats.get("question_count") or 0)
        emoji_message_count = int(stats.get("emoji_message_count") or 0)
        non_english_heavy_count = int(stats.get("non_english_heavy_count") or 0)
        last_keywords = stats.get("last_keywords") if isinstance(stats.get("last_keywords"), list) else []

        if message_count <= 0:
            return None

        avg_len = total_chars / max(1, message_count)
        question_ratio = question_count / max(1, message_count)
        emoji_ratio = emoji_message_count / max(1, message_count)
        non_en_ratio = non_english_heavy_count / max(1, message_count)

        # Build a short, actionable summary.
        prefs: list[str] = []
        if avg_len < 70:
            prefs.append("tends to write short messages")
        elif avg_len > 240:
            prefs.append("often provides long/detailed messages")

        if question_ratio >= 0.45:
            prefs.append("often asks direct questions")

        if emoji_ratio >= 0.35:
            prefs.append("often uses emojis")

        if non_en_ratio >= 0.30:
            prefs.append("often writes in a non-English script; reply in the user's language")

        if last_keywords:
            prefs.append(f"recent topics/keywords: {', '.join(str(k) for k in last_keywords[:8])}")

        summary = "User preferences (learned): " + ("; ".join(prefs) if prefs else "No strong preferences detected yet.")

        return UserProfileSummary(summary=summary, stats={
            "message_count": message_count,
            "avg_len": avg_len,
            "question_ratio": question_ratio,
            "emoji_ratio": emoji_ratio,
            "non_en_ratio": non_en_ratio,
            "last_keywords": last_keywords,
        })
