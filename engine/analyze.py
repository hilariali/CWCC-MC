"""Machine-readable analysis CSVs / JSON for a marked job."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def write_analysis(pages: list[dict[str, Any]], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    questions = [d["q"] for d in pages[0]["detail"]] if pages else []
    total_q = len(questions)

    summary_path = out_dir / "marks_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Page", "Score", "Total", "Wrong", "Blank", "Ambiguous", "Wrong/blank/amb",
                    "Alignment", "Skew_deg", "Checks", "Warnings"])
        for p in pages:
            notes = []
            for d in p["detail"]:
                if not d["ok"]:
                    if d.get("status") == "ambiguous":
                        marked = d.get("marked") or (
                            d["student"].split("/") if d.get("student") else []
                        )
                        mark_s = "/".join(marked) if marked else "amb"
                        notes.append(f"Q{d['q']}:amb({mark_s})->{d['key']}")
                    else:
                        stu = d["student"] or "blank"
                        notes.append(f"Q{d['q']}:{stu}->{d['key']}")
            w.writerow(
                [
                    p["page"],
                    p["score"],
                    p["total"],
                    p["wrong"],
                    p["blank"],
                    p.get("ambiguous", 0),
                    "; ".join(notes),
                    (p.get("alignment") or {}).get("method", ""),
                    (p.get("alignment") or {}).get("skew_deg", ""),
                    "; ".join(f"Q{c['q']}: {c['note']}" for c in p.get("checks") or []),
                    "; ".join(p.get("warnings") or []),
                ]
            )
    paths["marks_summary"] = summary_path

    matrix_path = out_dir / "student_question_matrix.csv"
    with open(matrix_path, "w", newline="", encoding="utf-8") as f:
        header = ["page", "score"]
        for q in questions:
            header += [f"Q{q}_correct", f"Q{q}_answer", f"Q{q}_key"]
        w = csv.writer(f)
        w.writerow(header)
        for p in pages:
            row: list[Any] = [p["page"], p["score"]]
            for d in p["detail"]:
                row += [1 if d["ok"] else 0, d["student"] or "", d["key"]]
            w.writerow(row)
    paths["student_question_matrix"] = matrix_path

    long_path = out_dir / "student_question_long.csv"
    with open(long_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["page", "question", "student_answer", "key", "correct", "status"]
        )
        for p in pages:
            for d in p["detail"]:
                w.writerow(
                    [
                        p["page"],
                        d["q"],
                        d["student"] or "",
                        d["key"],
                        1 if d["ok"] else 0,
                        d["status"],
                    ]
                )
    paths["student_question_long"] = long_path

    stats_path = out_dir / "question_stats.csv"
    who_path = out_dir / "question_who_right_wrong.csv"
    n_pages = max(len(pages), 1)
    q_stats: list[dict[str, Any]] = []
    with open(stats_path, "w", newline="", encoding="utf-8") as fs, open(
        who_path, "w", newline="", encoding="utf-8"
    ) as fw:
        ws = csv.writer(fs)
        ww = csv.writer(fw)
        ws.writerow(
            [
                "question",
                "key",
                "correct_count",
                "wrong_count",
                "blank_count",
                "ambiguous_count",
                "correct_pct",
                "wrong_pct",
                "blank_pct",
                "ambiguous_pct",
            ]
        )
        ww.writerow(
            [
                "question",
                "key",
                "pages_correct",
                "pages_wrong",
                "pages_blank",
                "pages_ambiguous",
            ]
        )
        for qi, q in enumerate(questions):
            right, wrong, blank, amb = [], [], [], []
            key_ans = pages[0]["detail"][qi]["key"] if pages else ""
            for p in pages:
                d = p["detail"][qi]
                label = str(p["page"])
                if d["status"] == "correct":
                    right.append(label)
                elif d["status"] == "blank":
                    blank.append(label)
                elif d["status"] == "ambiguous":
                    amb.append(label)
                else:
                    wrong.append(label)
            cc, wc, bc, ac = len(right), len(wrong), len(blank), len(amb)
            row_stat = {
                "question": q,
                "key": key_ans,
                "correct_count": cc,
                "wrong_count": wc,
                "blank_count": bc,
                "ambiguous_count": ac,
                "correct_pct": round(100.0 * cc / n_pages, 1),
                "wrong_pct": round(100.0 * wc / n_pages, 1),
                "blank_pct": round(100.0 * bc / n_pages, 1),
                "ambiguous_pct": round(100.0 * ac / n_pages, 1),
            }
            q_stats.append(row_stat)
            ws.writerow(
                [
                    q,
                    key_ans,
                    cc,
                    wc,
                    bc,
                    ac,
                    row_stat["correct_pct"],
                    row_stat["wrong_pct"],
                    row_stat["blank_pct"],
                    row_stat["ambiguous_pct"],
                ]
            )
            ww.writerow(
                [
                    q,
                    key_ans,
                    " ".join(right),
                    " ".join(wrong),
                    " ".join(blank),
                    " ".join(amb),
                ]
            )
    paths["question_stats"] = stats_path
    paths["question_who_right_wrong"] = who_path

    total_ambiguous = sum(int(p.get("ambiguous") or 0) for p in pages)
    summary = {
        "num_pages": len(pages),
        "total_questions": total_q,
        "total_ambiguous": total_ambiguous,
        "scores": [
            {
                "page": p["page"],
                "score": p["score"],
                "total": p["total"],
                "wrong": p["wrong"],
                "blank": p["blank"],
                "ambiguous": p.get("ambiguous", 0),
            }
            for p in pages
        ],
        "mean_score": round(
            sum(p["score"] for p in pages) / n_pages, 2
        )
        if pages
        else 0,
        "question_stats": q_stats,
        "hardest": sorted(q_stats, key=lambda r: r["correct_pct"])[:5],
        "easiest": sorted(q_stats, key=lambda r: -r["correct_pct"])[:5],
    }
    summary_json = out_dir / "analysis_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    paths["analysis_summary"] = summary_json
    return paths
