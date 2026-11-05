"""End-to-end: PDF → page images → read → annotate → analysis → zip."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfWriter

from .analyze import write_analysis
from .annotate import annotate_page
from .geometry import DPI, load_geometry
from .reader import read_answers, score_page
from .scheme import parse_marking_scheme


@dataclass
class MarkingResult:
    job_dir: Path
    pages: list[dict[str, Any]] = field(default_factory=list)
    zip_path: Path | None = None
    preview_paths: list[Path] = field(default_factory=list)
    pdf_paths: list[Path] = field(default_factory=list)
    combined_pdf: Path | None = None
    analysis_paths: dict[str, Path] = field(default_factory=dict)


def pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int = DPI) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "page"
    # Clear previous
    for old in out_dir.glob("page-*.png"):
        old.unlink()
    cmd = [
        "pdftoppm",
        "-png",
        "-r",
        str(dpi),
        str(pdf_path),
        str(prefix),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "pdftoppm (poppler-utils) is required. Install poppler-utils."
        ) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"pdftoppm failed: {e.stderr or e.stdout}") from e
    pages = sorted(out_dir.glob("page-*.png"))
    if not pages:
        raise RuntimeError("pdftoppm produced no page images")
    return pages


def _save_pdf(image: Image.Image, path: Path) -> None:
    rgb = image.convert("RGB")
    rgb.save(path, "PDF", resolution=DPI)


def mark_pdf(
    pdf_path: Path,
    scheme_text: str,
    job_dir: Path,
    *,
    geometry_path: Path | None = None,
) -> MarkingResult:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    key = parse_marking_scheme(scheme_text)
    (job_dir / "marking_scheme.json").write_text(
        json.dumps({str(k): v for k, v in key.items()}, indent=2),
        encoding="utf-8",
    )

    geo = load_geometry(geometry_path)

    pages_dir = job_dir / "pages"
    annotated_dir = job_dir / "annotated"
    preview_dir = job_dir / "preview"
    analysis_dir = job_dir / "analysis"
    for d in (pages_dir, annotated_dir, preview_dir, analysis_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Keep a copy of the uploaded PDF
    pdf_copy = job_dir / "input.pdf"
    if Path(pdf_path).resolve() != pdf_copy.resolve():
        shutil.copy2(pdf_path, pdf_copy)

    page_pngs = pdf_to_pngs(pdf_copy, pages_dir, dpi=DPI)

    result = MarkingResult(job_dir=job_dir)
    page_records: list[dict[str, Any]] = []

    for idx, png in enumerate(page_pngs, start=1):
        im = Image.open(png)
        reads = read_answers(im, geo)
        scored = score_page(reads, key)
        scored["page"] = idx
        page_records.append(scored)

        annotated = annotate_page(im, reads, scored, geo)
        preview_path = preview_dir / f"page-{idx:02d}-marked.png"
        annotated.save(preview_path, "PNG")
        result.preview_paths.append(preview_path)

        pdf_out = annotated_dir / f"P{idx:02d}_page{idx:02d}_marked.pdf"
        _save_pdf(annotated, pdf_out)
        result.pdf_paths.append(pdf_out)

    result.pages = page_records
    (job_dir / "scores.json").write_text(
        json.dumps(page_records, indent=2), encoding="utf-8"
    )

    # Combined PDF
    combined = annotated_dir / "ALL_pages_marked.pdf"
    writer = PdfWriter()
    for p in result.pdf_paths:
        writer.append(str(p))
    with open(combined, "wb") as f:
        writer.write(f)
    writer.close()
    result.combined_pdf = combined

    result.analysis_paths = write_analysis(page_records, analysis_dir)
    # Also copy analysis next to annotated for the zip bundle
    bundle_dir = job_dir / "bundle"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir()
    for p in result.pdf_paths:
        shutil.copy2(p, bundle_dir / p.name)
    shutil.copy2(combined, bundle_dir / combined.name)
    for p in result.analysis_paths.values():
        shutil.copy2(p, bundle_dir / p.name)
    # marks_summary already in analysis; ensure present
    shutil.copy2(
        result.analysis_paths["marks_summary"],
        bundle_dir / "marks_summary.csv",
    )

    zip_path = job_dir / "MC_exam_marked.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(bundle_dir.iterdir()):
            zf.write(fpath, arcname=fpath.name)
    result.zip_path = zip_path
    return result
