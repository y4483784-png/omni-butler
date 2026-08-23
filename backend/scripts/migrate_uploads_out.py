#!/usr/bin/env python3
"""Migrate legacy local uploads to MinIO/S3 (object keys in DB)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal, init_db
from app.models.models import Document
from app.storage import get_blob_store, make_object_key
from app.storage.paths import is_legacy_absolute_path


def _default_legacy_roots() -> list[Path]:
    backend = Path(__file__).resolve().parents[1]
    roots = [
        (backend / "data" / "uploads").resolve(),
        (Path.cwd() / "data" / "uploads").resolve(),
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def migrate(*, delete_old: bool = False, dry_run: bool = False) -> int:
    init_db()
    store = get_blob_store()
    legacy_roots = _default_legacy_roots()

    db = SessionLocal()
    migrated = 0
    skipped = 0
    try:
        docs = db.query(Document).filter(Document.stored_path != "").all()
        for doc in docs:
            sp = doc.stored_path or ""
            if not is_legacy_absolute_path(sp):
                skipped += 1
                continue
            src = Path(sp)
            if not src.is_file():
                print(f"skip missing file doc={doc.id} path={sp}")
                skipped += 1
                continue
            in_legacy = any(
                src == root or root in src.parents for root in legacy_roots
            )
            if not in_legacy:
                print(f"skip non-legacy path doc={doc.id} path={sp}")
                skipped += 1
                continue

            key = make_object_key(user_id=doc.user_id or 1, filename=doc.filename or src.name)
            print(f"migrate doc={doc.id} -> {key}")
            if dry_run:
                migrated += 1
                continue

            data = src.read_bytes()
            store.put(key, data)
            doc.stored_path = key
            db.commit()
            migrated += 1

            if delete_old:
                try:
                    src.unlink()
                except OSError as exc:
                    print(f"warn: could not delete old file {src}: {exc}")
    finally:
        db.close()

    print(f"done migrated={migrated} skipped={skipped} dry_run={dry_run}")
    return migrated


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate in-repo uploads to MinIO/S3")
    parser.add_argument("--dry-run", action="store_true", help="List actions only")
    parser.add_argument(
        "--delete-old",
        action="store_true",
        help="Remove legacy files after successful migration",
    )
    args = parser.parse_args()
    migrate(delete_old=args.delete_old, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
