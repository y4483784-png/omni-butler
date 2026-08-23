"""Adjacency window expansion after hybrid retrieval (Sentence-Window style)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.models import Chunk, Document


@dataclass
class ExpandSeed:
    """A fused hit before adjacency expansion."""

    chunk_id: int
    document_id: int
    filename: str
    content: str
    score: float
    chunk_index: int
    heading: str = ""
    page: int | None = None
    kind: str = "text"


@dataclass
class ExpandedHit:
    """Hit after neighbor merge; citation still points at the seed chunk_id."""

    chunk_id: int
    document_id: int
    filename: str
    content: str  # expanded passage for LLM / rerank
    score: float
    heading: str = ""
    page: int | None = None
    kind: str = "text"
    seed_content: str = ""  # original hit only (for snippets)


def _neighbor_label(row: Chunk) -> str:
    parts: list[str] = []
    if (row.heading or "").strip():
        parts.append(row.heading.strip())
    if row.page is not None:
        parts.append(f"p.{row.page}")
    return " · ".join(parts) if parts else "邻接"


def _format_neighbor(row: Chunk) -> str:
    kind = (row.kind or "text").lower()
    # Avoid dumping huge schema/table neighbors into every hit.
    if kind == "schema":
        label = _neighbor_label(row)
        return f"[{label}｜schema] （表结构摘要已省略，见原命中或单独命中）"
    body = (row.content or "").strip()
    if not body:
        return ""
    label = _neighbor_label(row)
    if kind == "table" and len(body) > 800:
        body = body[:797] + "…"
    return f"[{label}] {body}"


def expand_hits(
    db: Session,
    seeds: list[ExpandSeed],
    *,
    window: int = 1,
    max_chars: int = 2400,
) -> list[ExpandedHit]:
    """Attach same-document neighbors at chunk_index ± window; cite seed chunk_id."""
    if not seeds:
        return []
    w = max(0, int(window))
    budget = max(200, int(max_chars))

    # Batch-load neighbor ranges per document
    by_doc: dict[int, list[ExpandSeed]] = {}
    for s in seeds:
        by_doc.setdefault(s.document_id, []).append(s)

    neighbors_by_doc: dict[int, dict[int, Chunk]] = {}
    for doc_id, group in by_doc.items():
        indices = {s.chunk_index for s in group}
        lo = min(indices) - w
        hi = max(indices) + w
        rows = (
            db.query(Chunk)
            .filter(
                Chunk.document_id == doc_id,
                Chunk.chunk_index >= lo,
                Chunk.chunk_index <= hi,
            )
            .all()
        )
        neighbors_by_doc[doc_id] = {int(r.chunk_index): r for r in rows}

    out: list[ExpandedHit] = []
    for seed in seeds:
        seed_body = (seed.content or "").strip()
        parts: list[str] = [f"[命中] {seed_body}" if seed_body else "[命中]"]
        used = len(parts[0])
        seen_idx = {seed.chunk_index}
        nb_map = neighbors_by_doc.get(seed.document_id) or {}

        for offset in range(1, w + 1):
            for idx in (seed.chunk_index - offset, seed.chunk_index + offset):
                if idx in seen_idx:
                    continue
                row = nb_map.get(idx)
                if row is None or row.id == seed.chunk_id:
                    continue
                seen_idx.add(idx)
                frag = _format_neighbor(row)
                if not frag:
                    continue
                piece = f"\n---\n[邻接] {frag}"
                if used + len(piece) > budget:
                    continue
                parts.append(piece)
                used += len(piece)

        out.append(
            ExpandedHit(
                chunk_id=seed.chunk_id,
                document_id=seed.document_id,
                filename=seed.filename,
                content="".join(parts),
                score=seed.score,
                heading=seed.heading,
                page=seed.page,
                kind=seed.kind,
                seed_content=seed_body,
            )
        )
    return out
