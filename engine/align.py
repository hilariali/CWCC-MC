"""Landmark-based page alignment for CWCCC OMR answer sheets.

The answer area of the sheet is enclosed by a printed pink frame with three
internal vertical dividers (5 vertical lines, top + bottom horizontal lines).
These are used as registration landmarks:

1. Normalise orientation (portrait; flip 180 deg if the answer frame is not in
   the lower part of the page).
2. Estimate skew from the pink line pattern (projection-profile search).
3. In the deskewed mask, locate the 4 frame edges (sub-pixel profile peaks).
4. Map the 4 frame corners back to the page, and build a homography from the
   reference sheet's frame corners to this page's corners.

Bubble geometry (calibrated in reference-sheet pixel coordinates) is pushed
through the homography, then each bubble row is snapped to the printed bubble
outlines with a small local search, so residual distortion is absorbed too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image


class AlignmentError(RuntimeError):
    pass


def pink_mask(rgb: np.ndarray, loose: bool = False) -> np.ndarray:
    """Printed (pink/red) sheet ink. ``loose`` also catches faint thin outlines."""
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    if loose:
        return (r - g >= 16) & (r > 150) & (r - b >= 0)
    return (r - g > 30) & (r > 140) & (r - b > 5)


def _rotate_mask(mask: np.ndarray, angle_deg: float) -> np.ndarray:
    im = Image.fromarray((mask * 255).astype(np.uint8))
    rot = im.rotate(angle_deg, resample=Image.BILINEAR, expand=False, fillcolor=0)
    return np.asarray(rot) > 96


def estimate_skew(mask: np.ndarray, max_deg: float = 5.0) -> float:
    """Angle (deg, PIL rotate convention) that makes the pink lines axis-aligned."""
    def score_on(small: np.ndarray, a: float) -> float:
        m = _rotate_mask(small, a)
        rs = m.sum(1).astype(np.float64)
        cs = m.sum(0).astype(np.float64)
        return float((rs ** 2).sum() + (cs ** 2).sum())

    s4 = mask[::4, ::4]
    s2 = mask[::2, ::2]
    best = max(np.arange(-max_deg, max_deg + 1e-9, 0.25), key=lambda a: score_on(s4, a))
    fine = max(np.arange(best - 0.3, best + 0.3 + 1e-9, 0.05), key=lambda a: score_on(s2, a))
    return float(fine)


def _peak_centroid(profile: np.ndarray, i: int, rad: int = 2) -> float:
    lo, hi = max(0, i - rad), min(len(profile), i + rad + 1)
    w = profile[lo:hi].astype(np.float64)
    if w.sum() <= 0:
        return float(i)
    return float((np.arange(lo, hi) * w).sum() / w.sum())


def _peaks(profile: np.ndarray, thr: float, min_gap: int = 15) -> list[int]:
    idx = [i for i in range(len(profile)) if profile[i] >= thr]
    groups: list[list[int]] = []
    for i in idx:
        if groups and i - groups[-1][-1] <= min_gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return [max(g, key=lambda k: profile[k]) for g in groups]


def dil_cols(mask: np.ndarray, xi: int, rad: int = 3) -> np.ndarray:
    lo, hi = max(0, xi - rad), min(mask.shape[1], xi + rad + 1)
    return mask[:, lo:hi].any(1).astype(np.float64)


@dataclass
class Frame:
    left: float
    right: float
    top: float
    bottom: float
    dividers: list[float] = field(default_factory=list)


def find_frame(mask: np.ndarray) -> Frame:
    """Find the answer-area frame in an (approximately) axis-aligned pink mask."""
    h, w = mask.shape
    cs = mask.sum(0).astype(np.float64)
    if cs.max() < 0.3 * h:
        raise AlignmentError("no long vertical frame lines found")
    vpk = _peaks(cs, 0.6 * cs.max())
    if len(vpk) < 2:
        raise AlignmentError("fewer than 2 vertical frame lines")
    xl = _peak_centroid(cs, vpk[0])
    xr = _peak_centroid(cs, vpk[-1])
    width = xr - xl
    if width < 0.45 * w:
        raise AlignmentError(f"frame too narrow ({width:.0f}px)")
    dividers = [_peak_centroid(cs, p) for p in vpk[1:-1]]

    a, b = int(xl) + 4, int(xr) - 3
    edge = max(10, int(0.06 * width))
    # Dilate vertically (+-3 rows) so thin / slightly skewed lines stay continuous.
    dil = mask.copy()
    for k in (1, 2, 3):
        dil[k:] |= mask[:-k]
        dil[:-k] |= mask[k:]
    full = dil[:, a:b].sum(1).astype(np.float64) / max(1, b - a)
    leftseg = dil[:, a:a + edge].mean(1)
    rightseg = dil[:, b - edge:b].mean(1)
    cand = (full > 0.3) & ((leftseg > 0.1) | (rightseg > 0.1))
    rows = np.where(cand)[0]
    if len(rows) < 2:
        raise AlignmentError("top/bottom frame lines not found")
    hpk = _peaks(np.where(cand, full, 0.0), 0.3, min_gap=4)
    # A frame corner: the left/right vertical lines continue on one side only.
    vcol = np.zeros(h)
    for xc in (xl, xr):
        xi = int(round(xc))
        vcol += dil_cols(mask, xi)
    vcol /= 2.0

    def cov(y0: int, y1: int) -> float:
        y0, y1 = max(0, y0), min(h, y1)
        return float(vcol[y0:y1].mean()) if y1 > y0 else 0.0

    tops = [y for y in hpk if cov(y + 6, y + 60) > 0.7 and cov(y - 60, y - 6) < 0.3]
    bots = [y for y in hpk if cov(y - 60, y - 6) > 0.7 and cov(y + 6, y + 60) < 0.3]
    if not tops or not bots:
        raise AlignmentError("frame corners (top/bottom) not found")
    hpk = [tops[0], bots[-1]]
    raw = mask[:, a:b].mean(1)
    yt = _peak_centroid(raw, int(np.argmax(raw[max(0, hpk[0] - 4):hpk[0] + 5])) + max(0, hpk[0] - 4))
    yb = _peak_centroid(raw, int(np.argmax(raw[max(0, hpk[-1] - 4):hpk[-1] + 5])) + max(0, hpk[-1] - 4))
    if yb - yt < 0.35 * h:
        raise AlignmentError(f"frame too short ({yb - yt:.0f}px)")
    return Frame(xl, xr, yt, yb, dividers)


def _rot_point(x: float, y: float, cx: float, cy: float, angle_deg: float) -> tuple[float, float]:
    """Inverse of PIL's rotate(angle) about (cx,cy): rotated-image coords -> original."""
    t = math.radians(angle_deg)
    dx, dy = x - cx, y - cy
    # PIL rotate(angle) rotates content counter-clockwise by angle (y down).
    ox = cx + dx * math.cos(t) - dy * math.sin(t)
    oy = cy + dx * math.sin(t) + dy * math.cos(t)
    return ox, oy


