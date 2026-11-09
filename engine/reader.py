"""Read filled A–D bubbles from a CWCCC answer-sheet page image."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import numpy as np
from PIL import Image

from .align import pink_mask
from .geometry import LETTERS
from .layout import PageLayout, locate_bubbles

# A pixel is "ink" if it is darker than this AND not part of the printed pink sheet.
INK_GRAY = 200
# Bubble counts as marked if its ink fraction >= this (blank bubbles read ~0–0.06).
MARK_MIN_DARK = 0.09
# A second mark this strong relative to the strongest => ambiguous (no credit).
AMBIGUOUS_RATIO = 0.6
DEFAULT_BLANK_MIN_DARK = MARK_MIN_DARK
# Readings worth a human glance (still scored): residual / erased mark, or light fill.
NOTE_SECOND_MIN = 0.05
LIGHT_FILL_MAX = 0.15
# Legacy name kept for imports.
DARK_THRESHOLD = INK_GRAY

StatusHint = Literal["answered", "blank", "ambiguous"]


@dataclass
class QuestionRead:
    question: int
    choice: str | None  # A–D or None if blank / ambiguous
    darkness: dict[str, float]
    scores: dict[str, float]
    status_hint: StatusHint = "answered"
    marked: list[str] = field(default_factory=list)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    note: str = ""


def _ink(gray: np.ndarray, pink: np.ndarray, box: tuple[float, float, float, float]) -> tuple[float, float]:
    h, w = gray.shape
    x0, y0, x1, y1 = box
    a, b = max(0, int(round(x0))), min(w, int(round(x1)) + 1)
    t, u = max(0, int(round(y0))), min(h, int(round(y1)) + 1)
    if b <= a or u <= t:
        return 0.0, 0.0
    g = gray[t:u, a:b]
    p = pink[t:u, a:b]
    ink = (g < INK_GRAY) & ~p
    return float(ink.mean()), float(255.0 - g.mean())


def read_answers(
    image: Image.Image,
    geometry: dict[str, Any] | None = None,
    *,
    questions: Iterable[int] | None = None,
    blank_min_dark: float = DEFAULT_BLANK_MIN_DARK,
    mark_min_dark: float | None = None,
    layout: PageLayout | None = None,
) -> dict[int, QuestionRead]:
    """
    For each question, detect marked A–D bubbles (positions from landmarks).

    - no bubble >= threshold → blank
    - one clear mark → that letter (a weaker stray / erased mark is noted)
    - a second mark >= AMBIGUOUS_RATIO x the strongest → ambiguous (no credit)
    """
    thr = float(mark_min_dark if mark_min_dark is not None else max(blank_min_dark or 0, MARK_MIN_DARK))
    q_list = sorted(questions) if questions is not None else list(range(1, 41))
    lay = layout or locate_bubbles(image, geometry, q_list)
    rgb = np.asarray(lay.image.convert("RGB"))
    gray = np.asarray(lay.image.convert("L")).astype(np.int16)
    pink = pink_mask(rgb, loose=True)

    out: dict[int, QuestionRead] = {}
    for q in q_list:
        bx = lay.boxes[q]
        darkness: dict[str, float] = {}
        scores: dict[str, float] = {}
        for letter, box in zip(LETTERS, bx):
            d, inv = _ink(gray, pink, box)
            darkness[letter] = round(d, 4)
            scores[letter] = round(inv, 2)
        ranked = sorted(LETTERS, key=lambda L: darkness[L], reverse=True)
        top = darkness[ranked[0]]
        above = [L for L in LETTERS if darkness[L] >= thr]
        note = ""
        if top < thr:
            choice, hint, marked = None, "blank", []
            if top >= NOTE_SECOND_MIN:
                note = f"faint mark at {ranked[0]} ({top:.2f}) treated as blank"
        else:
            rivals = [L for L in above if L != ranked[0] and darkness[L] >= AMBIGUOUS_RATIO * top]
            if rivals:
                marked = [L for L in LETTERS if L == ranked[0] or L in rivals]
                choice, hint = None, "ambiguous"
            else:
                marked = [ranked[0]]
                choice, hint = ranked[0], "answered"
                weak = [L for L in LETTERS if L != ranked[0] and darkness[L] >= NOTE_SECOND_MIN]
                notes = []
                if weak:
                    notes.append("weaker mark at " + "/".join(f"{L} ({darkness[L]:.2f})" for L in weak)
                                 + f" ignored (erased?); took {ranked[0]} ({top:.2f})")
                if top < LIGHT_FILL_MAX:
                    notes.append(f"light fill at {ranked[0]} ({top:.2f}) accepted")
                note = "; ".join(notes)
        out[q] = QuestionRead(
            question=q, choice=choice, darkness=darkness, scores=scores,
            status_hint=hint, marked=marked, boxes=list(bx), note=note,
        )
    return out


def score_page(
    reads: dict[int, QuestionRead],
    key: dict[int, str],
) -> dict[str, Any]:
    detail = []
    correct = wrong = blank = ambiguous = 0
    for q in sorted(key.keys()):
        read = reads.get(q)
        hint = read.status_hint if read else "blank"
        marked = list(read.marked) if read else []
        student = read.choice if read else None
        expected = key[q]
        if hint == "ambiguous":
            status, ok = "ambiguous", False
            ambiguous += 1
            student_out = "/".join(marked)
        elif student is None:
            status, ok = "blank", False
            blank += 1
            student_out = ""
        elif student == expected:
            status, ok = "correct", True
            correct += 1
            student_out = student
        else:
            status, ok = "wrong", False
            wrong += 1
            student_out = student
        detail.append({
            "q": q, "student": student_out, "key": expected, "status": status, "ok": ok,
            "marked": marked, "note": read.note if read else "",
            "darkness": read.darkness if read else {},
        })
    return {
        "score": correct, "total": len(key), "wrong": wrong, "blank": blank,
        "ambiguous": ambiguous, "detail": detail,
    }
