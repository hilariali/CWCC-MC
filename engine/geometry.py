"""Bubble geometry for CWCCC answer sheets, defined in reference-sheet pixels.

Boxes (COL*/ROW_Y) are given in the pixel frame of the reference scan at ~150 dpi
(1275x1650). They are never used directly on a new page: ``engine.layout`` maps
them through a homography computed from the printed answer-frame landmarks
(``REF_FRAME``) detected on each page, then snaps each row to the printed bubble
outlines.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_GEOM_PATH = Path(__file__).with_name("geometry.json")

LETTERS = ("A", "B", "C", "D")
# Sheet layout: 4 columns of 25 (Q1–25, 26–50, 51–75, 76–100).
MAX_QUESTIONS = 100
ROWS_PER_COL = 25
DPI = 150
CALIB_WIDTH = 1275
CALIB_HEIGHT = 1650

# Back-compat alias
NUM_QUESTIONS = 40


def load_geometry(path: Path | None = None) -> dict[str, Any]:
    p = path or _GEOM_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("COL1", "COL2", "ROW_Y"):
        if key not in data:
            raise ValueError(f"geometry missing {key}")
    if len(data["ROW_Y"]) < ROWS_PER_COL:
        raise ValueError("ROW_Y must have 25 row bands")
    if len(data["COL1"]) != 4 or len(data["COL2"]) != 4:
        raise ValueError("COL1/COL2 must each have 4 [x0,x1] pairs for A–D")
    # Derive columns 3/4 from the column pitch (frame dividers are evenly spaced).
    for k in (3, 4):
        name = f"COL{k}"
        if name not in data:
            data[name] = [
                [c2[0] + (c2[0] - c1[0]) * (k - 2), c2[1] + (c2[1] - c1[1]) * (k - 2)]
                for c1, c2 in zip(data["COL1"], data["COL2"])
            ]
    data.setdefault("REF_FRAME", [[340.3, 604.0], [1201.8, 601.2], [1202.4, 1603.1], [341.7, 1606.6]])
    return data


def scale_geometry(geometry: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Legacy fallback: scale reference boxes to the page size (no landmarks)."""
    sx = width / float(CALIB_WIDTH)
    sy = height / float(CALIB_HEIGHT)
    if abs(sx - 1.0) < 0.01 and abs(sy - 1.0) < 0.01:
        return geometry
    out = copy.deepcopy(geometry)

    def sx_pair(pair: list[float]) -> list[float]:
        return [pair[0] * sx, pair[1] * sx]

    for k in ("COL1", "COL2", "COL3", "COL4"):
        if k in geometry:
            out[k] = [sx_pair(p) for p in geometry[k]]
    out["ROW_Y"] = [[p[0] * sy, p[1] * sy] for p in geometry["ROW_Y"]]
    if "BUBBLES" in geometry:
        out["BUBBLES"] = {q: [[b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy] for b in bs]
                          for q, bs in geometry["BUBBLES"].items()}
    out["REF_FRAME"] = [[x * sx, y * sy] for x, y in geometry["REF_FRAME"]]
    out["_scale"] = {"sx": sx, "sy": sy, "width": width, "height": height}
    return out


def bubble_box(geometry: dict[str, Any], question: int) -> tuple[list[list[float]], list[float]]:
    """Reference-frame (col_x_pairs A–D, [y0,y1]) for a 1-based question."""
    if not 1 <= question <= MAX_QUESTIONS:
        raise ValueError(f"question out of range: {question} (sheet supports 1–{MAX_QUESTIONS})")
    col = (question - 1) // ROWS_PER_COL + 1
    row = (question - 1) % ROWS_PER_COL
    return geometry[f"COL{col}"], geometry["ROW_Y"][row]


def ref_boxes(geometry: dict[str, Any], question: int) -> list[list[float]]:
    """Reference-frame [x0,y0,x1,y1] boxes for A–D of one question."""
    b = geometry.get("BUBBLES", {}).get(str(question))
    if b:
        return [list(map(float, bb)) for bb in b]
    cols, (y0, y1) = bubble_box(geometry, question)
    return [[float(x0), float(y0), float(x1), float(y1)] for x0, x1 in cols]


def expand_box(x0: float, y0: float, x1: float, y1: float, pad: int = 2) -> tuple[int, int, int, int]:
    return int(round(x0 - pad)), int(round(y0 - pad)), int(round(x1 + pad)), int(round(y1 + pad))
