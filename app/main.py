from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uuid
import json
import hashlib
from app.database import get_connection, return_connection, get_redis

app = FastAPI()


class ReviewRequest(BaseModel):
    code_snippet: str = Field(..., min_length=1, max_length=50000)
    language: str = Field(..., min_length=1, max_length=50)


@app.post("/review")
def submit_review(request: ReviewRequest):
    # check cache before creating a job
    cache_key = "cache:" + hashlib.sha256(request.code_snippet.encode()).hexdigest()

    try:
        cached_result = get_redis().get(cache_key)
    except Exception as e:
        print(f"Redis cache check failed: {e}")
        cached_result = None

    if cached_result:
        return {
            "job_id": None,
            "status": "done",
            "result": json.loads(cached_result.decode("utf-8")),
            "language": request.language,
            "cached": True
        }

    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO jobs (id, code_snippet, language) VALUES (%s, %s, %s)",
            (job_id, request.code_snippet, request.language)
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"Database error on job insert: {e}")
        return_connection(conn)
        raise HTTPException(status_code=500, detail="Internal server error")

    return_connection(conn)

    try:
        get_redis().rpush("review_queue", job_id)
    except Exception as e:
        print(f"Redis queue push failed: {e}")
        # job is in DB, worker can still pick it up via stuck job recovery

    return {"job_id": job_id, "status": "pending", "cached": False}


@app.get("/review/{job_id}")
def get_review(job_id: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, status, result, language, created_at FROM jobs WHERE id = %s",
            (job_id,)
        )
        job = cur.fetchone()
        cur.close()
    except Exception as e:
        print(f"Database error on job fetch: {e}")
        return_connection(conn)
        raise HTTPException(status_code=500, detail="Internal server error")

    return_connection(conn)

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job[0],
        "status": job[1],
        "result": json.loads(job[2]) if job[2] else None,
        "language": job[3],
        "created_at": str(job[4])
    }