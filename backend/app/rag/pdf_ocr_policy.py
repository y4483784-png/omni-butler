"""PDF page OCR decision: OCR when the page has images (or almost no useful text)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings

_USEFUL_RE = re.compile(r"[\w\u4e00-\u9fff]", re.UNICODE)


@dataclass
class PageSignals:
    page_index: int  # 0-based
    useful_chars: int
    image_count: int
    image_area_ratio: float
    reason: str = ""


def useful_char_count(text: str) -> int:
    return len(_USEFUL_RE.findall(text or ""))


def collect_page_signals(page, page_index: int) -> PageSignals:
    """Collect local signals from a PyMuPDF page (no API calls)."""
    native = page.get_text("text") or ""
    useful = useful_char_count(native)

    images = page.get_images(full=True) or []
    image_count = len(images)

    page_area = abs(float(page.rect.width * page.rect.height)) or 1.0
    img_area = 0.0
    for info in page.get_image_info(xrefs=True) or []:
        bbox = info.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x0, y0, x1, y1 = bbox[:4]
        img_area += max(0.0, float(x1 - x0)) * max(0.0, float(y1 - y0))
    ratio = min(1.0, img_area / page_area)

    return PageSignals(
        page_index=page_index,
        useful_chars=useful,
        image_count=image_count,
        image_area_ratio=ratio,
    )


def decide_ocr(signals: PageSignals, *, min_useful: int | None = None) -> tuple[bool, str]:
    """Return (need_ocr, reason).

    Primary: any embedded image → OCR.
    Fallback: no images but almost no useful text → OCR.
    """
    threshold = settings.ocr_min_useful_chars if min_useful is None else min_useful
    if signals.image_count >= 1:
        return True, f"有图({signals.image_count})"
    if signals.useful_chars < threshold:
        return True, f"贫文本(useful={signals.useful_chars}<{threshold})"
    return False, "纯文本充足"


def merge_native_and_ocr(native: str, ocr: str) -> str:
    """Prefer keeping both layers; drop exact duplicate lines."""
    native = (native or "").strip()
    ocr = (ocr or "").strip()
    if not ocr:
        return native
    if not native:
        return ocr
    native_lines = {ln.strip() for ln in native.splitlines() if ln.strip()}
    extra = [ln for ln in ocr.splitlines() if ln.strip() and ln.strip() not in native_lines]
    if not extra:
        # OCR may be a single block; if longer and not subset, append whole OCR
        if ocr not in native and len(ocr) > len(native) * 0.5:
            return native + "\n\n" + ocr
        return native
    return native + "\n\n" + "\n".join(extra)
