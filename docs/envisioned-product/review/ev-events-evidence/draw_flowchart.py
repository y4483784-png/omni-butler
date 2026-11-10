#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_INDEX = 2
OUT = Path(__file__).resolve().parents[1] / "ev-events-flowchart.png"

BLUE = (195, 224, 255)
BLUE_EDGE = (44, 93, 146)
TEAL = (191, 227, 223)
TEAL_EDGE = (46, 107, 102)
ORANGE = (240, 211, 191)
ORANGE_EDGE = (136, 73, 44)
INK = (30, 30, 30)
WHITE = (255, 255, 255)

WIDTH = 2480
HEIGHT = 2280
MAIN = 700
WAIT = 1480
SIDE = 2140
DH = 105


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


def label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, face: ImageFont.FreeTypeFont) -> None:
    if "，" in text or "," in text:
        raise SystemExit(f"split the phrase before drawing: {text}")
    draw.text((x, y), text, font=face, fill=INK)


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    body = font(32)
    small = font(26)

    def box(cx: int, cy: int, lines: list[str], fill, edge, width: int, height: int) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w + 48 > width or need_h + 24 > height:
            raise SystemExit(f"text does not fit: {lines} need {need_w}x{need_h} box {width}x{height}")
        left = cx - width // 2
        top = cy - height // 2
        rounded(draw, (left, top, left + width, top + height), fill, edge)
        center_text(draw, cx, cy, lines, body)

    def ask(cx: int, cy: int, lines: list[str], width: int = 760, height: int = 210) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w > width * 0.48 or need_h > height * 0.42:
            raise SystemExit(f"diamond text does not fit: {lines} need {need_w}x{need_h}")
        diamond(draw, cx, cy, width, height, TEAL, TEAL_EDGE)
        center_text(draw, cx, cy, lines, body)

    start, d1, d2, d3, d4, d5, d6 = 90, 310, 580, 850, 1120, 1390, 1660
    through, finger, again, reissue = 1960, 1660, 1960, 2200
    box(MAIN, start, ["内部路由收下一条事件"], BLUE, BLUE_EDGE, 520, 96)
    ask(MAIN, d1, ["这一投递已经有回执吗"])
    box(SIDE, d1, ["什么都不做", "还是原来那一条回执"], ORANGE, ORANGE_EDGE, 460, 120)
    ask(MAIN, d2, ["程序规则算不算噪声"])
    box(SIDE, d2, ["只记下已接收", "不开任务也不出卡"], ORANGE, ORANGE_EDGE, 460, 120)
    ask(MAIN, d3, ["模型决定要不要动"])
    box(SIDE, d3, ["只记下已接收", "不执行"], ORANGE, ORANGE_EDGE, 460, 120)
    ask(MAIN, d4, ["这一代是不是已经停止"])
    box(SIDE, d4, ["不写", "也不标外部成功"], ORANGE, ORANGE_EDGE, 460, 120)
    ask(MAIN, d5, ["这一代做完了", "或还没有任务吗"])
    box(SIDE, d5, ["另开新任务", "回执改为已派活"], BLUE, BLUE_EDGE, 460, 120)
    ask(MAIN, d6, ["这一代是不是正在等人"])
    box(MAIN, through, ["进入这一代且不另开派活", "回执仍是已接收"], BLUE, BLUE_EDGE, 560, 120)

    ask(WAIT, finger, ["指纹和打开的卡相同吗"])
    box(SIDE, 1780, ["保留已经打开的那张卡"], BLUE, BLUE_EDGE, 460, 96)
    ask(WAIT, again, ["已经重签过", "或这次没有新卡吗"])
    box(SIDE, 2080, ["只排队", "不执行也不再开卡"], ORANGE, ORANGE_EDGE, 460, 120)
    box(WAIT, reissue, ["作废旧卡并只重签一张"], BLUE, BLUE_EDGE, 520, 96)

    arrow(draw, MAIN, start + 48, MAIN, d1 - DH)
    arrow(draw, MAIN + 380, d1, SIDE - 230, d1)
    arrow(draw, MAIN, d1 + DH, MAIN, d2 - DH)
    arrow(draw, MAIN + 380, d2, SIDE - 230, d2)
    arrow(draw, MAIN, d2 + DH, MAIN, d3 - DH)
    arrow(draw, MAIN + 380, d3, SIDE - 230, d3)
    arrow(draw, MAIN, d3 + DH, MAIN, d4 - DH)
    arrow(draw, MAIN + 380, d4, SIDE - 230, d4)
    arrow(draw, MAIN, d4 + DH, MAIN, d5 - DH)
    arrow(draw, MAIN + 380, d5, SIDE - 230, d5)
    arrow(draw, MAIN, d5 + DH, MAIN, d6 - DH)
    arrow(draw, MAIN + 380, d6, WAIT - 380, finger)
    arrow(draw, MAIN, d6 + DH, MAIN, through - 60)

    arrow(draw, WAIT + 380, finger, SIDE - 230, 1780)
    arrow(draw, WAIT, finger + DH, WAIT, again - DH)
    arrow(draw, WAIT + 380, again, SIDE - 230, 2080)
    arrow(draw, WAIT, again + DH, WAIT, reissue - 48)

    label(draw, MAIN + 16, start + 70, "否", small)
    label(draw, MAIN + 400, d1 - 34, "是", small)
    label(draw, MAIN + 16, d1 + DH + 8, "否", small)
    label(draw, MAIN + 400, d2 - 34, "是", small)
    label(draw, MAIN + 16, d2 + DH + 8, "要", small)
    label(draw, MAIN + 400, d3 - 34, "不要", small)
    label(draw, MAIN + 16, d3 + DH + 8, "否", small)
    label(draw, MAIN + 400, d4 - 34, "是", small)
    label(draw, MAIN + 16, d4 + DH + 8, "否", small)
    label(draw, MAIN + 400, d5 - 34, "是", small)
    label(draw, MAIN + 16, d5 + DH + 8, "否", small)
    label(draw, MAIN + 400, d6 - 34, "是", small)
    label(draw, MAIN + 16, d6 + DH + 8, "否", small)
    label(draw, WAIT + 16, finger + DH + 8, "否", small)
    label(draw, WAIT + 400, finger - 34, "是", small)
    label(draw, WAIT + 16, again + DH + 8, "否", small)
    label(draw, WAIT + 400, again - 34, "是", small)

    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
