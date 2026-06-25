# llm-code-review-service

A code review service that uses LLMs to analyze code for bugs, security vulnerabilities, and style issues. Built to learn async pipeline design and backend infrastructure patterns beyond basic CRUD.

---

## How it works

1. `POST /review` — submit code, get a job ID back immediately
2. A background worker picks up the job, calls an LLM, stores the result
3. `GET /review/{id}` — poll until status is `done`
4. Same code submitted twice? Returns from cache instantly — no LLM call

---

## Architecture

```
                        ┌─────────────────────────────────┐
                        │         FastAPI Server          │
                        │                                 │
  POST /review ────────►│  Cache hit? ──► return instantly│
                        │                                 │
                        │  Cache miss?                    │
                        │    Insert job → Postgres        │
                        │    Push ID   → Redis queue      │
                        │    Return job_id immediately    │
                        └─────────────────────────────────┘
                                        │
                                   Redis queue
                                        │
                        ┌─────────────────────────────────┐
                        │         Worker Process          │
                        │                                 │
                        │  BLPOP (blocks until job ready) │
                        │    ↓                            │
                        │  Fetch job from Postgres        │
                        │    ↓                            │
                        │  Cache hit? ──► write result    │
                        │                                 │
                        │  Cache miss?                    │
                        │    Call Groq LLM                │
                        │    Fail? ──► Gemini fallback    │
                        │    Still fail? ──►  retry (x3)  │
                        │    Exhausted? ──► mark failed   │
                        │    ↓                            │
                        │  Store result → Postgres        │
                        │  Cache result → Redis (24h TTL) │
                        └─────────────────────────────────┘

  GET /review/{id} ────► Postgres lookup ──► return status + result
```

---

## Design decisions

**Async processing** — LLM calls take 2–10 seconds. Calling inline would block the server thread. The worker decouples submission from processing so the API stays fast under load.

**Redis BLPOP over Postgres polling** — polling Postgres repeatedly wastes DB resources even when the queue is empty. BLPOP blocks at the OS level and wakes instantly when a job arrives.

**Postgres commit before Redis push** — Postgres is the source of truth. Pushing to Redis before the insert succeeds would send workers after jobs that don't exist.

**Content-addressed cache** — SHA-256 of the code is the cache key. Same code always maps to the same key. No separate deduplication logic needed. Measured 50x speedup on cache hits (796ms → 16ms).

**UUID job IDs** — integer IDs are enumerable and require a central counter. UUIDs are non-guessable and generated independently by any process.

**Exponential backoff** — retrying immediately under an overloaded service makes things worse. Wait time doubles each attempt: 2s → 4s → 8s.

**Groq primary, Gemini fallback** — two independent LLMs mean both must fail before a job is marked failed.

---

## Tech stack

| Component | Technology |
|---|---|
| API | FastAPI |
| Database | Postgres |
| Queue + Cache | Redis |
| Primary LLM | Groq (llama-3.1-8b-instant) |
| Fallback LLM | Gemini (gemini-2.0-flash) |
| Infrastructure | Docker Compose |

---

## Project structure

```
llm-code-review-service/
├── app/
│   ├── main.py         # POST /review, GET /review/{id}
│   ├── database.py     # Postgres pool + Redis client
│   └── reviewer.py     # Groq + Gemini integration
├── worker/
│   └── worker.py       # Job processor — cache, retry, stuck job recovery
├── docker-compose.yml
├── demo.py
└── .env                # Not committed
```

---

## Database schema

```sql
CREATE TABLE jobs (
    id            VARCHAR(36)   PRIMARY KEY,
    code_snippet  TEXT          NOT NULL,
    language      VARCHAR(50)   NOT NULL,
    status        VARCHAR(20)   NOT NULL DEFAULT 'pending',
    result        TEXT,
    attempts      INTEGER       NOT NULL DEFAULT 0,
    created_at    TIMESTAMP     NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP
);
```

Status lifecycle: `pending` → `processing` → `done` / `failed`

`started_at` is set when the worker picks up the job — used for stuck job detection. Different from `created_at` since a job may wait in the queue before processing begins.

---

## Setup

**Prerequisites:** Docker Desktop, Python 3.10+

```bash
git clone git@github.com:yourusername/llm-code-review-service.git
cd llm-code-review-service
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env`:
```
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/code_reviewer
REDIS_URL=redis://localhost:6380
GROQ_API_KEY=your_groq_key
GEMINI_API_KEY=your_gemini_key
```

Get API keys:
- Groq: https://console.groq.com
- Gemini: https://aistudio.google.com/app/apikey

Start infrastructure:
```bash
docker-compose up -d
```

Create the jobs table (connect to `localhost:5433`, db: `code_reviewer`):
```sql
CREATE TABLE jobs (
    id VARCHAR(36) PRIMARY KEY,
    code_snippet TEXT NOT NULL,
    language VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    result TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

Run:
```bash
# Terminal 1
uvicorn app.main:app --reload

# Terminal 2
python worker/worker.py

# Terminal 3
python demo.py
```

---

## API

### POST /review

```json
{
  "code_snippet": "def divide(a, b):\n    return a / b",
  "language": "python"
}
```

Cache miss response:
```json
{ "job_id": "f47ac10b-...", "status": "pending", "cached": false }
```

Cache hit response:
```json
{
  "job_id": null,
  "status": "done",
  "cached": true,
  "result": {
    "bugs": ["Division by zero not handled"],
    "security": ["None found"],
    "style": ["Function name not descriptive"],
    "summary": "Function lacks error handling."
  }
}
```

### GET /review/{job_id}

```json
{
  "job_id": "f47ac10b-...",
  "status": "done",
  "result": {
    "bugs": ["Division by zero not handled"],
    "security": ["None found"],
    "style": ["Function name not descriptive"],
    "summary": "Function lacks error handling."
  },
  "language": "python",
  "created_at": "2026-06-24 07:24:35.651676"
}
```

---

## Worker

- **Cache check** — SHA-256 hash looked up in Redis before any LLM call. 24h TTL.
- **Retry with backoff** — up to 3 attempts, wait doubles each time (2s → 4s → 8s). Groq tried first, then Gemini.
- **Stuck job recovery** — every 60 seconds, jobs stuck in `processing` for 5+ minutes are requeued.

---

## Performance

| Scenario | Latency |
|---|---|
| Cache hit (API level) | ~5ms |
| Cache hit (worker level) | ~16ms |
| Fresh LLM call | ~800ms – 2s |
| All retries exhausted | ~14s |

---

## Future work

- Rate limiting
- API key authentication
- Multiple worker instances
- Structured logging and metrics
- Webhook callbacks instead of polling