# MC Exam Marking (v2.0)

Python-only web app that marks **CWCCC-style** multiple-choice answer sheets (A–D bubbles). Question count follows the marking scheme you paste (Q1…N, up to **100** — 4 columns of 25). No LLM.

Upload a scanned PDF + marking scheme → get annotated PDFs (red box on correct answer, OK/X/AMB, score banner) and machine-readable analysis CSVs/JSON in a zip.

## Requirements

- Python 3.10+
- [poppler-utils](https://poppler.freedesktop.org/) (`pdftoppm`) for PDF → PNG
- CWCCC OMR sheet, rendered at **150 dpi** (pages are aligned via printed landmarks)
- numpy

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

A sample classroom key (40 Q) is prefilled in the UI — shorten or extend it; score total = number of entries in the scheme.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Upload UI |
| POST | `/api/jobs` | Upload PDF + scheme → start job |
| GET | `/api/jobs/{id}` | Job status / scores |
| GET | `/api/jobs/{id}/download` | Zip of annotated PDFs + analysis |
| GET | `/health` | Health check |


## How bubbles are located (v2.0 — landmark alignment)

v1.x used fixed pixel coordinates tuned on one scan, so a few pixels of shift, skew or a
different scanner made boxes land on the printed letters instead of the bubbles.
v2.0 locates every page from the sheet's own printed landmarks:

1. **Orientation** — landscape renders are turned portrait; if the answer frame is found in
   the top half the page is treated as upside down and rotated 180°.
2. **Skew** — the pink sheet print (frame lines, bubble outlines) is isolated by colour and the
   rotation that makes those lines most axis-aligned is found (±5°, 0.05° steps).
3. **Frame landmarks** — the answer-area frame (outer box + 3 column dividers) is located; each
   of its 4 edges is then re-measured in ~30 strips on the original image and fitted as a
   straight line, and the 4 corners are their intersections.
4. **Homography** — a perspective transform maps the reference sheet's frame corners
   (`REF_FRAME` in `engine/geometry.json`) to this page's corners. All 400 reference bubble
   boxes (`BUBBLES`) are pushed through it, so shift / scale / rotation / mild keystone are absorbed.
5. **Local snap** — each question row is nudged (≤4 px) to centre on the printed bubble
   outlines, smoothed per 5-row block so a fully-shaded row still follows its neighbours.
6. If landmarks can't be found the page falls back to fixed coordinates and is flagged
   (`Alignment = fallback-fixed`, orange warning strip on the page) — check those by eye.

`BUBBLES` were recalibrated from 29 real scans (2 print batches) as the median snapped position.

## Reading marks

Ink = pixels darker than 200/255 that are **not** the pink sheet print, as a fraction of the box.
Blank bubbles read ~0–0.06; pencil fills 0.10–0.75.

- nothing ≥ `MARK_MIN_DARK` (0.09) → **blank**
- a second bubble ≥ 0.09 and ≥ 60 % of the strongest → **ambiguous** (no credit, orange **AMB**)
- otherwise the strongest bubble is the answer. A weaker residual mark (≥0.05, e.g. an
  erasure) or a light fill (<0.15) is still scored but listed in **Checks** and marked `?` on the page.

Cross-outs are not recognised as shapes: a slashed-out filled bubble next to a real answer reads as ambiguous.

`python3 scripts/smoke_test.py` checks the classroom sample, double-mark/blank cases, and
rotation ±1.5–2.5°, shift, 0.96 scale and upside-down robustness.

## Layout

```
mc-marking-webapp/
  app/           # FastAPI + Jinja UI
  engine/        # OMR geometry, read, annotate, analyze
  scripts/       # smoke_test.py
  requirements.txt
  run.sh
```
