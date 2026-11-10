from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "verification" / "tlc" / "tla2tools.jar"

RELEASE_TAG = "v1.8.0"
MANIFEST_VERSION = "2.0 2026-11-06"
GIT_REVISION = "d8fb231de4e4b5d02c1b2ed1ac759c086f694021"
SHA256 = "e1f11b75c4b5bc4478ba0ac5cb6ae28e40a01b0016bc33e0aa97da22dfdbcd7f"


def fail(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def manifest(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as jar:
        text = jar.read("META-INF/MANIFEST.MF").decode("utf-8")
    fields: dict[str, str] = {}
    key = ""
    for line in text.splitlines():
        if line.startswith(" ") and key:
            fields[key] += line[1:]
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        key = name
        fields[key] = value.strip()
    return fields


def jar_path() -> Path:
    if not JAR.is_file():
        fail(f"missing {JAR.relative_to(ROOT)}")
    digest = sha256(JAR)
    if digest != SHA256:
        fail(f"tla2tools.jar sha256 does not match the {RELEASE_TAG} pin")
    fields = manifest(JAR)
    if fields.get("Implementation-Version") != MANIFEST_VERSION:
        fail(
            "tla2tools.jar manifest version is "
            f"{fields.get('Implementation-Version')}, expected {MANIFEST_VERSION}"
        )
    if fields.get("X-Git-Revision") != GIT_REVISION:
        fail(
            "tla2tools.jar revision is "
            f"{fields.get('X-Git-Revision')}, expected {GIT_REVISION}"
        )
    return JAR


def main() -> None:
    path = jar_path()
    print(f"path {path.relative_to(ROOT)}")
    print(f"bytes {path.stat().st_size}")
    print(f"sha256 {SHA256}")
    print(f"release {RELEASE_TAG}")
    print(f"manifest {MANIFEST_VERSION}")
    print(f"revision {GIT_REVISION}")


if __name__ == "__main__":
    main()
