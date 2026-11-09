"""MC Exam Marking web app — FastAPI."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import threading
import traceback
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import mark_pdf, parse_marking_scheme  # noqa: E402
from engine.scheme import DEFAULT_KEY as _DK  # noqa: E402

DATA_DIR = Path(os.environ.get("MARKING_DATA_DIR", str(ROOT / "data" / "jobs")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

APP_PASSWORD = os.environ.get("MARKING_APP_PASSWORD", "").strip()
security = HTTPBasic(auto_error=False)

app = FastAPI(title="MC Exam Marking", version="2.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _default_scheme_text() -> str:
    return "\n".join(f"{q}:{a}" for q, a in sorted(_DK.items()))


def require_password(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    if not APP_PASSWORD:
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    pass_ok = secrets.compare_digest(credentials.password or "", APP_PASSWORD)
    if not pass_ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid password",
            headers={"WWW-Authenticate": "Basic"},
        )


def _set_job(job_id: str, **fields) -> None:
    fields.pop("job_id", None)
    with _jobs_lock:
        cur = _jobs.get(job_id, {})
        cur.update(fields)
        cur["job_id"] = job_id
        _jobs[job_id] = cur
        # Persist status for multi-worker / restart friendliness
        job_dir = DATA_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        status_path = job_dir / "status.json"
        dump = {k: v for k, v in cur.items() if k != "result"}
        status_path.write_text(json.dumps(dump, indent=2), encoding="utf-8")


def _get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        if job_id in _jobs:
            return dict(_jobs[job_id])
    status_path = DATA_DIR / job_id / "status.json"
    if status_path.is_file():
        return json.loads(status_path.read_text(encoding="utf-8"))
    return None


def _run_mark_job(job_id: str, pdf_path: Path, scheme: str) -> None:
    job_dir = DATA_DIR / job_id

    def progress(stage: str, extra: dict) -> None:
        _set_job(job_id, status="running", stage=stage, **extra)

    try:
        _set_job(job_id, status="running", stage="starting")
        result = mark_pdf(pdf_path, scheme, job_dir, progress=progress)
        payload = {
            "status": "done",
            "num_pages": len(result.pages),
            "num_questions": result.pages[0]["total"] if result.pages else 0,
            "page_errors": result.page_errors,
            "scores": [
                {
                    "page": p["page"],
                    "score": p["score"],
                    "total": p["total"],
                    "wrong": p["wrong"],
                    "blank": p["blank"],
                    "ambiguous": p.get("ambiguous", 0),
                    "error": p.get("error"),
                    "alignment": (p.get("alignment") or {}).get("method"),
                    "warnings": p.get("warnings") or [],
                    "checks": p.get("checks") or [],
                }
                for p in result.pages
            ],
            "mean_score": round(
                sum(p["score"] for p in result.pages if not p.get("error"))
                / max(sum(1 for p in result.pages if not p.get("error")), 1),
                2,
            ),
            "download_url": f"/api/jobs/{job_id}/download",
            "preview_urls": [
                f"/api/jobs/{job_id}/preview/{p['page']}"
                for p in result.pages
                if not p.get("error")
            ],
        }
        _set_job(job_id, **payload)
    except Exception as e:
        _set_job(
            job_id,
            status="error",
            error=str(e),
            traceback=traceback.format_exc()[-2000:],
        )


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def index(request: Request, _: None = Depends(require_password)):
    if request.method == "HEAD":
        return Response(status_code=200)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "default_scheme": _default_scheme_text(),
            "password_enabled": bool(APP_PASSWORD),
        },
    )


@app.post("/api/mark")
async def api_mark(
    pdf: UploadFile = File(...),
    scheme: str = Form(...),
    _: None = Depends(require_password),
):
    name = (pdf.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF answer sheet (.pdf)")
    try:
        key = parse_marking_scheme(scheme)
    except Exception as e:
        raise HTTPException(400, f"Invalid marking scheme: {e}") from e

    job_id = secrets.token_hex(8)
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = job_dir / "upload.pdf"
    content = await pdf.read()
    if not content:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, "Empty PDF upload")
    if len(content) > 80 * 1024 * 1024:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, "PDF too large (max 80 MB)")
    pdf_path.write_bytes(content)

    _set_job(
        job_id,
        status="queued",
        num_questions=len(key),
        filename=pdf.filename,
    )
    t = threading.Thread(
        target=_run_mark_job, args=(job_id, pdf_path, scheme), daemon=True
    )
    t.start()

    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "num_questions": len(key),
            "status_url": f"/api/jobs/{job_id}",
        }
    )


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str, _: None = Depends(require_password)):
    job = _get_job(job_id)
    if not job:
        # fallback: completed artifact without in-memory status
        scores_path = DATA_DIR / job_id / "scores.json"
        if scores_path.is_file():
            pages = json.loads(scores_path.read_text(encoding="utf-8"))
            return {
                "job_id": job_id,
                "status": "done",
                "num_pages": len(pages),
                "scores": [
                    {
                        "page": p["page"],
                        "score": p["score"],
                        "total": p["total"],
                        "wrong": p["wrong"],
                        "blank": p["blank"],
                        "ambiguous": p.get("ambiguous", 0),
                        "error": p.get("error"),
                        "alignment": (p.get("alignment") or {}).get("method"),
                        "warnings": p.get("warnings") or [],
                        "checks": p.get("checks") or [],
                    }
                    for p in pages
                ],
                "mean_score": round(
                    sum(p["score"] for p in pages) / max(len(pages), 1), 2
                ),
                "download_url": f"/api/jobs/{job_id}/download",
                "preview_urls": [
                    f"/api/jobs/{job_id}/preview/{i}" for i in range(1, len(pages) + 1)
                ],
            }
        raise HTTPException(404, "Job not found")
    return JSONResponse(job)


@app.get("/api/jobs/{job_id}/preview/{page}")
async def api_preview(
    job_id: str, page: int, _: None = Depends(require_password)
):
    path = DATA_DIR / job_id / "preview" / f"page-{page:02d}-marked.png"
    if not path.is_file():
        raise HTTPException(404, "Preview not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/jobs/{job_id}/download")
async def api_download(job_id: str, _: None = Depends(require_password)):
    path = DATA_DIR / job_id / "MC_exam_marked.zip"
    if not path.is_file():
        raise HTTPException(404, "Download not found — job may still be running or failed")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"MC_exam_marked_{job_id}.zip",
    )


@app.get("/health")
@app.head("/health")
async def health():
    return {"ok": True, "password_required": bool(APP_PASSWORD), "version": "2.0.0"}
