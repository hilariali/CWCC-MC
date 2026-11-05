#!/usr/bin/env python3
"""Smoke-test the marking engine on the sample CWCCC PDF."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import DEFAULT_KEY, mark_pdf
from engine.scheme import parse_marking_scheme

SAMPLE = Path(
    "/home/box/agent-data/agents/0238e52f-7351-468d-b4a0-8cb21e7b3d73/attachments/"
    "df2a1b1faadf7705b3957f341f8ae66633eeae63a68d3fed74af7a3abea36bbe.pdf"
)
EXPECTED = [16, 24, 24, 22, 15, 27, 25, 27, 32, 24]


def main() -> int:
    if not SAMPLE.is_file():
        # fallback to already-rasterized pages via a tiny PDF rebuild is hard;
        # use existing pages by synthesizing from /workspace/mc-marking if sample missing
        alt = Path("/workspace/mc-marking")
        print(f"Sample PDF missing at {SAMPLE}", file=sys.stderr)
        if (alt / "pages" / "page-01.png").is_file():
            print("Will not invent PDF; fail.", file=sys.stderr)
        return 2

    job_dir = ROOT / "data" / "jobs" / "smoke"
    if job_dir.exists():
        import shutil

        shutil.rmtree(job_dir)

    scheme = "\n".join(f"{q}:{a}" for q, a in sorted(DEFAULT_KEY.items()))
    parse_marking_scheme(scheme)  # validate
    result = mark_pdf(SAMPLE, scheme, job_dir)

    scores = [p["score"] for p in result.pages]
    print("scores:", scores)
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
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
