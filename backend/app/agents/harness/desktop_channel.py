from __future__ import annotations

import hashlib
import hmac

from app.core.db import maintenance_session
from app.models.models import DesktopDevice

_SEAL = object()


class PackageProof:
    __slots__ = ("device_id", "user_id")

    def __init__(self, source: object, device_id: int | None = None, user_id: int | None = None) -> None:
        if isinstance(source, str) or source is not _SEAL:
            raise TypeError("PackageProof is not built from a client string")
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "user_id", user_id)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PackageProof) and other.device_id == self.device_id

    def __hash__(self) -> int:
        return hash(self.device_id)

    def audit_word(self) -> str:
        return "desktop"


def recognize_package(header: str | None) -> PackageProof | None:
    if not isinstance(header, str):
        return None
    presented = header.strip()
    if not presented or presented in {"desktop", "web"}:
        return None
    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    db = maintenance_session()
    try:
        row = (
            db.query(DesktopDevice)
            .filter(DesktopDevice.token_hash == digest, DesktopDevice.revoked_at.is_(None))
            .one_or_none()
        )
        if row is None:
            stored = ""
            device_id = None
            owner_id = None
        else:
            stored = row.token_hash
            device_id = row.id
            owner_id = row.user_id
    finally:
        db.close()
    if device_id is None:
        return None
    stored_bytes = stored.encode("utf-8")
    digest_bytes = digest.encode("utf-8")
    if len(stored_bytes) != len(digest_bytes) or not hmac.compare_digest(stored_bytes, digest_bytes):
        return None
    return PackageProof(_SEAL, device_id, owner_id)
