"""MC Exam Marking web app — FastAPI."""

from __future__ import annotations

import os
import secrets
import shutil
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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

app = FastAPI(title="MC Exam Marking", version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
    user_ok = secrets.compare_digest(credentials.username or "", "mark")
    # Also allow empty username with password only via password field
    pass_ok = secrets.compare_digest(credentials.password or "", APP_PASSWORD)
    # Accept username "mark" OR any username if password matches
    if not pass_ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid password",
            headers={"WWW-Authenticate": "Basic"},
        )
    _ = user_ok  # username optional


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, _: None = Depends(require_password)):
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
    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF answer sheet")
    try:
        parse_marking_scheme(scheme)
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
    pdf_path.write_bytes(content)

    try:
        result = mark_pdf(pdf_path, scheme, job_dir)
    except Exception as e:
        raise HTTPException(500, f"Marking failed: {e}") from e

    return JSONResponse(
        {
            "job_id": job_id,
            "num_pages": len(result.pages),
            "scores": [
                {
                    "page": p["page"],
                    "score": p["score"],
                    "total": p["total"],
                    "wrong": p["wrong"],
                    "blank": p["blank"],
                }
                for p in result.pages
            ],
            "mean_score": round(
                sum(p["score"] for p in result.pages) / max(len(result.pages), 1), 2
            ),
            "download_url": f"/api/jobs/{job_id}/download",
            "preview_urls": [
                f"/api/jobs/{job_id}/preview/{i}"
                for i in range(1, len(result.pages) + 1)
            ],
        }
    )


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
        raise HTTPException(404, "Download not found")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"MC_exam_marked_{job_id}.zip",
    )


@app.get("/api/jobs/{job_id}/summary")
async def api_summary(job_id: str, _: None = Depends(require_password)):
    path = DATA_DIR / job_id / "scores.json"
    if not path.is_file():
        raise HTTPException(404, "Job not found")
    import json

    pages = json.loads(path.read_text(encoding="utf-8"))
    return {
        "job_id": job_id,
        "scores": [
            {
                "page": p["page"],
                "score": p["score"],
                "total": p["total"],
                "wrong": p["wrong"],
                "blank": p["blank"],
            }
            for p in pages
        ],
    }


@app.get("/health")
async def health():
    return {"ok": True, "password_required": bool(APP_PASSWORD)}
