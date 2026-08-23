"""MinIO/S3 upload storage tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.storage.documents import delete_document_blob, document_exists, materialize_document
from app.storage.paths import is_legacy_absolute_path, make_object_key

boto3 = pytest.importorskip("boto3")

from app.storage.s3 import S3BlobStore  # noqa: E402


def _store_with_mock_client() -> tuple[S3BlobStore, MagicMock]:
    client = MagicMock()
    client.head_bucket.return_value = {}
    with patch("boto3.client", return_value=client):
        with patch("app.storage.s3.settings.s3_endpoint", "http://localhost:9000"):
            store = S3BlobStore()
    return store, client


def test_make_object_key_layout():
    key = make_object_key(user_id=1, filename="差旅制度.pdf")
    assert key.startswith("u1/")
    assert key.endswith(".pdf")


def test_s3_put_exists_delete():
    store, client = _store_with_mock_client()
    key = "u1/abc_notes.md"

    assert store.put(key, b"# hello") == key
    client.put_object.assert_called_once_with(
        Bucket="omni-uploads", Key=key, Body=b"# hello"
    )

    client.head_object.return_value = {}
    assert store.exists(key)
    store.delete(key)
    client.delete_object.assert_called_once_with(Bucket="omni-uploads", Key=key)


def test_s3_materialize_is_one_use_temp_file():
    store, client = _store_with_mock_client()
    client.download_file.side_effect = (
        lambda _bucket, _key, dest: Path(dest).write_bytes(b"s3data")
    )

    path = store.materialize("u1/demo.csv")
    assert path.read_bytes() == b"s3data"
    assert path.name.startswith("omni-blob-")

    store.release_materialized(path, "u1/demo.csv")
    assert not path.exists()


def test_document_helpers_use_s3_and_release_temp():
    store, client = _store_with_mock_client()
    client.head_object.return_value = {}
    client.download_file.side_effect = (
        lambda _bucket, _key, dest: Path(dest).write_bytes(b"remote")
    )

    with patch("app.storage.documents.get_blob_store", return_value=store):
        key = "u1/new.txt"
        assert document_exists(key)
        blob = materialize_document(key)
        assert blob.path.read_bytes() == b"remote"
        temp_path = blob.path
        blob.release()
        assert not temp_path.exists()

        delete_document_blob(key)
        client.delete_object.assert_called_once_with(Bucket="omni-uploads", Key=key)


def test_legacy_absolute_path_detection():
    assert is_legacy_absolute_path(r"D:\data\file.pdf")
    assert is_legacy_absolute_path("/tmp/file.pdf")
    assert not is_legacy_absolute_path("u1/abc_file.pdf")


def test_invalid_object_key_rejected():
    store, _ = _store_with_mock_client()
    with pytest.raises(ValueError):
        store.put("../escape.txt", b"bad")
