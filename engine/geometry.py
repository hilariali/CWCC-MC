"""Calibrated bubble geometry for CWCCC answer sheets at ~150 dpi (1275x1650)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_GEOM_PATH = Path(__file__).with_name("geometry.json")

LETTERS = ("A", "B", "C", "D")
# Sheet layout: col1 Q1–25, col2 Q26–50. Marking uses only questions in the scheme.
MAX_QUESTIONS = 50
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
    if len(data["ROW_Y"]) < 25:
        raise ValueError("ROW_Y must have 25 row bands for Q1–25 / Q26–50")
    if len(data["COL1"]) != 4 or len(data["COL2"]) != 4:
        raise ValueError("COL1/COL2 must each have 4 [x0,x1] pairs for A–D")
    return data


def scale_geometry(geometry: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Scale calibrated boxes to the actual page pixel size."""
    sx = width / float(CALIB_WIDTH)
    sy = height / float(CALIB_HEIGHT)
    if abs(sx - 1.0) < 0.01 and abs(sy - 1.0) < 0.01:
        return geometry
    out = copy.deepcopy(geometry)

    def sx_pair(pair: list[int]) -> list[int]:
        return [int(round(pair[0] * sx)), int(round(pair[1] * sx))]

    def sy_pair(pair: list[int]) -> list[int]:
        return [int(round(pair[0] * sy)), int(round(pair[1] * sy))]

    out["COL1"] = [sx_pair(p) for p in geometry["COL1"]]
    out["COL2"] = [sx_pair(p) for p in geometry["COL2"]]
    out["ROW_Y"] = [sy_pair(p) for p in geometry["ROW_Y"]]
    out["_scale"] = {"sx": sx, "sy": sy, "width": width, "height": height}
    return out


def bubble_box(geometry: dict[str, Any], question: int) -> tuple[list[list[int]], list[int]]:
    """Return (col_x_pairs A–D, [y0,y1]) for a 1-based question 1–MAX_QUESTIONS."""
    if not 1 <= question <= MAX_QUESTIONS:
        raise ValueError(f"question out of range: {question} (sheet supports 1–{MAX_QUESTIONS})")
    if question <= 25:
        cols = geometry["COL1"]
        row = geometry["ROW_Y"][question - 1]
    else:
        cols = geometry["COL2"]
        row = geometry["ROW_Y"][question - 26]
    return cols, row


def expand_box(x0: int, y0: int, x1: int, y1: int, pad: int = 2) -> tuple[int, int, int, int]:
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad
