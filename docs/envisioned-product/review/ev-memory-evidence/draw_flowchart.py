#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_INDEX = 2
OUT = Path(__file__).resolve().parents[1] / "ev-memory-flowchart.png"

BLUE = (195, 224, 255)
BLUE_EDGE = (44, 93, 146)
TEAL = (191, 227, 223)
TEAL_EDGE = (46, 107, 102)
ORANGE = (240, 211, 191)
ORANGE_EDGE = (136, 73, 44)
INK = (30, 30, 30)
WHITE = (255, 255, 255)

WIDTH = 1640
HEIGHT = 1980
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

    def ask(cx: int, cy: int, lines: list[str], width: int = 640, height: int = 170) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w > width * 0.46 or need_h > height * 0.55:
            raise SystemExit(f"diamond text does not fit: {lines} need {need_w}x{need_h}")
        diamond(draw, cx, cy, width, height, TEAL, TEAL_EDGE)
        center_text(draw, cx, cy, lines, body)

    start, d1, d2, d3, d4 = 90, 280, 490, 700, 910
    wrote, panel, d5, d6, card = 1100, 1260, 1460, 1670, 1860
    half = 85

    box(MAIN, start, ["这一代对话结束"], BLUE, BLUE_EDGE, 360, 72)
    ask(MAIN, d1, ["这一代完成了吗"])
    box(SIDE, d1, ["不写账本", "也不调模型"], ORANGE, ORANGE_EDGE, 320, 100)
    ask(MAIN, d2, ["是持久事实吗"])
    box(SIDE, d2, ["普通问答不记"], ORANGE, ORANGE_EDGE, 280, 72)
    ask(MAIN, d3, ["模型给出答案了吗"])
    box(SIDE, d3, ["只追加待确认账本", "不开确认卡"], ORANGE, ORANGE_EDGE, 360, 100)
    ask(MAIN, d4, ["会盖住人写的值吗"])
    box(SIDE, d4, ["两边都留下", "活档案仍是人写的"], ORANGE, ORANGE_EDGE, 380, 100)
    box(MAIN, wrote, ["账本记下改前和改后", "活档案改成新值"], BLUE, BLUE_EDGE, 420, 100)
    box(MAIN, panel, ["面板同时显示两边"], BLUE, BLUE_EDGE, 380, 72)
    ask(MAIN, d5, ["到点且有日程偏好吗"])
    box(SIDE, d5, ["这一轮没有提醒卡"], ORANGE, ORANGE_EDGE, 340, 72)
    ask(MAIN, d6, ["已有未完成凭据吗"])
    box(SIDE, d6, ["不再开第二张卡"], ORANGE, ORANGE_EDGE, 320, 72)
    box(MAIN, card, ["聊天里放一张确认卡", "点击前不写日历"], BLUE, BLUE_EDGE, 420, 100)

    arrow(draw, MAIN, start + 36, MAIN, d1 - half)
    arrow(draw, MAIN + 320, d1, SIDE - 160, d1)
    arrow(draw, MAIN, d1 + half, MAIN, d2 - half)
    arrow(draw, MAIN + 320, d2, SIDE - 140, d2)
    arrow(draw, MAIN, d2 + half, MAIN, d3 - half)
    arrow(draw, MAIN + 320, d3, SIDE - 180, d3)
    arrow(draw, MAIN, d3 + half, MAIN, d4 - half)
    arrow(draw, MAIN + 320, d4, SIDE - 190, d4)
    arrow(draw, MAIN, d4 + half, MAIN, wrote - 50)
    arrow(draw, MAIN, wrote + 50, MAIN, panel - 36)
    arrow(draw, MAIN, panel + 36, MAIN, d5 - half)
    arrow(draw, MAIN + 320, d5, SIDE - 170, d5)
    arrow(draw, MAIN, d5 + half, MAIN, d6 - half)
    arrow(draw, MAIN + 320, d6, SIDE - 160, d6)
    arrow(draw, MAIN, d6 + half, MAIN, card - 50)

    label(draw, MAIN + 12, d1 + half + 4, "是", small)
    label(draw, MAIN + 330, d1 - 36, "否", small)
    label(draw, MAIN + 12, d2 + half + 4, "是", small)
    label(draw, MAIN + 330, d2 - 36, "否", small)
    label(draw, MAIN + 12, d3 + half + 4, "有", small)
    label(draw, MAIN + 330, d3 - 36, "没有", small)
    label(draw, MAIN + 12, d4 + half + 4, "不会", small)
    label(draw, MAIN + 330, d4 - 36, "会", small)
    label(draw, MAIN + 12, d5 + half + 4, "是", small)
    label(draw, MAIN + 330, d5 - 36, "否", small)
    label(draw, MAIN + 12, d6 + half + 4, "否", small)
    label(draw, MAIN + 330, d6 - 36, "是", small)

    image.save(OUT)
    print(OUT, image.size)


if __name__ == "__main__":
    main()