def _fit_line(pts: list[tuple[float, float]]) -> tuple[float, float]:
    """Robust least squares v = m*u + c over (u, v) samples."""
    if len(pts) < 3:
        raise AlignmentError("too few edge samples")
    u = np.array([p[0] for p in pts]); v = np.array([p[1] for p in pts])
    keep = np.ones(len(u), bool)
    for _ in range(3):
        m, c = np.polyfit(u[keep], v[keep], 1)
        res = np.abs(v - (m * u + c))
        new = res <= max(1.5, 2.5 * np.median(res[keep]))
        if new.sum() < 3 or (new == keep).all():
            break
        keep = new
    return float(m), float(c)


def refine_edge(mask: np.ndarray, p0: tuple[float, float], p1: tuple[float, float],
                horizontal: bool, search: int = 8, step: int = 30, band: int = 24) -> tuple[float, float]:
    """Sample the printed line between p0..p1 in strips; return (m, c) of the fitted line.

    Horizontal: y = m*x + c. Vertical: x = m*y + c.
    """
    h, w = mask.shape
    pts: list[tuple[float, float]] = []
    if horizontal:
        (x0, y0), (x1, y1) = p0, p1
        for xs in range(int(min(x0, x1)) + 25, int(max(x0, x1)) - 25 - band, step):
            xc = xs + band / 2
            yp = y0 + (y1 - y0) * (xc - x0) / (x1 - x0)
            lo, hi = max(0, int(yp) - search), min(h, int(yp) + search + 1)
            prof = mask[lo:hi, xs:xs + band].sum(1).astype(np.float64)
            if prof.size == 0 or prof.max() < 0.35 * band:
                continue
            i = int(np.argmax(prof))
            pts.append((xc, lo + _peak_centroid(prof, i, 1)))
    else:
        (x0, y0), (x1, y1) = p0, p1
        for ys in range(int(min(y0, y1)) + 25, int(max(y0, y1)) - 25 - band, step):
            yc = ys + band / 2
            xp = x0 + (x1 - x0) * (yc - y0) / (y1 - y0)
            lo, hi = max(0, int(xp) - search), min(w, int(xp) + search + 1)
            prof = mask[ys:ys + band, lo:hi].sum(0).astype(np.float64)
            if prof.size == 0 or prof.max() < 0.35 * band:
                continue
            i = int(np.argmax(prof))
            pts.append((yc, lo + _peak_centroid(prof, i, 1)))
    return _fit_line(pts)


