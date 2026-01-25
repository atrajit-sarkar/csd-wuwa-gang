from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Callable, Iterable

from dotenv import load_dotenv


# Ensure repo root is on sys.path so `botlib` is importable when running as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from botlib.firestore_keys import _init_firebase


def _iter_documents(collection_ref, *, page_size: int) -> Iterable[object]:
    """Yield documents from a collection in pages.

    We intentionally do not try to be clever with cursors: after each page is deleted,
    we re-query the first N docs until the collection is empty.
    """

    while True:
        docs = list(collection_ref.limit(page_size).stream())
        if not docs:
            return
        for doc in docs:
            yield doc


def _delete_document_recursive(doc_ref) -> int:
    """Recursively delete a document and all subcollections.

    Returns number of documents deleted (including nested docs and the doc itself).
    """

    deleted = 0

    # Delete subcollection documents first.
    for subcoll in doc_ref.collections():
        deleted += _delete_collection_recursive(subcoll)

    doc_ref.delete()
    return deleted + 1


def _delete_collection_recursive(collection_ref, *, page_size: int = 200) -> int:
    deleted = 0
    for doc in _iter_documents(collection_ref, page_size=page_size):
        deleted += _delete_document_recursive(doc.reference)
    return deleted


def _sanitize_bot_key(value: str) -> str:
    # Keep behavior aligned with FirestoreChannelMemoryStore._sanitize_bot_key
    import re

    t = (value or "").strip().lower()
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"[^a-z0-9_\-]", "", t)
    return t or "bot"


def _build_channel_memory_matcher(
    *,
    prefix: str,
    bot_key: str | None,
    guild_id: int | None,
    channel_id: int | None,
    user_id: int | None,
) -> Callable[[str], bool]:
    """Return a predicate that matches channel-memory document ids.

    Expected document id format (see botlib/channel_memory.py):
      {prefix}{bot_key}_{guild_id}_{channel_id}_{user_id}

    Note: bot_key itself may contain underscores/hyphens.
    """

    import re

    # Bot key must be sanitized to match stored ids.
    bot_pat = re.escape(_sanitize_bot_key(bot_key)) if bot_key else r"[-a-z0-9_]+"

    # If an id is provided, it must match exactly.
    gid_pat = re.escape(str(int(guild_id))) if isinstance(guild_id, int) and guild_id > 0 else r"\d+"
    cid_pat = re.escape(str(int(channel_id))) if isinstance(channel_id, int) and channel_id > 0 else r"\d+"
    uid_pat = re.escape(str(int(user_id))) if isinstance(user_id, int) and user_id > 0 else r"\d+"

    rx = re.compile(rf"^{re.escape(prefix)}{bot_pat}_{gid_pat}_{cid_pat}_{uid_pat}$")
    return lambda doc_id: bool(rx.match(doc_id))


def _scan_collection_for_matches(
    *,
    collection_ref,
    match_doc_id: Callable[[str], bool],
    protected_doc_ids: set[str],
    sample_limit: int = 25,
) -> tuple[int, list[str]]:
    matched = 0
    samples: list[str] = []
    for snap in collection_ref.stream():
        doc_id = getattr(snap, "id", "")
        if not isinstance(doc_id, str) or not doc_id:
            continue
        if doc_id in protected_doc_ids:
            continue
        if not match_doc_id(doc_id):
            continue
        matched += 1
        if len(samples) < sample_limit:
            samples.append(doc_id)
    return matched, samples


def _delete_matches_in_collection(
    *,
    collection_ref,
    match_doc_id: Callable[[str], bool],
    protected_doc_ids: set[str],
    page_size: int = 200,
) -> int:
    """Delete matching documents (and nested subcollections) under a collection.

    We intentionally do this via repeated scans + limit(page_size) so the tool is
    robust for large collections without holding all doc refs in memory.
    """

    deleted = 0

    # Each loop: pick up to N documents, delete those that match. Repeat until no
    # matching docs are found in the scanned window.
    while True:
        docs = list(collection_ref.limit(max(1, int(page_size))).stream())
        if not docs:
            return deleted

        progress = 0
        for snap in docs:
            doc_id = getattr(snap, "id", "")
            if not isinstance(doc_id, str) or not doc_id:
                continue
            if doc_id in protected_doc_ids:
                continue
            if not match_doc_id(doc_id):
                continue

            deleted += _delete_document_recursive(snap.reference)
            progress += 1

        # If we scanned a page and didn't delete anything, it likely means the
        # matching docs are outside the first page. Fall back to a full stream.
        if progress == 0:
            remaining: list[object] = []
            for snap in collection_ref.stream():
                doc_id = getattr(snap, "id", "")
                if not isinstance(doc_id, str) or not doc_id:
                    continue
                if doc_id in protected_doc_ids:
                    continue
                if match_doc_id(doc_id):
                    remaining.append(snap)
                    if len(remaining) >= max(1, int(page_size)):
                        break

            if not remaining:
                return deleted

            for snap in remaining:
                deleted += _delete_document_recursive(snap.reference)


