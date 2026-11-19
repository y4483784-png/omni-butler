from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import update

from app.chat_images import ChatImage, data_url
from app.core.db import SessionLocal, set_rls_context
from app.models.models import TaskImage, TaskRun
from app.storage.base import BlobStore

logger = logging.getLogger(__name__)

_TERMINAL = ("completed", "stopped")


class ImageBindError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LoadedImages:
    kind: str
    blocks: tuple[dict, ...]


def bind_images(
    db,
    *,
    user_id: int,
    task_run_id: int,
    images: Sequence[ChatImage],
    store: BlobStore,
) -> list[TaskImage]:
    task = db.get(TaskRun, task_run_id)
    if task is None or int(task.user_id) != int(user_id):
        raise ImageBindError("owner")
    if task.status in _TERMINAL:
        raise ImageBindError("terminal")
    rows: list[TaskImage] = []
    for index, image in enumerate(images):
        digest = hashlib.sha256(image.data).hexdigest()
        key = f"task-image/{int(user_id)}/{int(task_run_id)}/{digest}-{index}"
        stored = store.put(key, image.data)
        if not str(stored).startswith("task-image/"):
            raise ImageBindError("key")
        row = TaskImage(
            user_id=int(user_id),
            task_run_id=int(task_run_id),
            media_type=image.media_type,
            byte_length=len(image.data),
            sha256=digest,
            storage_key=str(stored),
            purge_state="ready",
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def mark_pending_delete(db, *, user_id: int, task_run_id: int) -> int:
    result = db.execute(
        update(TaskImage)
        .where(TaskImage.user_id == int(user_id))
        .where(TaskImage.task_run_id == int(task_run_id))
        .where(TaskImage.purge_state == "ready")
        .values(purge_state="pending_delete")
    )
    return int(result.rowcount or 0)


def _read_blob(store: BlobStore, key: str) -> bytes | None:
    if not store.exists(key):
        return None
    path = store.materialize(key)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    finally:
        store.release_materialized(path, key)


def load_image_blocks(db, *, user_id: int, task_run_id: int, store: BlobStore) -> LoadedImages:
    task = db.get(TaskRun, task_run_id)
    if task is None or int(task.user_id) != int(user_id):
        return LoadedImages("hidden", ())
    if task.status in _TERMINAL:
        return LoadedImages("hidden", ())
    rows = (
        db.query(TaskImage)
        .filter(
            TaskImage.user_id == int(user_id),
            TaskImage.task_run_id == int(task_run_id),
            TaskImage.purge_state == "ready",
        )
        .order_by(TaskImage.id.asc())
        .all()
    )
    blocks: list[dict] = []
    missing = False
    for row in rows:
        data = _read_blob(store, row.storage_key)
        if data is None:
            missing = True
            logger.info(
                "task image unavailable user_id=%s task_run_id=%s storage_key=%s",
                user_id,
                task_run_id,
                row.storage_key,
            )
            continue
        image = ChatImage(media_type=row.media_type, data=data)
        blocks.append({"type": "image_url", "image_url": {"url": data_url(image)}})
    if missing:
        return LoadedImages("unavailable", tuple(blocks))
    return LoadedImages("ready", tuple(blocks))


def purge_pending(*, user_id: int, store: BlobStore | None = None) -> int:
    db = SessionLocal()
    removed = 0
    try:
        set_rls_context(db, user_id)
        rows = (
            db.query(TaskImage)
            .filter(TaskImage.user_id == int(user_id), TaskImage.purge_state == "pending_delete")
            .all()
        )
        if not rows:
            return 0
        blob_store = store if store is not None else _default_store()
        for row in rows:
            key = row.storage_key
            try:
                if blob_store.exists(key):
                    blob_store.delete(key)
                if blob_store.exists(key):
                    continue
            except Exception:
                logger.exception("task image blob delete failed key=%s", key)
                continue
            db.delete(row)
            db.commit()
            removed += 1
        return removed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _default_store() -> BlobStore:
    from app.storage import get_blob_store

    return get_blob_store()
