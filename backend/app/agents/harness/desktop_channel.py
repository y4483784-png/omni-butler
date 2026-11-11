from __future__ import annotations

import hmac

from app.core.config import settings

_SEAL = object()


class PackageProof:
    __slots__ = ()

    def __init__(self, source: object) -> None:
        if isinstance(source, str) or source is not _SEAL:
            raise TypeError("PackageProof is not built from a client string")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PackageProof)

    def __hash__(self) -> int:
        return hash(PackageProof)

    def audit_word(self) -> str:
        return "desktop"


def recognize_package(header: str | None) -> PackageProof | None:
    token = settings.desktop_package_token
    if not token:
        return None
    if not isinstance(header, str):
        return None
    presented = header.strip()
    if presented in {"desktop", "web"}:
        return None
    if not hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8")):
        return None
    return PackageProof(_SEAL)
