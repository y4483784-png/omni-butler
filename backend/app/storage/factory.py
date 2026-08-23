"""BlobStore singleton factory."""

from __future__ import annotations

from app.storage.base import BlobStore

_store: BlobStore | None = None


def get_blob_store() -> BlobStore:
    global _store
    if _store is not None:
        return _store

    try:
        import botocore  # noqa: F401
        import boto3  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "缺少对象存储依赖 botocore/boto3。"
            "请在当前 venv 执行: pip install 'boto3>=1.34.41,<1.35'"
        ) from exc

    from app.storage.s3 import S3BlobStore

    _store = S3BlobStore()
    return _store


def reset_blob_store_for_tests() -> None:
    global _store
    _store = None
