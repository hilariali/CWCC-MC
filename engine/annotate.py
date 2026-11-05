"""Draw red key boxes, OK/X markers, and score banner (no name overlay)."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .geometry import LETTERS, bubble_box, expand_box, load_geometry
from .reader import QuestionRead


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def annotate_page(
    image: Image.Image,
    reads: dict[int, QuestionRead],
    score_info: dict[str, Any],
    geometry: dict[str, Any] | None = None,
) -> Image.Image:
    """Return annotated RGB copy. Red box = correct key bubble."""
    geo = geometry or load_geometry()
    im = image.convert("RGB").copy()
    draw = ImageDraw.Draw(im)
    font_ok = _font(18)
    font_banner = _font(36)
    font_small = _font(14)

    for d in score_info["detail"]:
        q = d["q"]
        cols, (y0, y1) = bubble_box(geo, q)
        expected = d["key"]
        li = LETTERS.index(expected)
        x0, x1 = cols[li]
        box = expand_box(x0, y0, x1, y1, pad=3)
        draw.rectangle(box, outline=(220, 30, 30), width=3)

        ok = d["ok"]
        label_x = cols[-1][1] + 8
        label_y = y0 - 2
        if ok:
            draw.text((label_x, label_y), "OK", fill=(0, 150, 40), font=font_ok)
        else:
            draw.text((label_x, label_y), "X", fill=(200, 20, 20), font=font_ok)

    w, h = im.size
    banner_w, banner_h = 260, 88
    bx0, by0 = w - banner_w - 18, 12
    bx1, by1 = bx0 + banner_w, by0 + banner_h
    draw.rectangle([bx0, by0, bx1, by1], fill=(40, 40, 40))
    score_txt = f"{score_info['score']} / {score_info['total']}"
    draw.text((bx0 + 16, by0 + 8), score_txt, fill=(255, 220, 40), font=font_banner)
    sub = f"Wrong {score_info['wrong']} | Blank {score_info['blank']}"
    draw.text((bx0 + 16, by0 + 52), sub, fill=(220, 220, 220), font=font_small)
    draw.text((bx0 + 16, by0 + 68), "Red box = correct ans", fill=(200, 200, 200), font=font_small)
    return im
