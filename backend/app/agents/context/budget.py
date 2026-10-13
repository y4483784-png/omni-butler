"""Token budget estimator and watermarks for ContextManager."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.core.config import settings

BudgetLevel = Literal["safe", "warn", "compact", "emergency"]


def estimate_tokens(text: str) -> int:
    """Approximate tokens for mixed CJK/Latin text (Zhipu ~1.6 chars/token)."""
    raw = text or ""
    if not raw:
        return 0
    cjk = 0
    other = 0
    for ch in raw:
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0xF900 <= code <= 0xFAFF
            or 0x3000 <= code <= 0x303F
            or 0xFF00 <= code <= 0xFFEF
        ):
            cjk += 1
        else:
            other += 1
    cjk_div = max(0.1, float(settings.context_cjk_chars_per_token))
    latin_div = max(0.1, float(settings.context_latin_chars_per_token))
    return int(math.ceil(cjk / cjk_div + other / latin_div))


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Sum message content tokens plus a small per-message structure overhead."""
    total = 0
    for msg in messages or []:
        total += estimate_tokens(str(msg.get("content") or "")) + 4
    return total


@dataclass
class TokenBudget:
    max_tokens: int
    reserve_ratio: float = 0.25
    warn_ratio: float = 0.70
    compact_ratio: float = 0.85
    emergency_ratio: float = 0.95

    @classmethod
    def from_settings(cls) -> TokenBudget:
        return cls(
            max_tokens=max(1024, int(settings.context_max_tokens)),
            reserve_ratio=float(settings.context_reserve_ratio),
            warn_ratio=float(settings.context_warn_ratio),
            compact_ratio=float(settings.context_compact_ratio),
            emergency_ratio=float(settings.context_emergency_ratio),
        )

    @property
    def usable(self) -> int:
        ratio = min(0.9, max(0.0, self.reserve_ratio))
        return max(256, int(self.max_tokens * (1.0 - ratio)))

    def level(self, used: int) -> BudgetLevel:
        usable = self.usable
        if usable <= 0:
            return "emergency"
        ratio = used / usable
        if ratio >= self.emergency_ratio:
            return "emergency"
        if ratio >= self.compact_ratio:
            return "compact"
        if ratio >= self.warn_ratio:
            return "warn"
        return "safe"
