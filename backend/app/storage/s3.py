"""S3-compatible blob store (MinIO, AWS, etc.)."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.tmpdir import ephemeral_dir

logger = logging.getLogger(__name__)


class S3BlobStore:
    def __init__(self) -> None:
        import boto3

        endpoint = settings.s3_endpoint.strip()
        if not endpoint:
            raise ValueError("S3_ENDPOINT is required for upload storage")
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region or "us-east-1",
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self._bucket)
                logger.info("Created S3 bucket %s", self._bucket)
            except ClientError as exc:
                logger.warning("Could not create bucket %s: %s", self._bucket, exc)

    def _normalize_key(self, key: str) -> str:
        safe = key.replace("\\", "/").lstrip("/")
        if ".." in safe.split("/"):
            raise ValueError(f"invalid object key: {key!r}")
        return safe

    def put(self, key: str, data: bytes | BinaryIO) -> str:
        norm = self._normalize_key(key)
        body = data if isinstance(data, (bytes, bytearray)) else data.read()
        self._client.put_object(Bucket=self._bucket, Key=norm, Body=body)
        return norm

    def exists(self, key: str) -> bool:
        norm = self._normalize_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=norm)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        norm = self._normalize_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=norm)
        except ClientError:
            pass

    def materialize(self, key: str) -> Path:
        """Download an object to a one-use OS temp file.

        Callers must invoke release_materialized() (normally via
        document_local_path) after parsing or sandbox execution.
        """
        norm = self._normalize_key(key)
        suffix = Path(norm).suffix[:16]
        tmp_root = ephemeral_dir()
        fd, temp_name = tempfile.mkstemp(
            prefix="omni-blob-",
            suffix=suffix,
            dir=str(tmp_root) if tmp_root is not None else None,
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            self._client.download_file(self._bucket, norm, str(temp_path))
        except ClientError as exc:
            temp_path.unlink(missing_ok=True)
            raise FileNotFoundError("上传文件丢失") from exc
        return temp_path

    def release_materialized(self, path: Path, key: str) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