def _read_service_project_id(credentials_path: Path) -> str | None:
    try:
        raw = json.loads(credentials_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    pid = raw.get("project_id") if isinstance(raw, dict) else None
    return pid if isinstance(pid, str) and pid.strip() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Safely clears Firestore channel memory without deleting API keys. "
            "By default it deletes documents whose ids start with 'channel_memory_' "
            "(and any nested subcollections like 'recent_messages')."
        )
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="Path to .env file (default: repo root .env)",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=None,
        help="Path to Firebase service account JSON (default: FIREBASE_CREDENTIALS_PATH or service.json)",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="Top-level Firestore collection to operate on (default: FIRESTORE_COLLECTION or wuwa-gang)",
    )
    parser.add_argument(
        "--admin-keys-doc",
        type=str,
        default=None,
        help="Document id that stores API keys (default: FIRESTORE_ADMIN_KEYS_DOC or admin_keys). This doc is never deleted.",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="channel_memory_",
        help="Document id prefix for channel memory (default: channel_memory_)",
    )
    parser.add_argument(
        "--bot-key",
        type=str,
        default=None,
        help="If set, only clear channel memory for this bot key (matches the bot_name; sanitized before matching)",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="If set, only clear channel memory for this guild id",
    )
    parser.add_argument(
        "--channel-id",
        type=int,
        default=None,
        help="If set, only clear channel memory for this channel id",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="If set, only clear channel memory for this user id",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=200,
        help="Documents deleted per page (default: 200)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually perform deletion (without this flag, no writes happen)",
    )

    args = parser.parse_args()

    env_path: Path = args.env
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

    repo_root = Path(__file__).resolve().parents[1]

    credentials_path = args.credentials
    if credentials_path is None:
        credentials_raw = (os.getenv("FIREBASE_CREDENTIALS_PATH", "service.json") or "service.json").strip()
        credentials_path = (env_path.parent / credentials_raw).resolve() if env_path else (repo_root / credentials_raw).resolve()

    collection = args.collection
    if collection is None:
        collection = (os.getenv("FIRESTORE_COLLECTION", "wuwa-gang") or "wuwa-gang").strip()

    admin_keys_doc = args.admin_keys_doc
    if admin_keys_doc is None:
        admin_keys_doc = (os.getenv("FIRESTORE_ADMIN_KEYS_DOC", "admin_keys") or "admin_keys").strip() or "admin_keys"

    prefix = (args.prefix or "channel_memory_").strip()
    if not prefix:
        raise SystemExit("Missing --prefix")

    if not credentials_path.exists():
        raise SystemExit(f"Credentials file not found: {credentials_path}")
    if not collection:
        raise SystemExit("Missing collection name")

    project_id = _read_service_project_id(credentials_path)
    project_label = project_id or "(unknown project_id)"

    print(f"Target project: {project_label}")
    print(f"Credentials: {credentials_path}")
    print(f"Collection: {collection}")
    print(f"Protected doc: {admin_keys_doc}")
    print(f"Match prefix: {prefix}")
    if args.bot_key:
        print(f"Bot filter: {_sanitize_bot_key(args.bot_key)}")
    if args.guild_id:
        print(f"Guild filter: {int(args.guild_id)}")
    if args.channel_id:
        print(f"Channel filter: {int(args.channel_id)}")
    if args.user_id:
        print(f"User filter: {int(args.user_id)}")

    # Connect.
    _init_firebase(credentials_path=credentials_path)
    from firebase_admin import firestore  # imported after init to match other modules

    db = firestore.client()
    top = db.collection(collection)

    protected_doc_ids = {admin_keys_doc, "admin_keys"}
    match_doc_id = _build_channel_memory_matcher(
        prefix=prefix,
        bot_key=args.bot_key,
        guild_id=args.guild_id,
        channel_id=args.channel_id,
        user_id=args.user_id,
    )

    matched, samples = _scan_collection_for_matches(
        collection_ref=top,
        match_doc_id=match_doc_id,
        protected_doc_ids=protected_doc_ids,
    )

    print(f"\nMatched channel-memory docs: {matched}")
    if samples:
        print("Sample ids:")
        for s in samples:
            print(f"- {s}")

    if not args.yes:
        print("\nDry run: no changes made.")
        print("Re-run with --yes to actually delete matched channel-memory docs.")
        return 0

    deleted = _delete_matches_in_collection(
        collection_ref=top,
        match_doc_id=match_doc_id,
        protected_doc_ids=protected_doc_ids,
        page_size=max(1, int(args.page_size)),
    )

    print(f"\nDone. Deleted {deleted} documents (including nested subcollection docs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
