"""Helpers for Document.stored_path (object key or legacy absolute path)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.storage.base import MaterializedBlob
from app.storage.factory import get_blob_store
from app.storage.paths import is_legacy_absolute_path
def document_exists(stored_path: str) -> bool:
    if not stored_path:
        return False
    if is_legacy_absolute_path(stored_path):
        return Path(stored_path).is_file()
    return get_blob_store().exists(stored_path)


def delete_document_blob(stored_path: str) -> None:
    if not stored_path:
        return
    if is_legacy_absolute_path(stored_path):
        path = Path(stored_path)
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
        return
    get_blob_store().delete(stored_path)


def materialize_document(stored_path: str) -> MaterializedBlob:
    if not stored_path:
        raise FileNotFoundError("上传文件丢失")
    if is_legacy_absolute_path(stored_path):
        path = Path(stored_path)
        if not path.is_file():
            raise FileNotFoundError("上传文件丢失")
        # Compatibility for pre-migration records only. New uploads never use
        # local paths; migrate_uploads_out.py moves these objects to MinIO.
        return MaterializedBlob(path=path, key=stored_path, store=None)

    store = get_blob_store()
    try:
        path = store.materialize(stored_path)
    except FileNotFoundError as exc:
        raise FileNotFoundError("上传文件丢失") from exc
    return MaterializedBlob(path=path, key=stored_path, store=store)


@contextmanager
def document_local_path(stored_path: str) -> Iterator[Path]:
    blob = materialize_document(stored_path)
    try:
        yield blob.path
    finally:
        blob.release()
