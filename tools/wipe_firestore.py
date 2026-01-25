from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

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
            "Deletes ALL documents (and subcollections) under a Firestore collection. "
            "This is destructive and intended for local/dev resets."
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
        help="Top-level Firestore collection to wipe (default: FIRESTORE_COLLECTION or wuwa-gang)",
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

    if not credentials_path.exists():
        raise SystemExit(f"Credentials file not found: {credentials_path}")
    if not collection:
        raise SystemExit("Missing collection name")

    project_id = _read_service_project_id(credentials_path)
    project_label = project_id or "(unknown project_id)"

    print(f"Target project: {project_label}")
    print(f"Credentials: {credentials_path}")
    print(f"Collection: {collection}")

    if not args.yes:
        print("\nDry run: no changes made.")
        print("Re-run with --yes to actually delete everything under this collection.")
        return 0

    # Connect and wipe.
    _init_firebase(credentials_path=credentials_path)
    from firebase_admin import firestore  # imported after init to match other modules

    db = firestore.client()
    deleted = _delete_collection_recursive(db.collection(collection), page_size=max(1, int(args.page_size)))

    print(f"\nDone. Deleted {deleted} documents (including nested subcollection docs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
