#!/usr/bin/env python3
"""Smoke-test the marking engine on the sample CWCCC PDF + synthetic blank/amb."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from engine import DEFAULT_KEY, mark_pdf
from engine.geometry import LETTERS
from engine.layout import locate_bubbles
from engine.reader import read_answers, score_page
from engine.scheme import parse_marking_scheme

SAMPLE_CANDIDATES = [
    Path(
        "/home/box/agent-data/agents/0238e52f-7351-468d-b4a0-8cb21e7b3d73/attachments/"
        "df2a1b1faadf7705b3957f341f8ae66633eeae63a68d3fed74af7a3abea36bbe.pdf"
    ),
    ROOT / "data" / "jobs" / "smoke" / "input.pdf",
    ROOT / "data" / "jobs" / "smoke10" / "input.pdf",
]
# Legacy darkest-wins baseline was [16,24,24,22,15,27,25,27,32,24].
# v1.2 (fixed coords, 0.005 threshold) gave [16,23,24,22,12,25,25,26,32,24] with
# spurious ambiguities from outline noise and a missed fill (P2 Q24).
# v2.0 landmark alignment agrees with the legacy read on 399/400 answers; the one
# difference is P8 Q27 where D is crossed out over a filled B -> ambiguous (flagged).
EXPECTED = [16, 24, 24, 22, 15, 27, 25, 26, 32, 24]
EXPECTED_AMB = [0, 0, 0, 0, 0, 0, 0, 1, 0, 0]
EXPECTED_LEGACY_DARKEST = [16, 24, 24, 22, 15, 27, 25, 27, 32, 24]


def _find_sample() -> Path | None:
    for p in SAMPLE_CANDIDATES:
        if p.is_file():
            return p
    return None


def _fill_box(draw: ImageDraw.ImageDraw, box, fill=20) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle([x0 + 2, y0 + 1, x1 - 2, y1 - 1], fill=(fill, fill, fill))


def _transform(im: Image.Image, angle: float = 0.0, dx: int = 0, dy: int = 0,
               scale: float = 1.0, flip: bool = False) -> Image.Image:
    out = im
    if scale != 1.0:
        out = out.resize((int(out.width * scale), int(out.height * scale)), Image.BILINEAR)
        canvas = Image.new("RGB", im.size, (255, 255, 255))
        canvas.paste(out, ((im.width - out.width) // 2, (im.height - out.height) // 2))
        out = canvas
    if angle:
        out = out.rotate(angle, resample=Image.BILINEAR, fillcolor=(255, 255, 255))
    if dx or dy:
        canvas = Image.new("RGB", im.size, (255, 255, 255))
        canvas.paste(out, (dx, dy))
        out = canvas
    if flip:
        out = out.rotate(180)
    return out


def run_synthetic(page_png: Path) -> bool:
    """Double-mark / blank tests, plus robustness to rotation, shift, scale and flips."""
    ok = True
    key = dict(DEFAULT_KEY)
    qs = sorted(key.keys())
    with Image.open(page_png) as im0:
        base = im0.convert("RGB")
        base.load()
    lay0 = locate_bubbles(base, None, qs)
    reads0 = read_answers(base, None, questions=qs, layout=lay0)
    target_q = next((q for q in qs if reads0[q].status_hint == "answered"
                     and max(reads0[q].darkness.values()) >= 0.3), None)
    if target_q is None:
        print("SYN FAIL: no answered question on page 1")
        return False

    amb_im = base.copy()
    already = reads0[target_q].marked[0]
    other = next(L for L in LETTERS if L != already)
    _fill_box(ImageDraw.Draw(amb_im), lay0.boxes[target_q][LETTERS.index(other)], fill=10)
    d = score_page(read_answers(amb_im, None, questions=[target_q]), {target_q: key[target_q]})["detail"][0]
    print(f"SYN double-mark Q{target_q}: status={d['status']} marked={d.get('marked')}")
    if d["status"] != "ambiguous" or d["ok"]:
        print("SYN FAIL: expected ambiguous / no credit"); ok = False
    else:
        print("SYN PASS: double-mark -> ambiguous, no credit")

    blank_im = base.copy()
    dr = ImageDraw.Draw(blank_im)
    for x0, y0, x1, y1 in lay0.boxes[target_q]:
        dr.rectangle([x0 - 3, y0 - 3, x1 + 3, y1 + 3], fill=(255, 255, 255))
    db = score_page(read_answers(blank_im, None, questions=[target_q]), {target_q: key[target_q]})["detail"][0]
    print(f"SYN blank Q{target_q}: status={db['status']}")
    if db["status"] != "blank":
        print("SYN FAIL: expected blank"); ok = False
    else:
        print("SYN PASS: wiped bubbles -> blank")

    ref = [reads0[q].choice for q in qs]
    cases = {
        "rotate +1.5deg": dict(angle=1.5),
        "rotate -2.5deg": dict(angle=-2.5),
        "shift (+25,-18)px": dict(dx=25, dy=-18),
        "scale 0.96": dict(scale=0.96),
        "upside-down + 1deg": dict(angle=1.0, flip=True),
    }
    for name, kw in cases.items():
        t = _transform(base, **kw)
        lay = locate_bubbles(t, None, qs)
        got = [read_answers(t, None, questions=qs, layout=lay)[q].choice for q in qs]
        agree = sum(a == b for a, b in zip(ref, got))
        status = "PASS" if agree == len(qs) and lay.method == "landmarks" else "FAIL"
        print(f"SYN {status}: {name}: {agree}/{len(qs)} same answers "
              f"(method={lay.method}, rot={lay.info.get('rotation')}, skew={lay.info.get('skew_deg')})")
        if status == "FAIL":
            ok = False
    return ok


def main() -> int:
    sample = _find_sample()
    if sample is None:
        print("Sample PDF missing; tried:", SAMPLE_CANDIDATES, file=sys.stderr)
        return 2
    print("using sample:", sample)

    job_dir = ROOT / "data" / "jobs" / "smoke"
    if job_dir.exists():
        import shutil

        shutil.rmtree(job_dir)

    scheme = "\n".join(f"{q}:{a}" for q, a in sorted(DEFAULT_KEY.items()))
    parse_marking_scheme(scheme)  # validate
    result = mark_pdf(sample, scheme, job_dir)

    scores = [p["score"] for p in result.pages]
    amb_counts = [p.get("ambiguous", 0) for p in result.pages]
    print("scores:", scores)
    print("ambiguous_per_page:", amb_counts)
    print("zip:", result.zip_path)
    print("pages:", len(result.pages))

    ok = True
    if len(scores) != 10:
        print(f"WARN: expected 10 pages, got {len(scores)}")
        ok = False
    if scores == EXPECTED:
        print("PASS: scores match calibrated classroom run")
    else:
        print("FAIL: score mismatch")
        print(" expected:", EXPECTED)
        print(" got:     ", scores)
        ok = False

    print(f"total_ambiguous on sample: {sum(amb_counts)}")
    if amb_counts == EXPECTED_AMB:
        print("PASS: ambiguous_per_page matches v2.0 landmark run")
    else:
        print("FAIL: ambiguous mismatch")
        print(" expected:", EXPECTED_AMB)
        print(" got:     ", amb_counts)
        ok = False

    # Ensure analysis files exist
    needed = [
        "student_question_matrix.csv",
        "student_question_long.csv",
        "question_stats.csv",
        "question_who_right_wrong.csv",
        "analysis_summary.json",
        "marks_summary.csv",
    ]
    for name in needed:
        p = job_dir / "analysis" / name
        if not p.is_file():
            print("MISSING", p)
            ok = False
        else:
            print("ok", name)

    if result.zip_path and result.zip_path.is_file():
        print("zip bytes:", result.zip_path.stat().st_size)
    else:
        ok = False

    summary = json.loads((job_dir / "analysis" / "analysis_summary.json").read_text())
    print("mean_score:", summary.get("mean_score"))
    print("total_ambiguous:", summary.get("total_ambiguous"))
    print("scores_with_amb:", summary.get("scores"))

    page1 = job_dir / "pages" / "page-02.png"  # P1 student used very light pencil
    if page1.is_file():
        if not run_synthetic(page1):
            ok = False
    else:
        print("SYN SKIP: page-01.png missing")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
