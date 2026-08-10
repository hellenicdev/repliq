# Dialogue Video Generator

Type a sentence → Groq splits it into natural phrases → the system finds matching dialogue clips
in indexed movie transcripts → fetches the source film (on demand, cached) → extracts the clips
with FFmpeg → concatenates them → returns an MP4 you can play and download.

**Current phase**: Groq segmentation + lexical phrase search + clip extraction/concatenation
+ real public-domain films (Internet Archive) with lazy on-demand fetch.

## Architecture

```
React + Vite (frontend)  ──REST──▶  FastAPI (backend)  ──▶  MongoDB
                                        │
                                        └──▶ FFmpeg (extract + concat)
```

- Frontend: React + Vite + TypeScript (GitHub Pages-ready, `base: '/repliq/'`)
- Backend: FastAPI + motor (async MongoDB driver)
- Database: MongoDB (Atlas in production, local mongod for dev)
- Video processing: FFmpeg (H.264 + AAC, yuv420p, faststart — browser-playable MP4)
- Bot protection: Cloudflare Turnstile

## Project layout

```
backend/
  app/
    main.py            # FastAPI app, CORS, lifespan
    config.py          # all settings from env / .env
    database.py        # MongoDB client + indexes
    models/            # video, dialogue, job (pydantic)
    repositories/      # MongoDB access layer
    routes/            # health, videos, jobs
    services/          # search (replaceable), video (ffmpeg), storage, generation
    utils/             # turnstile, text
  scripts/seed_test_data.py   # generates synthetic test videos + dialogue docs
  media/               # source/, clips/, output/ (gitignored)
frontend/
  src/
    pages/HomePage.tsx # textarea → generate → clips list → video player → download
    services/api.ts    # REST client + job polling
    components/TurnstileWidget.tsx
```

## Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- FFmpeg on PATH (`winget install Gyan.FFmpeg` on Windows)
- MongoDB (MongoDB Atlas free tier, or local mongod)

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows
pip install -r requirements.txt
```

Create `backend/.env` from `.env.example`:

```ini
MONGODB_URI=mongodb+srv://<user>:<pass>@<cluster>.mongodb.net/?retryWrites=true&w=majority
MONGODB_DATABASE=dialogue_video
TURNSTILE_SECRET_KEY=<secret key from Cloudflare>
TURNSTILE_ENFORCED=true
GROQ_API_KEY=<key from console.groq.com>   # sentence segmentation; app falls back without it
# FFMPEG_PATH=/path/to/ffmpeg   # optional if on PATH
# CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Seed the test dataset (7 synthetic videos + 7 dialogue lines):

```powershell
python scripts/seed_test_data.py
```

Index real public-domain films (metadata + transcript only — the film file itself is
downloaded lazily on first use and cached):

```powershell
python scripts/index_pd_film.py --identifier CarnivalOfSouls1962
python scripts/index_pd_film.py --identifier NightOfTheLivingDead-MPEG --title "Night of the Living Dead (1968)"
```

Run the API:

```powershell
uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

### 2. Frontend

```powershell
cd frontend
npm install
```

Create `frontend/.env`:

```ini
VITE_API_URL=http://localhost:8000
VITE_TURNSTILE_SITE_KEY=<site key from Cloudflare>
```

Run:

```powershell
npm run dev        # http://localhost:5173
```

## Test it

Enter: `We need to leave right now`

Expected: clips **"We need to leave."** + **"Right now."** are extracted and concatenated into an MP4 with a download link.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | DB + config status |
| GET | `/api/videos` | list source videos |
| POST | `/api/generate` | `{ "sentence": "...", "turnstileToken": "..." }` → `{ "jobId": "..." }` |
| GET | `/api/jobs/{jobId}` | job status, selected clips, output URL |
| GET | `/api/jobs/{jobId}/output` | the generated MP4 |

## Deployment

- **Backend**: Render web service (free tier: 512 MB RAM, spins down after 15 min idle). Set all `backend/.env` values as Render environment variables. Note: Render free instances have an ephemeral filesystem — output MP4s are lost on restart. Phase 2 moves storage to Cloudflare R2.
- **Frontend**: GitHub Pages — build with `npm run build`, deploy `dist/` to the `gh-pages` branch (`base` is already `/repliq/`).
- **Database**: MongoDB Atlas M0 free cluster (vector search works on M0 for Phase 4).

## Next phases

1. faster-whisper transcription (word-level timestamps)
2. Phrase segmentation (LLM + database-aware)
3. Semantic search (sentence-transformers + Atlas Vector Search)
4. Ranking (semantic/lexical/duration/quality/context)
5. Better cut boundaries + source attribution in output video
6. R2 storage + background worker
