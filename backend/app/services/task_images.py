from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import delete, not_, select, update

from app.chat_images import ChatImage, data_url
from app.core.db import SessionLocal, maintenance_session, set_rls_context
from app.models.models import TaskImage, TaskRun
from app.storage.base import BlobStore

logger = logging.getLogger(__name__)

_TERMINAL = ("completed", "stopped")
STAGED_HOLD = timedelta(minutes=10)


class ImageBindError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LoadedImages:
    kind: str
    blocks: tuple[dict, ...]


@dataclass(frozen=True)
class ReservedImage:
    id: int
    user_id: int
    task_run_id: int
    storage_key: str


def bind_images(
    db,
    *,
    user_id: int,
    task_run_id: int,
    images: Sequence[ChatImage],
    store: BlobStore,
) -> list[TaskImage]:
    reserved = reserve_images(user_id=user_id, task_run_id=task_run_id, images=images)
    if not reserved:
        return []
    try:
        for item, image in zip(reserved, images, strict=True):
            stored = store.put(item.storage_key, image.data)
            if stored != item.storage_key or not str(stored).startswith("task-image/"):
                raise ImageBindError("key")
    except Exception:
        release_reserved(reserved, store)
        raise
    if finish_reserved(reserved):
        ids = [item.id for item in reserved]
        return (
            db.query(TaskImage)
            .filter(TaskImage.id.in_(ids))
            .order_by(TaskImage.id.asc())
            .all()
        )
    release_reserved(reserved, store)
    raise ImageBindError(_bind_failure(reserved))


