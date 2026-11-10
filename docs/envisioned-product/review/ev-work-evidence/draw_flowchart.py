#!/usr/bin/env python3
"""Draw the ev-work flowchart. Each line is a whole phrase. The font does not wrap."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
OUT = Path(__file__).resolve().parents[1] / "ev-work-flowchart.png"

BLUE = (195, 224, 255)
BLUE_EDGE = (44, 93, 146)
TEAL = (191, 227, 223)
TEAL_EDGE = (46, 107, 102)
ORANGE = (240, 211, 191)
ORANGE_EDGE = (136, 73, 44)
INK = (30, 30, 30)
WHITE = (255, 255, 255)

WIDTH = 2100
HEIGHT = 2280
CX = 1050


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def text_size(draw: ImageDraw.ImageDraw, lines: list[str], face: ImageFont.FreeTypeFont) -> tuple[int, int]:
    width = 0
    height = 0
    for line in lines:
        box = draw.textbbox((0, 0), line, font=face)
        width = max(width, box[2] - box[0])
        height += box[3] - box[1] + 10
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
        y += line_h + 10


def arrow(draw: ImageDraw.ImageDraw, x1: int, y1: int, x2: int, y2: int) -> None:
    draw.line((x1, y1, x2, y2), fill=INK, width=3)
    if abs(x1 - x2) <= 2 and y2 > y1:
        draw.polygon([(x2 - 8, y2 - 14), (x2 + 8, y2 - 14), (x2, y2)], fill=INK)
    elif x2 > x1:
        draw.polygon([(x2 - 14, y2 - 8), (x2 - 14, y2 + 8), (x2, y2)], fill=INK)
    elif x2 < x1:
        draw.polygon([(x2 + 14, y2 - 8), (x2 + 14, y2 + 8), (x2, y2)], fill=INK)


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    body = font(32)
    small = font(28)

    def box(cx: int, cy: int, lines: list[str], fill, edge, width: int, height: int) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w + 48 > width or need_h + 28 > height:
            raise SystemExit(f"box too small for {lines}: need {need_w}x{need_h}, have {width}x{height}")
        rect = (cx - width // 2, cy - height // 2, cx + width // 2, cy + height // 2)
        rounded(draw, rect, fill, edge)
        center_text(draw, cx, cy, lines, body)

    def dia(cx: int, cy: int, lines: list[str], width: int = 640, height: int = 170) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w + 180 > width or need_h + 36 > height:
            raise SystemExit(f"diamond too small for {lines}: need {need_w}x{need_h}, have {width}x{height}")
        diamond(draw, cx, cy, width, height, TEAL, TEAL_EDGE)
        center_text(draw, cx, cy, lines, body)

    box(CX, 90, ["用户发出一句需要干活的话"], BLUE, BLUE_EDGE, 760, 100)
    arrow(draw, CX, 140, CX, 190)
    box(CX, 250, ["派活记下这一轮要用的工具"], BLUE, BLUE_EDGE, 760, 100)
    arrow(draw, 1430, 250, 1560, 250)
    box(1780, 250, ["派活不写回复", "不发凭据，也不写日历"], ORANGE, ORANGE_EDGE, 400, 130)
    arrow(draw, CX, 300, CX, 370)
    dia(CX, 470, ["这一代是不是已经停止"])
    arrow(draw, 730, 470, 560, 470)
    draw.text((600, 430), "是", font=small, fill=INK)
    box(320, 470, ["结束这一轮循环", "不再调用工具"], ORANGE, ORANGE_EDGE, 440, 130)
    arrow(draw, CX, 555, CX, 640)
    draw.text((1070, 580), "否", font=small, fill=INK)
    dia(CX, 740, ["这个名称在允许列表里吗"])
    arrow(draw, 730, 740, 560, 740)
    draw.text((600, 700), "否", font=small, fill=INK)
    box(320, 740, ["拒绝执行这个工具"], ORANGE, ORANGE_EDGE, 440, 110)
    arrow(draw, CX, 825, CX, 910)
    draw.text((1070, 850), "是", font=small, fill=INK)
    dia(CX, 1010, ["这一步要不要过确认闸"])
    arrow(draw, 1370, 1010, 1500, 1010)
    draw.text((1390, 960), "只读", font=small, fill=INK)
    box(1780, 1010, ["直接在聊天气泡里回答", "不出确认卡"], BLUE, BLUE_EDGE, 500, 140)
    arrow(draw, CX, 1095, CX, 1180)
    draw.text((1070, 1120), "要确认", font=small, fill=INK)
    box(CX, 1250, ["调用日历工具，只生成确认卡"], BLUE, BLUE_EDGE, 820, 110)
    arrow(draw, CX, 1305, CX, 1390)
    dia(CX, 1490, ["这一步的结果是不是未知写入"])
    arrow(draw, 1370, 1490, 1500, 1490)
    draw.text((1390, 1440), "是", font=small, fill=INK)
    box(1780, 1490, ["按已有核对函数查找", "不再插入"], ORANGE, ORANGE_EDGE, 500, 140)
    arrow(draw, CX, 1575, CX, 1660)
    draw.text((1070, 1600), "否", font=small, fill=INK)
    box(CX, 1730, ["没有下一步，干活路径写出最终回复"], BLUE, BLUE_EDGE, 980, 110)
    arrow(draw, CX, 1785, CX, 1860)
    box(CX, 1930, ["聊天只流出干活已经写好的回复"], BLUE, BLUE_EDGE, 900, 110)
    arrow(draw, CX, 1985, CX, 2060)
    box(CX, 2130, ["点击确认卡之后才创建一件事"], BLUE, BLUE_EDGE, 860, 110)

    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
