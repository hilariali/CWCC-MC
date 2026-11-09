"""End-to-end: PDF → page images → read → annotate → analysis → zip."""

from __future__ import annotations

import gc
import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image
from pypdf import PdfWriter

from .analyze import write_analysis
from .annotate import annotate_page
from .geometry import DPI, load_geometry
from .layout import locate_bubbles
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
    page_errors: list[dict[str, Any]] = field(default_factory=list)


def _pdf_page_count(pdf_path: Path) -> int | None:
    """Return page count via pdfinfo, or None if unavailable."""
    try:
        proc = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        if line.lower().startswith("pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def pdf_to_pngs(
    pdf_path: Path,
    out_dir: Path,
    dpi: int = DPI,
    *,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    page_timeout: int = 120,
) -> list[Path]:
    """Rasterize a PDF one page at a time to keep peak RAM low (Render free ~512MB)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.png"):
        old.unlink()

    total = _pdf_page_count(pdf_path)
    pages: list[Path] = []
    page_num = 1

    def report(stage: str, **extra: Any) -> None:
        if progress:
            progress(stage, extra)

    while True:
        if total is not None and page_num > total:
            break
        # pdftoppm names: prefix-1.png, prefix-2.png, ... (no zero-pad by default)
        # Use a per-page prefix so we control the final filename.
        tmp_prefix = out_dir / f"_tmp_page{page_num}"
        cmd = [
            "pdftoppm",
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page_num),
            "-l",
            str(page_num),
            "-singlefile",
            str(pdf_path),
            str(tmp_prefix),
        ]
        report(
            "rasterizing",
            page=page_num,
            total_pages=total if total is not None else page_num,
            dpi=dpi,
        )
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=page_timeout,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "pdftoppm (poppler-utils) is required. Install poppler-utils."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"pdftoppm timed out after {page_timeout}s on page {page_num}"
            ) from e
        except subprocess.CalledProcessError as e:
            if total is None and page_num > 1:
                # Past the last page when we did not know the count up front.
                break
            raise RuntimeError(
                f"pdftoppm failed on page {page_num}: {e.stderr or e.stdout}"
            ) from e

        produced = out_dir / f"_tmp_page{page_num}.png"
        if not produced.is_file():
            if total is None and page_num > 1:
                break
            raise RuntimeError(
                f"pdftoppm produced no image for page {page_num} — is the PDF valid/scanned?"
            )
        final = out_dir / f"page-{page_num:02d}.png"
        produced.replace(final)
        pages.append(final)
        page_num += 1

    if not pages:
        raise RuntimeError("pdftoppm produced no page images — is the PDF valid/scanned?")
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
    dpi: int | None = None,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> MarkingResult:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    def report(stage: str, **extra: Any) -> None:
        if progress:
            progress(stage, extra)

    key = parse_marking_scheme(scheme_text)
    (job_dir / "marking_scheme.json").write_text(
        json.dumps({str(k): v for k, v in key.items()}, indent=2),
        encoding="utf-8",
    )
    questions = sorted(key.keys())
    report("parsed_scheme", num_questions=len(questions))

    geo = load_geometry(geometry_path)

    pages_dir = job_dir / "pages"
    annotated_dir = job_dir / "annotated"
    preview_dir = job_dir / "preview"
    analysis_dir = job_dir / "analysis"
    for d in (pages_dir, annotated_dir, preview_dir, analysis_dir):
        d.mkdir(parents=True, exist_ok=True)

    pdf_copy = job_dir / "input.pdf"
    if Path(pdf_path).resolve() != pdf_copy.resolve():
        shutil.copy2(pdf_path, pdf_copy)

    use_dpi = dpi or DPI
    report("rasterizing", dpi=use_dpi)
    page_pngs = pdf_to_pngs(
        pdf_copy, pages_dir, dpi=use_dpi, progress=progress
    )
    report("rasterized", num_pages=len(page_pngs))

    result = MarkingResult(job_dir=job_dir)
    page_records: list[dict[str, Any]] = []

    for idx, png in enumerate(page_pngs, start=1):
        report("marking_page", page=idx, total_pages=len(page_pngs))
        try:
            with Image.open(png) as im0:
                im = im0.convert("RGB")
                # Force load before file closes
                im.load()
            layout = locate_bubbles(im, geo, questions)
            reads = read_answers(im, geo, questions=questions, layout=layout)
            scored = score_page(reads, key)
            scored["page"] = idx
            scored["error"] = None
            scored["alignment"] = {"method": layout.method, **layout.info}
            scored["warnings"] = list(layout.warnings)
            scored["checks"] = [
                {"q": d["q"], "note": d["note"]} for d in scored["detail"] if d.get("note")
            ]
            page_records.append(scored)

            annotated = annotate_page(im, reads, scored, geo, layout=layout)
            preview_path = preview_dir / f"page-{idx:02d}-marked.png"
            annotated.save(preview_path, "PNG", optimize=True)
            result.preview_paths.append(preview_path)

            pdf_out = annotated_dir / f"P{idx:02d}_page{idx:02d}_marked.pdf"
            _save_pdf(annotated, pdf_out)
            result.pdf_paths.append(pdf_out)
            # free memory — keep peak RAM low on Render free (512MB)
            del annotated, reads, im, layout, scored
            gc.collect()
        except Exception as e:
            err = {"page": idx, "error": str(e)}
            result.page_errors.append(err)
            page_records.append(
                {
                    "page": idx,
                    "score": 0,
                    "total": len(questions),
                    "wrong": 0,
                    "blank": len(questions),
                    "ambiguous": 0,
                    "detail": [
                        {
                            "q": q,
                            "student": "",
                            "key": key[q],
                            "status": "error",
                            "ok": False,
                            "marked": [],
                        }
                        for q in questions
                    ],
                    "error": str(e),
                }
            )
            gc.collect()

    if not result.pdf_paths:
        raise RuntimeError(
            "No pages could be marked. "
            + (result.page_errors[0]["error"] if result.page_errors else "Unknown error")
        )

    result.pages = page_records
    (job_dir / "scores.json").write_text(
        json.dumps(page_records, indent=2), encoding="utf-8"
    )

    combined = annotated_dir / "ALL_pages_marked.pdf"
    writer = PdfWriter()
    for p in result.pdf_paths:
        writer.append(str(p))
    with open(combined, "wb") as f:
        writer.write(f)
    writer.close()
    result.combined_pdf = combined

    result.analysis_paths = write_analysis(
        [p for p in page_records if not p.get("error")], analysis_dir
    ) if any(not p.get("error") for p in page_records) else write_analysis(page_records, analysis_dir)

    bundle_dir = job_dir / "bundle"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir()
    for p in result.pdf_paths:
        shutil.copy2(p, bundle_dir / p.name)
    shutil.copy2(combined, bundle_dir / combined.name)
    for p in result.analysis_paths.values():
        shutil.copy2(p, bundle_dir / p.name)

    zip_path = job_dir / "MC_exam_marked.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(bundle_dir.iterdir()):
            zf.write(fpath, arcname=fpath.name)
    result.zip_path = zip_path
    report("done", num_pages=len(page_records), errors=len(result.page_errors))
    return result
