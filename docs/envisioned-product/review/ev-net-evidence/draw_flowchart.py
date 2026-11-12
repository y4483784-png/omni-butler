#!/usr/bin/env python3
"""Draw the ev-net operator flowchart. Each line is a whole phrase. The font does not wrap."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_INDEX = 2
OUT = Path(__file__).resolve().parents[1] / "ev-net-flowchart.png"

BLUE = (195, 224, 255)
BLUE_EDGE = (44, 93, 146)
TEAL = (191, 227, 223)
TEAL_EDGE = (46, 107, 102)
ORANGE = (240, 211, 191)
ORANGE_EDGE = (136, 73, 44)
INK = (30, 30, 30)
WHITE = (255, 255, 255)

WIDTH = 1640
HEIGHT = 2360
MAIN = 500
SIDE = 1240


def font(size: int) -> ImageFont.FreeTypeFont:
    face = ImageFont.truetype(FONT_PATH, size, index=FONT_INDEX)
    if face.getname()[0] != "Noto Sans CJK SC":
        raise SystemExit(f"font is {face.getname()}, expected Noto Sans CJK SC")
    return face


def text_size(draw: ImageDraw.ImageDraw, lines: list[str], face: ImageFont.FreeTypeFont) -> tuple[int, int]:
    width = 0
    height = 0
    for line in lines:
        if "，" in line or "," in line:
            raise SystemExit(f"split the phrase before drawing: {line}")
        box = draw.textbbox((0, 0), line, font=face)
        width = max(width, box[2] - box[0])
        height += box[3] - box[1] + 8
    return width, height


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, edge) -> None:
    draw.rounded_rectangle(box, radius=16, fill=fill, outline=edge, width=3)


def diamond(draw: ImageDraw.ImageDraw, cx: int, cy: int, w: int, h: int, fill, edge) -> None:
    points = [(cx, cy - h // 2), (cx + w // 2, cy), (cx, cy + h // 2), (cx - w // 2, cy)]
    draw.polygon(points, fill=fill, outline=edge)
    draw.line(points + [points[0]], fill=edge, width=3)


def center_text(draw: ImageDraw.ImageDraw, cx: int, cy: int, lines: list[str], face: ImageFont.FreeTypeFont) -> None:
    _width, height = text_size(draw, lines, face)
    y = cy - height // 2
    for line in lines:
        box = draw.textbbox((0, 0), line, font=face)
        line_h = box[3] - box[1]
        line_w = box[2] - box[0]
        draw.text((cx - line_w // 2, y - box[1]), line, font=face, fill=INK)
        y += line_h + 8


def arrow(draw: ImageDraw.ImageDraw, x1: int, y1: int, x2: int, y2: int) -> None:
    draw.line((x1, y1, x2, y2), fill=INK, width=3)
    if abs(x1 - x2) <= 2 and y2 > y1:
        draw.polygon([(x2 - 8, y2 - 14), (x2 + 8, y2 - 14), (x2, y2)], fill=INK)
    elif x2 > x1:
        draw.polygon([(x2 - 14, y2 - 8), (x2 - 14, y2 + 8), (x2, y2)], fill=INK)
    else:
        draw.polygon([(x2 + 14, y2 - 8), (x2 + 14, y2 + 8), (x2, y2)], fill=INK)


def label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, face: ImageFont.FreeTypeFont) -> None:
    if "，" in text or "," in text:
        raise SystemExit(f"split the phrase before drawing: {text}")
    draw.text((x, y), text, font=face, fill=INK)


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    body = font(28)
    small = font(22)

    def box(cx: int, cy: int, lines: list[str], fill, edge, width: int, height: int) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w + 36 > width or need_h + 20 > height:
            raise SystemExit(f"text does not fit: {lines} need {need_w}x{need_h} box {width}x{height}")
        left = cx - width // 2
        top = cy - height // 2
        rounded(draw, (left, top, left + width, top + height), fill, edge)
        center_text(draw, cx, cy, lines, body)

    def ask(cx: int, cy: int, lines: list[str], width: int = 760, height: int = 190) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w > width * 0.46 or need_h > height * 0.55:
            raise SystemExit(f"diamond text does not fit: {lines} need {need_w}x{need_h}")
        diamond(draw, cx, cy, width, height, TEAL, TEAL_EDGE)
        center_text(draw, cx, cy, lines, body)

    start, allow, pro, once, stop, choose, link, shown, calendar, end = (
        110,
        340,
        580,
        760,
        1000,
        1220,
        1480,
        1700,
        1960,
        2200,
    )
    half = 95

    box(MAIN, start, ["用户发来一句话"], BLUE, BLUE_EDGE, 420, 80)
    ask(MAIN, allow, ["允许列表含网页吗"])
    box(SIDE, allow, ["改用日常模型", "且不附带搜索"], ORANGE, ORANGE_EDGE, 360, 120)
    box(MAIN, pro, ["回答请求改走专业模型", "并附带网页搜索"], BLUE, BLUE_EDGE, 520, 120)
    box(MAIN, once, ["同一轮不再调用智谱搜索"], BLUE, BLUE_EDGE, 560, 80)
    ask(MAIN, stop, ["人已经按下停止吗"])
    box(SIDE, stop, ["不再发送下一次请求", "丢掉迟到输出"], ORANGE, ORANGE_EDGE, 420, 120)
    box(MAIN, choose, ["模型自己决定要不要搜索"], BLUE, BLUE_EDGE, 560, 80)
    ask(MAIN, link, ["搜索结果带来链接了吗"])
    box(SIDE, link, ["不编造来源"], ORANGE, ORANGE_EDGE, 280, 80)
    box(MAIN, shown, ["有链接才展示引用"], BLUE, BLUE_EDGE, 460, 80)
    ask(MAIN, calendar, ["这一步要写日历吗"])
    box(SIDE, calendar, ["只出示确认卡", "且不写入"], ORANGE, ORANGE_EDGE, 360, 120)
    box(MAIN, end, ["这一轮结束"], BLUE, BLUE_EDGE, 360, 80)

    arrow(draw, MAIN, start + 40, MAIN, allow - half)
    arrow(draw, MAIN + 380, allow, SIDE - 180, allow)
    arrow(draw, MAIN, allow + half, MAIN, pro - 60)
    arrow(draw, MAIN, pro + 60, MAIN, once - 40)
    arrow(draw, MAIN, once + 40, MAIN, stop - half)
    arrow(draw, MAIN + 380, stop, SIDE - 210, stop)
    arrow(draw, MAIN, stop + half, MAIN, choose - 40)
    arrow(draw, MAIN, choose + 40, MAIN, link - half)
    arrow(draw, MAIN + 380, link, SIDE - 140, link)
    arrow(draw, MAIN, link + half, MAIN, shown - 40)
    arrow(draw, MAIN, shown + 40, MAIN, calendar - half)
    arrow(draw, MAIN + 380, calendar, SIDE - 180, calendar)
    arrow(draw, MAIN, calendar + half, MAIN, end - 40)

    label(draw, MAIN + 16, allow + half + 8, "是", small)
    label(draw, MAIN + 390, allow - 40, "否", small)
    label(draw, MAIN + 16, stop + half + 8, "否", small)
    label(draw, MAIN + 390, stop - 40, "是", small)
    label(draw, MAIN + 16, link + half + 8, "是", small)
    label(draw, MAIN + 390, link - 40, "否", small)
    label(draw, MAIN + 16, calendar + half + 8, "否", small)
    label(draw, MAIN + 390, calendar - 40, "是", small)

    image.save(OUT)
    print(OUT, image.size)


if __name__ == "__main__":
    main()
