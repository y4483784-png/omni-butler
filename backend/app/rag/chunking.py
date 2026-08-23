"""Structure-aware chunking with overlap (Phase 2+).

Split on headings / page markers / table & OCR blocks first, then apply
character windows to oversized segments. Schema summaries stay as one piece.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.rag.parse import ParseResult, ParsedElement

PARSER_VERSION = 4

_PAGE_RE = re.compile(r"^---\s*第\s*(\d+)\s*页\s*---\s*$")
_ROWS_BATCH_RE = re.compile(r"^---\s*rows\s*(\d+)-(\d+)\s*---\s*$", re.I)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_TABLE_RE = re.compile(r"^\[表格\s*(\d+)\]\s*$")
_OCR_IMG_RE = re.compile(r"^\[嵌入图片\s*(\d+)\s*OCR\]\s*$")
_SCHEMA_HINT = "列名（共"


@dataclass
class ChunkPiece:
    content: str
    kind: str = "text"  # text | heading | table | ocr | schema | page
    heading: str = ""
    page: int | None = None
    source: str = ""


def chunk_text(
    text: str | ParseResult,
    chunk_size: int | None = None,
    overlap: int | None = None,
    chunk_min_size: int | None = None,
) -> list[ChunkPiece]:
    size = chunk_size if chunk_size is not None else settings.chunk_size
    ov = overlap if overlap is not None else settings.chunk_overlap
    min_size = (
        chunk_min_size if chunk_min_size is not None else getattr(settings, "chunk_min_size_target", 0)
    )
    elements: list[ParsedElement] = []
    if isinstance(text, ParseResult):
        elements = text.elements or []
        text = text.text
    text = (text or "").strip()
    if not text:
        return []

    if elements:
        return _merge_small_chunks(_chunk_from_elements(elements, size, ov), min_size=min_size, max_size=size)

    # Tabular profile (before row batches) stays as one schema chunk
    if text.startswith("数据集文件名：") and _SCHEMA_HINT in text:
        if "--- rows" not in text:
            return [ChunkPiece(content=text, kind="schema", heading="数据集摘要")]
        profile, _, rest = text.partition("\n\n--- rows")
        out: list[ChunkPiece] = [ChunkPiece(content=profile.strip(), kind="schema", heading="数据集摘要")]
        if rest.strip():
            rest = "--- rows" + rest
            for seg in _split_structural(rest):
                if len(seg.content) <= size or size <= 0:
                    out.append(seg)
                else:
                    for piece in _window_split(seg.content, size, ov):
                        out.append(
                            ChunkPiece(
                                content=piece,
                                kind=seg.kind if seg.kind != "heading" else "text",
                                heading=seg.heading,
                                page=seg.page,
                                source=seg.source,
                            )
                        )
        return _merge_small_chunks(out, min_size=min_size, max_size=size)

    segments = _split_structural(text)
    out: list[ChunkPiece] = []
    for seg in segments:
        if len(seg.content) <= size or size <= 0:
            out.append(seg)
            continue
        for piece in _window_split(seg.content, size, ov):
            out.append(
                ChunkPiece(
                    content=piece,
                    kind=seg.kind if seg.kind != "heading" else "text",
                    heading=seg.heading,
                    page=seg.page,
                    source=seg.source,
                )
            )
    return _merge_small_chunks(out, min_size=min_size, max_size=size)


def _chunk_from_elements(elements: list[ParsedElement], size: int, overlap: int) -> list[ChunkPiece]:
    out: list[ChunkPiece] = []
    for el in elements:
        seg = ChunkPiece(
            content=(el.text or "").strip(),
            kind=el.type or "text",
            heading=el.heading or "",
            page=el.page,
            source=el.source or "",
        )
        if not seg.content:
            continue
        if len(seg.content) <= size or size <= 0 or seg.kind in {"schema", "table"}:
            out.append(seg)
            continue
        for piece in _window_split(seg.content, size, overlap):
            out.append(
                ChunkPiece(
                    content=piece,
                    kind=seg.kind if seg.kind != "heading" else "text",
                    heading=seg.heading,
                    page=seg.page,
                    source=seg.source,
                )
            )
    return out


def _split_structural(text: str) -> list[ChunkPiece]:
    lines = text.splitlines()
    segments: list[ChunkPiece] = []
    buf: list[str] = []
    cur_kind = "text"
    cur_heading = ""
    cur_page: int | None = None

    def flush() -> None:
        nonlocal buf, cur_kind, cur_heading, cur_page
        body = "\n".join(buf).strip()
        if body:
            segments.append(
                ChunkPiece(content=body, kind=cur_kind, heading=cur_heading, page=cur_page)
            )
        buf = []
        cur_kind = "text"

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        m_page = _PAGE_RE.match(stripped)
        if m_page:
            flush()
            cur_page = int(m_page.group(1))
            cur_kind = "page"
            cur_heading = f"第 {cur_page} 页"
            buf = [line]
            i += 1
            # absorb following lines until next structural marker
            while i < len(lines):
                nxt = lines[i].strip()
                if (
                    _PAGE_RE.match(nxt)
                    or _ROWS_BATCH_RE.match(nxt)
                    or _HEADING_RE.match(nxt)
                    or _TABLE_RE.match(nxt)
                    or _OCR_IMG_RE.match(nxt)
                ):
                    break
                buf.append(lines[i])
                i += 1
            flush()
            cur_page = int(m_page.group(1))  # keep page context for following loose text
            cur_heading = f"第 {cur_page} 页"
            continue

        m_rows = _ROWS_BATCH_RE.match(stripped)
        if m_rows:
            flush()
            start_row, end_row = m_rows.group(1), m_rows.group(2)
            cur_kind = "table"
            cur_heading = f"rows {start_row}-{end_row}"
            buf = [line]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if (
                    _PAGE_RE.match(nxt)
                    or _ROWS_BATCH_RE.match(nxt)
                    or _HEADING_RE.match(nxt)
                    or _TABLE_RE.match(nxt)
                    or _OCR_IMG_RE.match(nxt)
                ):
                    break
                buf.append(lines[i])
                i += 1
            flush()
            continue

        m_table = _TABLE_RE.match(stripped)
        if m_table:
            flush()
            cur_kind = "table"
            cur_heading = f"表格 {m_table.group(1)}"
            buf = [line]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if (
                    _PAGE_RE.match(nxt)
                    or _ROWS_BATCH_RE.match(nxt)
                    or _HEADING_RE.match(nxt)
                    or _TABLE_RE.match(nxt)
                    or _OCR_IMG_RE.match(nxt)
                ):
                    break
                buf.append(lines[i])
                i += 1
            flush()
            continue

        m_ocr = _OCR_IMG_RE.match(stripped)
        if m_ocr:
            flush()
            cur_kind = "ocr"
            cur_heading = f"嵌入图片 {m_ocr.group(1)}"
            buf = [line]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if (
                    _PAGE_RE.match(nxt)
                    or _ROWS_BATCH_RE.match(nxt)
                    or _HEADING_RE.match(nxt)
                    or _TABLE_RE.match(nxt)
                    or _OCR_IMG_RE.match(nxt)
                ):
                    break
                buf.append(lines[i])
                i += 1
            flush()
            continue

        m_h = _HEADING_RE.match(stripped)
        if m_h:
            flush()
            title = m_h.group(2).strip()
            cur_kind = "heading"
            cur_heading = title
            buf = [line]
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if (
                    _PAGE_RE.match(nxt)
                    or _ROWS_BATCH_RE.match(nxt)
                    or _HEADING_RE.match(nxt)
                    or _TABLE_RE.match(nxt)
                    or _OCR_IMG_RE.match(nxt)
                ):
                    break
                if nxt == "" and buf and buf[-1].strip() == "":
                    i += 1
                    break
                buf.append(lines[i])
                i += 1
            flush()
            cur_heading = title
            continue

        # paragraph break on blank line
        if stripped == "":
            if buf:
                flush()
                cur_kind = "text"
            i += 1
            continue

        buf.append(line)
        i += 1

    flush()
    return segments


def _window_split(text: str, size: int, overlap: int) -> list[str]:
    ov = max(0, min(overlap, size - 1)) if size > 0 else 0
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            window = text[start:end]
            for sep in ("\n\n", "\n", "。", "！", "？", ".", "!", "?"):
                idx = window.rfind(sep)
                if idx >= size // 3:
                    end = start + idx + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - ov, start + 1)
    return chunks


def _merge_small_chunks(chunks: list[ChunkPiece], *, min_size: int, max_size: int) -> list[ChunkPiece]:
    if min_size <= 0 or len(chunks) <= 1:
        return chunks

    merged = [ChunkPiece(**vars(ch)) for ch in chunks]
    i = 0
    while i < len(merged):
        cur = merged[i]
        if len(cur.content.strip()) >= min_size or cur.kind in {"schema", "table", "ocr"}:
            i += 1
            continue

        prev = merged[i - 1] if i > 0 else None
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        merged_once = False

        if nxt and _can_merge(cur, nxt, max_size=max_size):
            merged[i + 1] = _combine_chunks(cur, nxt)
            del merged[i]
            merged_once = True
        elif prev and _can_merge(prev, cur, max_size=max_size):
            merged[i - 1] = _combine_chunks(prev, cur)
            del merged[i]
            i = max(i - 1, 0)
            merged_once = True

        if not merged_once:
            i += 1

    return merged


def _can_merge(left: ChunkPiece, right: ChunkPiece, *, max_size: int) -> bool:
    if left.page is not None and right.page is not None and left.page != right.page:
        return False
    if left.kind in {"schema", "table"} and right.kind in {"schema", "table"}:
        return False
    combined_len = len(left.content.strip()) + len(right.content.strip()) + 2
    soft_cap = max_size + max(max_size // 4, 80)
    return max_size <= 0 or combined_len <= soft_cap


def _combine_chunks(left: ChunkPiece, right: ChunkPiece) -> ChunkPiece:
    heading = right.heading or left.heading
    page = left.page if left.page is not None else right.page
    source = right.source or left.source
    kind = right.kind
    if left.kind == "heading":
        kind = right.kind if right.kind != "heading" else "text"
    elif right.kind == "heading":
        kind = left.kind
    return ChunkPiece(
        content=f"{left.content.rstrip()}\n\n{right.content.lstrip()}".strip(),
        kind=kind,
        heading=heading,
        page=page,
        source=source,
    )