def reserve_images(
    *,
    user_id: int,
    task_run_id: int,
    images: Sequence[ChatImage],
) -> tuple[ReservedImage, ...]:
    if not images:
        return ()
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        task = db.get(TaskRun, task_run_id)
        if task is None or int(task.user_id) != int(user_id):
            raise ImageBindError("owner")
        if task.status in _TERMINAL:
            raise ImageBindError("terminal")
        rows: list[TaskImage] = []
        for image in images:
            digest = hashlib.sha256(image.data).hexdigest()
            key = f"task-image/{int(user_id)}/{int(task_run_id)}/{uuid.uuid4().hex}"
            row = TaskImage(
                user_id=int(user_id),
                task_run_id=int(task_run_id),
                media_type=image.media_type,
                byte_length=len(image.data),
                sha256=digest,
                storage_key=key,
                purge_state="staged",
            )
            db.add(row)
            rows.append(row)
        db.commit()
        return tuple(
            ReservedImage(
                id=int(row.id),
                user_id=int(row.user_id),
                task_run_id=int(row.task_run_id),
                storage_key=str(row.storage_key),
            )
            for row in rows
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finish_reserved(reserved: Sequence[ReservedImage]) -> bool:
    if not reserved:
        return True
    db = SessionLocal()
    try:
        set_rls_context(db, reserved[0].user_id)
        ids = [item.id for item in reserved]
        running = select(TaskRun.id).where(
            TaskRun.id == int(reserved[0].task_run_id),
            TaskRun.user_id == int(reserved[0].user_id),
            TaskRun.status.notin_(_TERMINAL),
        )
        result = db.execute(
            update(TaskImage)
            .where(TaskImage.id.in_(ids))
            .where(TaskImage.purge_state == "staged")
            .where(TaskImage.task_run_id.in_(running))
            .values(purge_state="ready")
        )
        if int(result.rowcount or 0) == len(ids):
            db.commit()
            return True
        ready = db.execute(
            select(TaskImage.id).where(
                TaskImage.id.in_(ids),
                TaskImage.purge_state == "ready",
                TaskImage.task_run_id.in_(running),
            )
        ).all()
        if len(ready) == len(ids):
            db.commit()
            return True
        db.execute(
            update(TaskImage)
            .where(TaskImage.id.in_(ids))
            .where(TaskImage.purge_state == "staged")
            .values(purge_state="pending_delete")
        )
        db.commit()
        return False
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def release_reserved(reserved: Sequence[ReservedImage], store: BlobStore) -> None:
    if not reserved:
        return
    ids = [item.id for item in reserved]
    db = SessionLocal()
    try:
        set_rls_context(db, reserved[0].user_id)
        db.execute(
            update(TaskImage)
            .where(TaskImage.id.in_(ids))
            .where(TaskImage.purge_state == "staged")
            .values(purge_state="tombstone")
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    purge_pending(user_id=reserved[0].user_id, store=store)
    for item in reserved:
        _drop_if_unreferenced(store, item.storage_key)


def _bind_failure(reserved: Sequence[ReservedImage]) -> str:
    db = SessionLocal()
    try:
        set_rls_context(db, reserved[0].user_id)
        task = db.get(TaskRun, reserved[0].task_run_id)
    finally:
        db.close()
    if task is not None and task.status in _TERMINAL:
        return "terminal"
    return "expired"


def mark_pending_delete(db, *, user_id: int, task_run_id: int) -> int:
    ready = db.execute(
        update(TaskImage)
        .where(TaskImage.user_id == int(user_id))
        .where(TaskImage.task_run_id == int(task_run_id))
        .where(TaskImage.purge_state == "ready")
        .values(purge_state="pending_delete")
    )
    staged = db.execute(
        update(TaskImage)
        .where(TaskImage.user_id == int(user_id))
        .where(TaskImage.task_run_id == int(task_run_id))
        .where(TaskImage.purge_state == "staged")
        .values(purge_state="tombstone")
    )
    return int(ready.rowcount or 0) + int(staged.rowcount or 0)


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


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _running_task(task_run_id: int):
    return select(TaskRun.id).where(
        TaskRun.id == int(task_run_id),
        TaskRun.status.notin_(_TERMINAL),
    )


def _staged_update(image_id: int):
    return (
        update(TaskImage)
        .where(TaskImage.id == int(image_id))
        .where(TaskImage.purge_state == "staged")
    )


def _fence_staged(image_id: int, task_run_id: int, *, mode: str) -> None:
    db = maintenance_session()
    try:
        running = _running_task(task_run_id)
        if mode == "adopt":
            db.execute(
                _staged_update(image_id)
                .where(TaskImage.task_run_id.in_(running))
                .values(purge_state="ready")
            )
            db.execute(
                _staged_update(image_id)
                .where(not_(TaskImage.task_run_id.in_(running)))
                .values(purge_state="tombstone")
            )
        elif mode == "terminal":
            db.execute(
                _staged_update(image_id)
                .where(not_(TaskImage.task_run_id.in_(running)))
                .values(purge_state="tombstone")
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _retire_ready_rows() -> None:
    db = maintenance_session()
    try:
        terminal = select(TaskRun.id).where(TaskRun.status.in_(_TERMINAL))
        db.execute(
            update(TaskImage)
            .where(TaskImage.purge_state == "ready")
            .where(TaskImage.task_run_id.in_(terminal))
            .values(purge_state="pending_delete")
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reconcile_task_images(*, now: datetime | None = None, store: BlobStore | None = None) -> int:
    moment = _aware(now or datetime.now(timezone.utc))
    db = maintenance_session()
    try:
        rows = db.execute(
            select(
                TaskImage.id,
                TaskImage.task_run_id,
                TaskImage.storage_key,
                TaskImage.created_at,
            ).where(TaskImage.purge_state == "staged")
        ).all()
        staged = [
            (int(row.id), int(row.task_run_id), str(row.storage_key), _aware(row.created_at or moment))
            for row in rows
        ]
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    blob = store
    for image_id, task_run_id, key, created in staged:
        if moment - created < STAGED_HOLD:
            _fence_staged(image_id, task_run_id, mode="terminal")
            continue
        if blob is None:
            blob = _default_store()
        if blob.exists(key):
            _fence_staged(image_id, task_run_id, mode="adopt")
        else:
            _fence_staged(image_id, task_run_id, mode="terminal")
    _retire_ready_rows()
    return _purge_all(store=blob)


def purge_pending(*, user_id: int, store: BlobStore | None = None) -> int:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        rows = (
            db.query(TaskImage)
            .filter(
                TaskImage.user_id == int(user_id),
                TaskImage.purge_state.in_(("pending_delete", "tombstone")),
            )
            .all()
        )
        if not rows:
            return 0
        return _delete_pending_rows(db, rows, store if store is not None else _default_store())
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _purge_all(*, store: BlobStore | None) -> int:
    db = maintenance_session()
    try:
        rows = db.query(TaskImage).filter(
            TaskImage.purge_state.in_(("pending_delete", "tombstone"))
        ).all()
        if not rows:
            return 0
        return _delete_pending_rows(db, rows, store if store is not None else _default_store())
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _delete_pending_rows(db, rows: Sequence[TaskImage], store: BlobStore) -> int:
    removed = 0
    for row in rows:
        key = row.storage_key
        image_id = int(row.id)
        state = row.purge_state
        try:
            if store.exists(key):
                store.delete(key)
            if store.exists(key):
                continue
        except Exception:
            logger.exception("task image blob delete failed key=%s", key)
            continue
        if state != "pending_delete":
            continue
        result = db.execute(
            delete(TaskImage).where(
                TaskImage.id == image_id,
                TaskImage.purge_state == "pending_delete",
            )
        )
        if int(result.rowcount or 0) != 1:
            db.rollback()
            continue
        db.commit()
        removed += 1
    return removed


def _drop_if_unreferenced(store: BlobStore, key: str) -> None:
    db = maintenance_session()
    try:
        referenced = db.query(TaskImage.id).filter(TaskImage.storage_key == key).count()
    finally:
        db.close()
    if referenced:
        return
    try:
        if store.exists(key):
            store.delete(key)
    except Exception:
        logger.exception("task image blob delete failed key=%s", key)


def _default_store() -> BlobStore:
    from app.storage import get_blob_store

    return get_blob_store()
