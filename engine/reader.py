"""Read filled A–D bubbles from a CWCCC answer-sheet page image."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from PIL import Image

from .geometry import LETTERS, bubble_box, load_geometry, scale_geometry

# Pixels darker than this count as "ink" (pencil / pen).
DARK_THRESHOLD = 140
# If the darkest bubble is still this empty, treat as blank.
DEFAULT_BLANK_MIN_DARK = 0.0  # match calibrated classroom run; raise to treat empty bubbles as blank


@dataclass
class QuestionRead:
    question: int
    choice: str | None  # A–D or None if blank
    darkness: dict[str, float]
    scores: dict[str, float]


def _region_stats(im: Image.Image, x0: int, y0: int, x1: int, y1: int) -> tuple[float, float]:
    # Clamp to image bounds
    w, h = im.size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0
    crop = im.crop((x0, y0, x1, y1)).convert("L")
    pixels = list(crop.getdata())
    if not pixels:
        return 0.0, 0.0
    n = len(pixels)
    dark = sum(1 for p in pixels if p < DARK_THRESHOLD) / n
    inv_mean = 255.0 - (sum(pixels) / n)
    return dark, inv_mean


def read_answers(
    image: Image.Image,
    geometry: dict[str, Any] | None = None,
    *,
    questions: Iterable[int] | None = None,
    blank_min_dark: float = DEFAULT_BLANK_MIN_DARK,
) -> dict[int, QuestionRead]:
    """
    For each requested question pick the darkest A–D bubble.
    Geometry is auto-scaled to the page pixel size.
    """
    base = geometry or load_geometry()
    w, h = image.size
    geo = scale_geometry(base, w, h)
    gray_base = image.convert("RGB")
    out: dict[int, QuestionRead] = {}
    q_list = sorted(questions) if questions is not None else list(range(1, 41))

    for q in q_list:
        cols, (y0, y1) = bubble_box(geo, q)
        darkness: dict[str, float] = {}
        scores: dict[str, float] = {}
        ranked: list[tuple[float, float, str]] = []
        for letter, (x0, x1) in zip(LETTERS, cols):
            dark, inv = _region_stats(gray_base, x0, y0, x1, y1)
            darkness[letter] = dark
            scores[letter] = inv
            ranked.append((dark, inv, letter))
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        best_dark, _, best_letter = ranked[0]
        if blank_min_dark > 0 and best_dark < blank_min_dark:
            choice = None
        else:
            choice = best_letter
        out[q] = QuestionRead(
            question=q,
            choice=choice,
            darkness=darkness,
            scores=scores,
        )
    return out


def score_page(
    reads: dict[int, QuestionRead],
    key: dict[int, str],
) -> dict[str, Any]:
    detail = []
    correct = wrong = blank = 0
    questions = sorted(key.keys())
    for q in questions:
        student = reads[q].choice if q in reads else None
        expected = key[q]
        if student is None:
            status = "blank"
            ok = False
            blank += 1
        elif student == expected:
            status = "correct"
            ok = True
            correct += 1
        else:
            status = "wrong"
            ok = False
            wrong += 1
        detail.append(
            {
                "q": q,
                "student": student or "",
                "key": expected,
                "status": status,
                "ok": ok,
            }
        )
    return {
        "score": correct,
        "total": len(questions),
        "wrong": wrong,
        "blank": blank,
        "detail": detail,
    }
