"""Draw red key boxes, OK/X/AMB markers, and score banner (no name overlay)."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .geometry import LETTERS, expand_box
from .layout import PageLayout


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
    reads: dict,
    score_info: dict[str, Any],
    geometry: dict[str, Any] | None = None,
    layout: PageLayout | None = None,
    key_only: bool = True,
) -> Image.Image:
    """Return annotated RGB copy of the upright page.

    Red box = correct answer. Student's pick: green box if right, blue box if wrong.
    Orange = ambiguous marks. Label to the right: OK / X / AMB / — (blank), '?' = check note.
    """
    base_img = layout.image if layout is not None else image
    im = base_img.convert("RGB").copy()
    w, h = im.size
    draw = ImageDraw.Draw(im)
    font_ok = _font(18)
    font_banner = _font(36)
    font_small = _font(14)

    for d in score_info["detail"]:
        q = d["q"]
        rd = reads.get(q)
        if rd is None or not rd.boxes:
            continue
        bx = rd.boxes
        status = d.get("status")
        y0 = min(b[1] for b in bx)
        label_x = bx[-1][2] + 8
        label_y = y0 - 5

        if key_only:
            b = bx[LETTERS.index(d["key"])]
            draw.rectangle(expand_box(*b, pad=3), outline=(220, 30, 30), width=3)
            continue
        # Student's pick
        if status in ("correct", "wrong") and d["student"] in LETTERS:
            b = bx[LETTERS.index(d["student"])]
            col = (0, 160, 60) if status == "correct" else (30, 90, 220)
            draw.rectangle(expand_box(*b, pad=6), outline=col, width=2)
        # Correct key
        b = bx[LETTERS.index(d["key"])]
        draw.rectangle(expand_box(*b, pad=3), outline=(220, 30, 30), width=3)

        if status == "ambiguous":
            for letter in d.get("marked") or []:
                mb = bx[LETTERS.index(letter)]
                draw.rectangle(expand_box(*mb, pad=6), outline=(230, 140, 20), width=3)
            draw.text((label_x, label_y), "AMB", fill=(230, 140, 20), font=font_ok)
        elif status == "correct":
            draw.text((label_x, label_y), "OK", fill=(0, 150, 40), font=font_ok)
        elif status == "blank":
            draw.text((label_x, label_y), "—", fill=(120, 120, 120), font=font_ok)
        else:
            draw.text((label_x, label_y), "X", fill=(200, 20, 20), font=font_ok)
        if d.get("note"):
            txt = {"ambiguous": "AMB", "correct": "OK", "blank": "—"}.get(status, "X")
            qx = label_x + draw.textlength(txt, font=font_ok) + 1
            draw.text((qx, label_y), "?", fill=(230, 140, 20), font=font_ok)

    if layout is not None and layout.method != "landmarks":
        draw.rectangle([10, h - 50, w - 10, h - 10], fill=(255, 235, 200))
        draw.text((20, h - 44), "WARNING: landmarks not found — fixed coordinates used, check by eye",
                  fill=(180, 60, 0), font=font_small)

    amb = int(score_info.get("ambiguous") or 0)
    banner_w, banner_h = (300, 50) if key_only else (300, 92)
    # Blank left margin beside the answer frame (keeps the header readable).
    frame_left = min(b[0] for rd in reads.values() for b in (rd.boxes or [(w, 0, w, 0)]))
    if frame_left > banner_w + 60:
        bx0, by0 = 16, int(min(b[1] for rd in reads.values() for b in rd.boxes) - 40)
    else:
        bx0, by0 = w - banner_w - 18, 12
    draw.rectangle([bx0, by0, bx0 + banner_w, by0 + banner_h], fill=(40, 40, 40))
    draw.text((bx0 + 16, by0 + 4), f"{score_info['score']} / {score_info['total']}", fill=(255, 220, 40), font=font_banner)
    if key_only:
        return im
    draw.text((bx0 + 16, by0 + 46), f"Wrong {score_info['wrong']} | Blank {score_info['blank']} | Amb {amb}",
              fill=(220, 220, 220), font=font_small)
    draw.text((bx0 + 16, by0 + 60), "Red=key  green=right  blue=wrong", fill=(200, 200, 200), font=font_small)
    draw.text((bx0 + 16, by0 + 74), "? = check (erasure/faint)", fill=(200, 200, 200), font=font_small)
    return im
