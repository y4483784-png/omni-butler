#!/usr/bin/env python3
"""Draw the ev-webblock flowchart. Each line is a whole phrase. The font does not wrap."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_INDEX = 2
OUT = Path(__file__).resolve().parents[1] / "ev-webblock-flowchart.png"

BLUE = (195, 224, 255)
BLUE_EDGE = (44, 93, 146)
TEAL = (191, 227, 223)
TEAL_EDGE = (46, 107, 102)
ORANGE = (240, 211, 191)
ORANGE_EDGE = (136, 73, 44)
INK = (30, 30, 30)
WHITE = (255, 255, 255)

WIDTH = 1640
HEIGHT = 1680
MAIN = 520
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

    start, d1, written, d2, d3, outcome = 110, 340, 600, 880, 1160, 1460
    half = 95

    box(MAIN, start, ["网页用户发来消息"], BLUE, BLUE_EDGE, 460, 80)
    ask(MAIN, d1, ["请求体自报成桌面吗"])
    box(SIDE, d1, ["忽略自报字段", "本机工具仍拒绝"], ORANGE, ORANGE_EDGE, 360, 120)
    box(MAIN, written, ["服务器按登录写成网页"], BLUE, BLUE_EDGE, 520, 80)
    ask(MAIN, d2, ["这次调用是本机工具吗"])
    box(SIDE, d2, ["拒绝本机工具", "确认凭据也不放行"], ORANGE, ORANGE_EDGE, 420, 120)
    ask(MAIN, d3, ["名称在白名单里吗"])
    box(SIDE, d3, ["按白名单拒绝"], ORANGE, ORANGE_EDGE, 320, 80)
    box(MAIN, outcome, ["知识库和日历照常可用"], BLUE, BLUE_EDGE, 520, 80)

    arrow(draw, MAIN, start + 40, MAIN, d1 - half)
    arrow(draw, MAIN + 380, d1, SIDE - 180, d1)
    arrow(draw, MAIN, d1 + half, MAIN, written - 40)
    arrow(draw, MAIN, written + 40, MAIN, d2 - half)
    arrow(draw, MAIN + 380, d2, SIDE - 210, d2)
    arrow(draw, MAIN, d2 + half, MAIN, d3 - half)
    arrow(draw, MAIN + 380, d3, SIDE - 160, d3)
    arrow(draw, MAIN, d3 + half, MAIN, outcome - 40)

    label(draw, MAIN + 16, d1 + half + 8, "否", small)
    label(draw, MAIN + 390, d1 - 40, "是", small)
    label(draw, MAIN + 16, d2 + half + 8, "否", small)
    label(draw, MAIN + 390, d2 - 40, "是", small)
    label(draw, MAIN + 16, d3 + half + 8, "是", small)
    label(draw, MAIN + 390, d3 - 40, "否", small)

    image.save(OUT)
    print(OUT, image.size)


if __name__ == "__main__":
    main()
