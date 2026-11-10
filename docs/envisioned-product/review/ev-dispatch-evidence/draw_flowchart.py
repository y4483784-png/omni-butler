#!/usr/bin/env python3
"""Draw the ev-dispatch flowchart. Lines are whole phrases. The font wraps nothing."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
OUT = Path(__file__).resolve().parents[1] / "ev-dispatch-flowchart.png"

BLUE = (195, 224, 255)
BLUE_EDGE = (44, 93, 146)
TEAL = (191, 227, 223)
TEAL_EDGE = (46, 107, 102)
ORANGE = (240, 211, 191)
ORANGE_EDGE = (136, 73, 44)
INK = (30, 30, 30)
WHITE = (255, 255, 255)

WIDTH = 1800
HEIGHT = 2140
CX = 900


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def text_size(draw: ImageDraw.ImageDraw, lines: list[str], face: ImageFont.FreeTypeFont) -> tuple[int, int]:
    width = 0
    height = 0
    for line in lines:
        box = draw.textbbox((0, 0), line, font=face)
        width = max(width, box[2] - box[0])
        height += box[3] - box[1] + 8
    return width, height


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, edge) -> None:
    draw.rounded_rectangle(box, radius=18, fill=fill, outline=edge, width=3)


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
    if y2 > y1 and x1 == x2:
        draw.polygon([(x2 - 8, y2 - 14), (x2 + 8, y2 - 14), (x2, y2)], fill=INK)
    elif x2 > x1:
        draw.polygon([(x2 - 14, y2 - 8), (x2 - 14, y2 + 8), (x2, y2)], fill=INK)
    elif x2 < x1:
        draw.polygon([(x2 + 14, y2 - 8), (x2 + 14, y2 + 8), (x2, y2)], fill=INK)


def label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, face: ImageFont.FreeTypeFont) -> None:
    draw.text(xy, text, font=face, fill=INK)


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    body = font(30)
    small = font(26)

    def box(cx: int, cy: int, lines: list[str], fill, edge, width: int = 520, height: int = 96) -> tuple[int, int, int, int]:
        rect = (cx - width // 2, cy - height // 2, cx + width // 2, cy + height // 2)
        rounded(draw, rect, fill, edge)
        center_text(draw, cx, cy, lines, body)
        return rect

    def dia(cx: int, cy: int, lines: list[str], width: int = 560, height: int = 150) -> None:
        diamond(draw, cx, cy, width, height, TEAL, TEAL_EDGE)
        center_text(draw, cx, cy, lines, body)

    box(CX, 80, ["用户发来一句"], BLUE, BLUE_EDGE)
    arrow(draw, CX, 128, CX, 168)
    box(CX, 216, ["记下这一代正在做"], BLUE, BLUE_EDGE)
    arrow(draw, CX, 264, CX, 304)
    box(CX, 370, ["派活观察来意，", "写下这一回合的分配"], BLUE, BLUE_EDGE, height=120)
    arrow(draw, 1160, 370, 1330, 370)
    box(1540, 370, ["不调用工具，", "不发布凭据，", "也不写日历"], ORANGE, ORANGE_EDGE, width=420, height=140)
    arrow(draw, 640, 370, 470, 370)
    box(260, 370, ["把握低", "派活自己不开卡"], ORANGE, ORANGE_EDGE, width=420, height=120)
    arrow(draw, CX, 430, CX, 490)
    dia(CX, 580, ["这一代是否", "已经停止"])
    arrow(draw, 620, 580, 450, 580)
    label(draw, (530, 548), "是", small)
    box(250, 580, ["分配可以记下，", "网关不再调用"], ORANGE, ORANGE_EDGE, width=400, height=120)
    arrow(draw, CX, 655, CX, 720)
    label(draw, (916, 670), "否", small)
    dia(CX, 810, ["分配里有", "没有工具"])
    arrow(draw, 1180, 810, 1330, 810)
    label(draw, (1204, 772), "没有", small)
    box(1540, 810, ["干活路径直接写出回答"], BLUE, BLUE_EDGE, width=460, height=96)
    arrow(draw, CX, 885, CX, 940)
    label(draw, (916, 900), "有", small)
    box(CX, 996, ["干活路径调用", "这一个工具"], BLUE, BLUE_EDGE, height=120)
    arrow(draw, CX, 1056, CX, 1110)
    dia(CX, 1200, ["这一步是不是", "要写日历"])
    arrow(draw, 1180, 1200, 1330, 1200)
    label(draw, (1204, 1162), "不是", small)
    box(1540, 1200, ["干活路径写出回答"], BLUE, BLUE_EDGE, width=420, height=96)
    arrow(draw, CX, 1275, CX, 1340)
    label(draw, (916, 1290), "是", small)
    box(CX, 1406, ["先给出确认卡，", "点击之前不写"], BLUE, BLUE_EDGE, height=120)
    arrow(draw, CX, 1466, CX, 1520)
    dia(CX, 1610, ["用户点了", "确认没有"])
    arrow(draw, 620, 1610, 450, 1610)
    label(draw, (530, 1578), "没有", small)
    box(250, 1610, ["卡片留着，", "日历不增加"], ORANGE, ORANGE_EDGE, width=400, height=120)
    arrow(draw, CX, 1685, CX, 1748)
    label(draw, (916, 1700), "点了", small)
    box(CX, 1804, ["凭据核销后写入这一次"], BLUE, BLUE_EDGE, width=560)
    arrow(draw, CX, 1852, CX, 1904)
    box(CX, 1960, ["最终回复仍由干活路径写出"], BLUE, BLUE_EDGE, width=620)

    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
