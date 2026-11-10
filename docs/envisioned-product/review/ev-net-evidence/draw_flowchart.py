#!/usr/bin/env python3

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

WIDTH = 2200
HEIGHT = 1760
CX = 1100


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size, index=FONT_INDEX)


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
    if abs(x1 - x2) <= 2 and y2 > y1:
        draw.polygon([(x2 - 8, y2 - 14), (x2 + 8, y2 - 14), (x2, y2)], fill=INK)
    elif x2 > x1:
        draw.polygon([(x2 - 14, y2 - 8), (x2 - 14, y2 + 8), (x2, y2)], fill=INK)
    else:
        draw.polygon([(x2 + 14, y2 - 8), (x2 + 14, y2 + 8), (x2, y2)], fill=INK)


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    body = font(32)
    small = font(26)

    def box(cx: int, cy: int, lines: list[str], fill, edge, width: int, height: int) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w + 48 > width or need_h + 24 > height:
            raise SystemExit(f"box too small for {lines}: need {need_w}x{need_h}, have {width}x{height}")
        rect = (cx - width // 2, cy - height // 2, cx + width // 2, cy + height // 2)
        rounded(draw, rect, fill, edge)
        center_text(draw, cx, cy, lines, body)

    def dia(cx: int, cy: int, lines: list[str], width: int = 820, height: int = 190) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w + 200 > width or need_h + 40 > height:
            raise SystemExit(f"diamond too small for {lines}: need {need_w}x{need_h}, have {width}x{height}")
        diamond(draw, cx, cy, width, height, TEAL, TEAL_EDGE)
        center_text(draw, cx, cy, lines, body)

    box(CX, 90, ["这一轮开始回答"], BLUE, BLUE_EDGE, 640, 100)
    arrow(draw, CX, 140, CX, 190)
    box(CX, 250, ["回合开始时已经记下允许列表"], BLUE, BLUE_EDGE, 820, 100)
    arrow(draw, CX, 300, CX, 360)
    dia(CX, 470, ["聊天补全文档", "有没有联网字段"])
    arrow(draw, 690, 470, 520, 470)
    box(300, 470, ["没有字段", "记下缺口", "不把猜测写进请求"], ORANGE, ORANGE_EDGE, 420, 160)
    draw.text((560, 430), "没有", font=small, fill=INK)
    arrow(draw, CX, 565, CX, 630)
    draw.text((CX + 16, 575), "然后", font=small, fill=INK)
    box(CX, 690, ["模型请求只带思考开关"], BLUE, BLUE_EDGE, 720, 100)
    arrow(draw, CX, 740, CX, 800)
    dia(CX, 910, ["路由器要不要", "用网页工具"])
    arrow(draw, 1510, 910, 1680, 910)
    box(1900, 910, ["要", "调用现有的一条搜索"], BLUE, BLUE_EDGE, 420, 130)
    arrow(draw, 690, 910, 520, 910)
    box(300, 910, ["不要", "这一轮不调用搜索"], ORANGE, ORANGE_EDGE, 420, 130)
    draw.text((1560, 870), "要", font=small, fill=INK)
    draw.text((560, 870), "不要", font=small, fill=INK)
    arrow(draw, CX, 1005, CX, 1070)
    dia(CX, 1180, ["这一步是不是日历"])
    arrow(draw, 1510, 1180, 1680, 1180)
    box(1900, 1180, ["是", "出示确认卡", "网页路径不写日历"], BLUE, BLUE_EDGE, 420, 160)
    draw.text((1560, 1140), "是", font=small, fill=INK)
    arrow(draw, CX, 1275, CX, 1340)
    dia(CX, 1450, ["这一代是不是已经停止"])
    arrow(draw, 690, 1450, 520, 1450)
    box(300, 1450, ["是", "不再发送下一次请求"], ORANGE, ORANGE_EDGE, 420, 130)
    draw.text((560, 1410), "是", font=small, fill=INK)
    arrow(draw, CX, 1545, CX, 1610)
    box(CX, 1670, ["这一轮结束"], BLUE, BLUE_EDGE, 520, 100)

    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
