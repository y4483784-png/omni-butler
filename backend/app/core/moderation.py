"""Basic input/output sensitive-word filter (PRD 4.2).

Operators extend the list via SENSITIVE_WORDS (comma-separated). Built-in terms
cover high-precision illegal/NSFW phrases to avoid office false positives.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from app.core.config import settings

INPUT_BLOCKED_MESSAGE = "输入包含违规内容，请修改后重试。"

# High-precision workplace denylist. Extend in env rather than bloating this tuple.
_BUILTIN_WORDS: tuple[str, ...] = (
    "海洛因",
    "冰毒",
    "摇头丸",
    "氯胺酮",
    "色情片",
    "约炮",
    "制作炸弹",
    "买卖枪支",
)

_SEP_RE = re.compile(r"[\s\u200b\u200c\u200d\u2060\-_\*\.·•]+")


def _fold(text: str) -> str:
    return _SEP_RE.sub("", text or "").casefold()


def word_list() -> list[str]:
    if not settings.sensitive_filter_enabled:
        return []
    extra = [w.strip() for w in (settings.sensitive_words or "").split(",") if w.strip()]
    builtin = list(_BUILTIN_WORDS) if settings.sensitive_use_builtin else []
    seen: set[str] = set()
    out: list[str] = []
    for raw in [*builtin, *extra]:
        word = (raw or "").strip()
        if len(word) < 2:
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    out.sort(key=len, reverse=True)
    return out


def holdback_chars() -> int:
    words = word_list()
    if not words:
        return 0
    return max(len(w) for w in words) + 2


def split_holdback(buf: str, hold: int) -> tuple[str, str]:
    if hold <= 0 or len(buf) <= hold:
        return "", buf
    return buf[:-hold], buf[-hold:]


def _word_regex(word: str) -> re.Pattern[str]:
    parts = [re.escape(ch) for ch in word]
    return re.compile(r"[\s\-_\*\.·•]*".join(parts), re.IGNORECASE)


def trim_denylist_prefixes(emit: str, words: list[str] | None = None) -> tuple[str, str]:
    """Keep suffixes that are a proper prefix of a denylist term in the hold-back."""
    terms = words if words is not None else word_list()
    if not emit or not terms:
        return emit, ""
    folded = [_fold(w) for w in terms if _fold(w)]
    if not folded:
        return emit, ""
    max_l = max(len(fw) for fw in folded)
    cut = len(emit)
    while cut > 0:
        spill = False
        limit = min(max_l, cut)
        for n in range(1, limit + 1):
            suf = _fold(emit[cut - n : cut])
            if any(fw.startswith(suf) and len(fw) > len(suf) for fw in folded):
                spill = True
                break
        if not spill:
            break
        cut -= 1
    return emit[:cut], emit[cut:]


def find_sensitive(text: str) -> list[str]:
    """Return denylist hits (original word forms), longest first."""
    if not text:
        return []
    words = word_list()
    if not words:
        return []
    folded = _fold(text)
    hits: list[str] = []
    for word in words:
        if _fold(word) in folded or _word_regex(word).search(text):
            hits.append(word)
    return hits


def contains_sensitive(text: str) -> bool:
    return bool(find_sensitive(text))


def redact_text(text: str) -> str:
    if not text:
        return text
    words = word_list()
    if not words:
        return text
    out = text
    for word in words:
        out = _word_regex(word).sub("***", out)
    return out


async def redact_token_stream(deltas: AsyncIterator[str]) -> AsyncIterator[str]:
    """Yield tokens with a short hold-back so denylist terms are redacted before emit."""
    hold = holdback_chars()
    words = word_list()
    buf = ""
    async for delta in deltas:
        buf += delta or ""
        emit, buf = split_holdback(buf, hold)
        safe, spill = trim_denylist_prefixes(emit, words)
        buf = spill + buf
        if safe:
            yield redact_text(safe)
    tail = redact_text(buf)
    if tail:
        yield tail
