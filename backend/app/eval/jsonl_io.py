"""JSONL writes that stay LF, and newline-insensitive dataset comparison.

A Windows host or HGFS share often stores the working tree as CRLF even when
git's canonical bytes are LF. Generators on Linux emit LF. Byte-for-byte
idempotency then fails on the carriage return alone. Writers here pass
``newline`` so Python does not translate LF to ``os.linesep``, and comparisons
fold CRLF / bare CR to LF so a checkout's newlines are not a false drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize_newlines(data: bytes) -> bytes:
    """Map CRLF and bare CR to LF. Every other byte stays put."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write one JSON object per line, always LF, with a trailing newline."""
    text = (
        "\n".join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows) + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" keeps LF on Windows; the default would translate to os.linesep.
    path.write_text(text, encoding="utf-8", newline="\n")
