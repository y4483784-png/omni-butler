"""Artifact persistence helpers (chart/code/document panel on assistant messages)."""

from __future__ import annotations

import re
from typing import Any

# ~1.5 MB base64 payload cap before we store metadata-only (SQLite bloat guard)
_ARTIFACT_MAX_CONTENT_CHARS = 1_500_000
_SVG_MAX_CHARS = 400_000
_TOO_LARGE_MSG = "图表过大未落库，请重新生成"

_HEADING_RE = re.compile(r"(?m)^(#{1,3})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"```(\w+)?\n([\s\S]*?)```")
_SKIP_PREFIXES = ("模型调用失败", "当前网络", "⚠️", "工具规划失败")
_SKIP_CODE_MARKERS = ("dump_chart_sidecar", "ARTIFACT_PATH", "===SUMMARY===", "【执行代码】")


def prepare_artifact_for_storage(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a JSON-serializable artifact safe for SQLite TEXT column."""
    if not artifact:
        return None
    kind = str(artifact.get("kind") or "")
    title = str(artifact.get("title") or "")
    language = str(artifact.get("language") or "")
    content = artifact.get("content")
    if content is None and artifact.get("image_base64"):
        content = f"data:image/png;base64,{artifact['image_base64']}"
    content = str(content or "")
    svg = str(artifact.get("svg") or "")
    looks_like_chart = (
        kind == "image"
        or bool(artifact.get("image_base64"))
        or content.startswith("data:image")
        or "<svg" in content[:500]
        or svg.lstrip().startswith("<")
        or language.lower() in {"png", "svg", "jpeg", "jpg", "webp"}
    )
    if looks_like_chart:
        kind = "image"
    elif not kind:
        kind = "code"

    stored: dict[str, Any] = {"kind": kind, "title": title, "language": language, "content": content}
    if kind == "image" and len(content) > _ARTIFACT_MAX_CONTENT_CHARS:
        stored["content"] = _TOO_LARGE_MSG
        stored["truncated"] = True
    if svg and len(svg) <= _SVG_MAX_CHARS:
        stored["svg"] = svg
    points = artifact.get("chart_points")
    if isinstance(points, list) and points:
        stored["chart_points"] = points[:200]
    return stored


def infer_workspace_artifact(text: str) -> dict[str, Any] | None:
    """PRD 3.5: long markdown or code (>15 lines) opens the right-hand workspace.

    Tool-produced image/code artifacts take precedence (caller skips if those exist).
    Industry: Claude Canvas / ChatGPT canvas auto-open for long answers.
    """
    t = (text or "").strip()
    if not t or any(t.startswith(p) for p in _SKIP_PREFIXES):
        return None
    if any(m in t for m in _SKIP_CODE_MARKERS):
        return None
    doc = _maybe_document_artifact(t)
    if doc:
        return doc
    return _maybe_code_artifact(t)


def _maybe_document_artifact(text: str) -> dict[str, Any] | None:
    heads = _HEADING_RE.findall(text)
    long_enough = len(text) >= 1800
    outlined = len(heads) >= 2 and len(text) >= 400
    if not (long_enough or outlined):
        return None
    title = "长文档"
    if heads:
        title = re.sub(r"[#*`]+", "", heads[0][1]).strip()[:40] or title
    return {
        "kind": "document",
        "title": title,
        "language": "markdown",
        "content": text,
    }


def _maybe_code_artifact(text: str) -> dict[str, Any] | None:
    if any(m in text for m in _SKIP_CODE_MARKERS):
        return None
    for m in _FENCE_RE.finditer(text):
        body = (m.group(2) or "").rstrip()
        lines = [ln for ln in body.splitlines() if ln.strip()] or body.splitlines()
        if len(body.splitlines()) > 15 or len(lines) > 15:
            lang = (m.group(1) or "text").strip() or "text"
            return {
                "kind": "code",
                "title": f"{lang} 代码",
                "language": lang,
                "content": body,
            }
    return None
