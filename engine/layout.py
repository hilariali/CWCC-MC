"""Locate every bubble on a page: landmarks -> homography -> local snap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
from PIL import Image

from .align import Alignment, AlignmentError, align_page, apply_h, pink_mask
from .geometry import LETTERS, ROWS_PER_COL, load_geometry, ref_boxes, scale_geometry

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 on the aligned page image

SNAP_RADIUS = 4


@dataclass
class PageLayout:
    image: Image.Image                 # upright page the boxes refer to
    boxes: dict[int, list[Box]]        # q -> [A,B,C,D]
    method: str                        # "landmarks" | "fallback-fixed"
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)


def _two_line_fit(prof: np.ndarray, center: float, half_range: int, spans: range) -> tuple[float, float]:
    """Best centre c (within +-half_range of `center`) where lines at c-s/2 and c+s/2 both show."""
    best = (0.0, center)
    n = len(prof)
    for s in spans:
        for dc in range(-half_range, half_range + 1):
            c = center + dc
            lo, hi = int(round(c - s / 2.0)), int(round(c + s / 2.0))
            if lo < 0 or hi >= n:
                continue
            sc = min(prof[lo], prof[hi]) * 2 + 0.25 * (prof[lo] + prof[hi])
            if sc > best[0] or (sc == best[0] and abs(c - center) < abs(best[1] - center)):
                best = (float(sc), c)
    return best[1] - center, best[0]


def _snap_rows(mask: np.ndarray, boxes: dict[int, list[Box]]) -> dict[int, tuple[int, int]]:
    """Per-question shift that centres the boxes on the printed bubble outlines."""
    h, w = mask.shape
    R = SNAP_RADIUS
    raw: dict[int, tuple[int, int, bool]] = {}
    for q, bx in boxes.items():
        # vertical: one profile across all 4 bubbles of the row
        y0 = min(b[1] for b in bx); y1 = max(b[3] for b in bx)
        yc = (y0 + y1) / 2.0
        t, u = max(0, int(yc) - 12 - R), min(h, int(yc) + 13 + R)
        prof_y = np.zeros(u - t)
        for x0, _, x1, _ in bx:
            prof_y += mask[t:u, max(0, int(x0)):min(w, int(x1) + 1)].sum(1)
        dy, sy = _two_line_fit(prof_y, yc - t, R, range(5, 10))
        # horizontal: shared dx, summed over the 4 bubbles
        best = (-1.0, 0)
        profs = []
        for x0, _, x1, _ in bx:
            a, b = max(0, int(x0) - 6 - R), min(w, int(x1) + 7 + R)
            pr = mask[max(0, int(y0 + dy) - 1):min(h, int(y1 + dy) + 2), a:b].sum(0).astype(np.float64)
            profs.append((pr, (x0 + x1) / 2.0 - a, x1 - x0))
        for dx in range(-R, R + 1):
            tot = 0.0
            for pr, c, wid in profs:
                bb = 0.0
                for s in (wid - 1, wid, wid + 1, wid + 2):
                    lo, hi = int(round(c + dx - s / 2)), int(round(c + dx + s / 2))
                    if 0 <= lo and hi < len(pr):
                        bb = max(bb, min(pr[lo], pr[hi]))
                tot += bb
            if tot > best[0] or (tot == best[0] and abs(dx) < abs(best[1])):
                best = (tot, dx)
        ok = sy > 0 and best[0] > 0
        raw[q] = (int(round(best[1])), int(round(dy)), ok)
    # Smooth: a row should move like its 5-row block (pencil can hide outlines).
    out: dict[int, tuple[int, int]] = {}
    for q, (dx, dy, ok) in raw.items():
        blk = [raw[k] for k in raw if (k - 1) // 5 == (q - 1) // 5 and raw[k][2]]
        if not blk:
            out[q] = (0, 0)
            continue
        mdx = int(np.median([b[0] for b in blk])); mdy = int(np.median([b[1] for b in blk]))
        if not ok or abs(dx - mdx) > 1 or abs(dy - mdy) > 1:
            dx, dy = mdx, mdy
        out[q] = (dx, dy)
    return out


def locate_bubbles(
    image: Image.Image,
    geometry: dict[str, Any] | None = None,
    questions: Iterable[int] | None = None,
    *,
    snap: bool = True,
) -> PageLayout:
    geo = geometry or load_geometry()
    qs = sorted(questions) if questions is not None else list(range(1, 41))
    warnings: list[str] = []
    try:
        al: Alignment = align_page(image, geo["REF_FRAME"])
        page = al.image
        H = al.H
        method = "landmarks"
        info: dict[str, Any] = {
            "rotation": al.rotation,
            "skew_deg": round(al.skew_deg, 3),
            "scale": round(al.scale, 4),
            "corners": [[round(x, 1), round(y, 1)] for x, y in al.corners],
            "dividers_ok": al.dividers_ok,
        }
        if not al.dividers_ok:
            warnings.append("column dividers not where expected — check alignment")
        if abs(al.scale - 1.0) > 0.08:
            warnings.append(f"unusual scale {al.scale:.3f} — is this the CWCCC sheet at 150 dpi?")
        if "refine_error" in al.info:
            warnings.append("edge refinement failed; using coarse frame")
    except AlignmentError as e:
        page = image.convert("RGB")
        if page.width > page.height:
            page = page.rotate(90, expand=True)
        sg = scale_geometry(geo, page.width, page.height)
        geo = sg
        H = np.eye(3)
        method = "fallback-fixed"
        info = {"error": str(e)}
        warnings.append(f"LANDMARKS NOT FOUND ({e}); used fixed coordinates — verify this page by eye")

    boxes: dict[int, list[Box]] = {}
    for q in qs:
        row: list[Box] = []
        for x0, ry0, x1, ry1 in ref_boxes(geo, q):
            pts = [apply_h(H, x, y) for x, y in ((x0, ry0), (x1, ry0), (x1, ry1), (x0, ry1))]
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            row.append((min(xs), min(ys), max(xs), max(ys)))
        boxes[q] = row

    if snap and method == "landmarks":
        mask = pink_mask(np.asarray(page.convert("RGB")), loose=True)
        shifts = _snap_rows(mask, boxes)
        boxes = {
            q: [(x0 + shifts[q][0], y0 + shifts[q][1], x1 + shifts[q][0], y1 + shifts[q][1]) for x0, y0, x1, y1 in bx]
            for q, bx in boxes.items()
        }
        info["snap_shifts"] = {str(q): list(s) for q, s in shifts.items() if s != (0, 0)}

    return PageLayout(image=page, boxes=boxes, method=method, warnings=warnings, info=info)