def _intersect(hline: tuple[float, float], vline: tuple[float, float]) -> tuple[float, float]:
    mh, ch = hline  # y = mh x + ch
    mv, cv = vline  # x = mv y + cv
    y = (mh * cv + ch) / (1 - mh * mv)
    x = mv * y + cv
    return float(x), float(y)


def homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> np.ndarray:
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    _, _, vt = np.linalg.svd(np.asarray(A, dtype=np.float64))
    H = vt[-1].reshape(3, 3)
    return H / H[2, 2]


def apply_h(H: np.ndarray, x: float, y: float) -> tuple[float, float]:
    p = H @ np.array([x, y, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


@dataclass
class Alignment:
    image: Image.Image            # orientation-normalised page (portrait, upright)
    rotation: int                 # 0/90/180/270 applied before skew
    skew_deg: float
    corners: list[tuple[float, float]]  # TL, TR, BR, BL on `image`
    H: np.ndarray                 # reference px -> page px
    scale: float
    dividers_ok: bool
    info: dict[str, Any] = field(default_factory=dict)


def _detect(im: Image.Image) -> tuple[float, Frame, np.ndarray]:
    rgb = np.asarray(im.convert("RGB"))
    mask = pink_mask(rgb, loose=True)
    ang = estimate_skew(mask)
    rot = _rotate_mask(mask, ang) if abs(ang) > 1e-6 else mask
    return ang, find_frame(rot), mask


def _refined_corners(mask: np.ndarray, coarse: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], dict[str, Any]]:
    tl, tr, br, bl = coarse
    top = refine_edge(mask, tl, tr, True)
    bot = refine_edge(mask, bl, br, True)
    lef = refine_edge(mask, tl, bl, False)
    rig = refine_edge(mask, tr, br, False)
    corners = [_intersect(top, lef), _intersect(top, rig), _intersect(bot, rig), _intersect(bot, lef)]
    drift = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(corners, coarse))
    if drift > 15:
        raise AlignmentError(f"edge refinement drifted {drift:.1f}px from coarse frame")
    return corners, {"lines": {"top": top, "bottom": bot, "left": lef, "right": rig}, "refine_drift_px": drift}


def align_page(image: Image.Image, ref_corners: list[list[float]]) -> Alignment:
    """Detect landmarks and return homography from reference coords to this page."""
    im = image.convert("RGB")
    rotation = 0
    if im.width > im.height:
        im = im.rotate(90, expand=True)  # PIL: counter-clockwise
        rotation = 90
    try:
        ang, fr, _ = _detect(im)
    except AlignmentError:
        ang, fr = None, None
    # Frame must sit in the lower part of an upright sheet.
    if fr is None or (fr.top + fr.bottom) / 2 < 0.5 * im.height:
        flipped = im.rotate(180, expand=True)
        try:
            ang2, fr2, _ = _detect(flipped)
        except AlignmentError as e:
            if fr is None:
                raise AlignmentError(f"landmark frame not found: {e}") from e
            ang2, fr2 = None, None
        if fr2 is not None and (fr2.top + fr2.bottom) / 2 >= 0.5 * flipped.height:
            im, ang, fr = flipped, ang2, fr2
            rotation = (rotation + 180) % 360
        elif fr is None:
            raise AlignmentError("landmark frame not found in either orientation")
    assert fr is not None and ang is not None
    cx, cy = im.width / 2.0, im.height / 2.0
    rc = [(fr.left, fr.top), (fr.right, fr.top), (fr.right, fr.bottom), (fr.left, fr.bottom)]
    coarse = [_rot_point(x, y, cx, cy, ang) for x, y in rc]
    mask0 = pink_mask(np.asarray(im), loose=True)
    try:
        corners, rinfo = _refined_corners(mask0, coarse)
    except AlignmentError as e:
        corners, rinfo = coarse, {"refine_error": str(e)}
    H = homography([tuple(c) for c in ref_corners], corners)
    ref_w = ref_corners[1][0] - ref_corners[0][0]
    scale = (fr.right - fr.left) / ref_w
    # Divider sanity check: expect 3 roughly evenly spaced internal lines.
    div_ok = False
    if len(fr.dividers) >= 3:
        span = fr.right - fr.left
        exp = [fr.left + span * k / 4 for k in (1, 2, 3)]
        div_ok = all(min(abs(d - e) for d in fr.dividers) < 0.02 * span for e in exp)
    return Alignment(
        image=im,
        rotation=rotation,
        skew_deg=ang,
        corners=corners,
        H=H,
        scale=scale,
        dividers_ok=div_ok,
        info={"frame_deskewed": [fr.left, fr.top, fr.right, fr.bottom], "dividers": fr.dividers, **rinfo},
    )
