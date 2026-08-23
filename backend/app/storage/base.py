"""Blob storage protocol for user-uploaded knowledge-base files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable


@runtime_checkable
class BlobStore(Protocol):
    """Logical object store; DB holds keys, not host absolute paths."""

    def put(self, key: str, data: bytes | BinaryIO) -> str:
        """Persist bytes under key; return key."""
        ...

    def exists(self, key: str) -> bool:
        ...

    def delete(self, key: str) -> None:
        ...

    def materialize(self, key: str) -> Path:
        """Download to a short-lived local temp file."""
        ...

    def release_materialized(self, path: Path, key: str) -> None:
        """Remove the temp file created by materialize()."""
        ...


@dataclass
class MaterializedBlob:
    path: Path
    key: str
    store: BlobStore | None

    def release(self) -> None:
        if self.store is not None:
            self.store.release_materialized(self.path, self.key)
