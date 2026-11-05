# MC Exam Marking (v1)

Python-only web app that marks **CWCCC-style** multiple-choice answer sheets (Q1–40, A–D bubbles). No LLM.

Upload a scanned PDF + marking scheme → get annotated PDFs (red box on correct answer, OK/X, score banner) and machine-readable analysis CSVs/JSON in a zip.

## Requirements

- Python 3.10+
- [poppler-utils](https://poppler.freedesktop.org/) (`pdftoppm`) for PDF → PNG
- Calibrated for CWCCC OMR sheets rendered at **~150 dpi** (page size ~1275×1650)

```bash
# Debian/Ubuntu
sudo apt-get install -y poppler-utils
```

## Setup & run (local)

```bash
cd mc-marking-webapp
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt

# optional shared password when sharing with colleagues
export MARKING_APP_PASSWORD='choose-a-password'

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or: `./run.sh`

Open http://127.0.0.1:8000

- Username for Basic Auth (if password set): any / recommended `mark`
- Password: value of `MARKING_APP_PASSWORD`

## Deploy notes

This is a **long-running FastAPI + Pillow + poppler** app (CPU, temp files, PDF conversion). It is **not** a static site and **will not run on Vercel** (no lasting Python process / no `pdftoppm`).

Good options after pushing to GitHub:

| Host | Notes |
|------|--------|
| Railway / Render / Fly.io | Native Python web service; install `poppler-utils` in build |
| Google Cloud Run / Cloudflare Containers | Container with Dockerfile (add `poppler-utils`) |
| School VPS / PC + Cloudflare Tunnel | Run uvicorn locally, expose with `cloudflared` |

Suggested start command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set env `MARKING_APP_PASSWORD` in the host dashboard.

### Minimal Dockerfile (optional)

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
```

## Marking scheme formats

- JSON: `{"1":"A","2":"C",...}`
- Lines: `1:A` / `1 = C` / `Q7 C`
- Compact: `1A 2C 3C ...`

Default classroom key (40 Q) is prefilled in the UI.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Upload UI |
| POST | `/api/jobs` | Upload PDF + scheme → start job |
| GET | `/api/jobs/{id}` | Job status / scores |
| GET | `/api/jobs/{id}/download` | Zip of annotated PDFs + analysis |
| GET | `/health` | Health check |

## Layout

```
mc-marking-webapp/
  app/           # FastAPI + Jinja UI
  engine/        # OMR geometry, read, annotate, analyze
  scripts/       # smoke_test.py
  requirements.txt
  run.sh
```
