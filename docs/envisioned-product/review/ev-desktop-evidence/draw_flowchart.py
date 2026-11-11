#!/usr/bin/env python3
"""Draw the ev-desktop operator flowchart. Each line is a whole phrase."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_INDEX = 2
OUT = Path(__file__).resolve().parents[1] / "ev-desktop-flowchart.png"

BLUE = (195, 224, 255)
BLUE_EDGE = (44, 93, 146)
TEAL = (191, 227, 223)
TEAL_EDGE = (46, 107, 102)
ORANGE = (240, 211, 191)
ORANGE_EDGE = (136, 73, 44)
INK = (30, 30, 30)
WHITE = (255, 255, 255)

WIDTH = 1640
HEIGHT = 2860
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

    def ask(cx: int, cy: int, lines: list[str], width: int = 720, height: int = 180) -> None:
        need_w, need_h = text_size(draw, lines, body)
        if need_w > width * 0.46 or need_h > height * 0.55:
            raise SystemExit(f"diamond text does not fit: {lines} need {need_w}x{need_h}")
        diamond(draw, cx, cy, width, height, TEAL, TEAL_EDGE)
        center_text(draw, cx, cy, lines, body)

    open_win = 110
    ask_file = 280
    know = 520
    card = 760
    owner = 1000
    stopped = 1260
    click = 1500
    armed = 1720
    ran = 1940
    again = 2200
    later = 2460
    kept = 2700
    half = 90

    box(MAIN, open_win, ["打开桌面窗口"], BLUE, BLUE_EDGE, 460, 80)
    box(MAIN, ask_file, ["提出打开文件"], BLUE, BLUE_EDGE, 460, 80)
    ask(MAIN, know, ["认出安装包吗"])
    box(SIDE, know, ["网页拒绝这次动作"], ORANGE, ORANGE_EDGE, 460, 80)
    box(MAIN, card, ["出示确认卡"], BLUE, BLUE_EDGE, 420, 80)
    ask(MAIN, owner, ["是本人的卡吗"])
    box(SIDE, owner, ["别人不能确认"], ORANGE, ORANGE_EDGE, 400, 80)
    ask(MAIN, stopped, ["确认前停止了吗"])
    box(SIDE, stopped, ["文件没有打开"], ORANGE, ORANGE_EDGE, 400, 80)
    box(MAIN, click, ["人点击确认"], BLUE, BLUE_EDGE, 420, 80)
    box(MAIN, armed, ["先记下这次动作"], BLUE, BLUE_EDGE, 480, 80)
    box(MAIN, ran, ["在本机执行一次"], BLUE, BLUE_EDGE, 480, 80)
    ask(MAIN, again, ["再点一次吗"])
    box(SIDE, again, ["不再执行第二次"], ORANGE, ORANGE_EDGE, 440, 80)
    ask(MAIN, later, ["做完后再停止吗"])
    box(SIDE, later, ["文件仍然开着"], ORANGE, ORANGE_EDGE, 400, 80)
    box(MAIN, kept, ["动作留在本机"], BLUE, BLUE_EDGE, 420, 80)

    arrow(draw, MAIN, open_win + 40, MAIN, ask_file - 40)
    arrow(draw, MAIN, ask_file + 40, MAIN, know - half)
    arrow(draw, MAIN + 360, know, SIDE - 230, know)
    arrow(draw, MAIN, know + half, MAIN, card - 40)
    arrow(draw, MAIN, card + 40, MAIN, owner - half)
    arrow(draw, MAIN + 360, owner, SIDE - 200, owner)
    arrow(draw, MAIN, owner + half, MAIN, stopped - half)
    arrow(draw, MAIN + 360, stopped, SIDE - 200, stopped)
    arrow(draw, MAIN, stopped + half, MAIN, click - 40)
    arrow(draw, MAIN, click + 40, MAIN, armed - 40)
    arrow(draw, MAIN, armed + 40, MAIN, ran - 40)
    arrow(draw, MAIN, ran + 40, MAIN, again - half)
    arrow(draw, MAIN + 360, again, SIDE - 220, again)
    arrow(draw, MAIN, again + half, MAIN, later - half)
    arrow(draw, MAIN + 360, later, SIDE - 200, later)
    arrow(draw, MAIN, later + half, MAIN, kept - 40)

    label(draw, MAIN + 390, know - 36, "否", small)
    label(draw, MAIN + 16, know + half + 6, "是", small)
    label(draw, MAIN + 390, owner - 36, "否", small)
    label(draw, MAIN + 16, owner + half + 6, "是", small)
    label(draw, MAIN + 390, stopped - 36, "是", small)
    label(draw, MAIN + 16, stopped + half + 6, "否", small)
    label(draw, MAIN + 390, again - 36, "是", small)
    label(draw, MAIN + 16, again + half + 6, "否", small)
    label(draw, MAIN + 390, later - 36, "是", small)
    label(draw, MAIN + 16, later + half + 6, "否", small)

    image.save(OUT)
    print(OUT, image.size)


if __name__ == "__main__":
    main()
