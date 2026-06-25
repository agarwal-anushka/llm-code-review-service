import sys
import os
import hashlib
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_connection, return_connection, get_redis
from app.reviewer import review_code
from dotenv import load_dotenv

load_dotenv()


def process_job(job_id):
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT id, code_snippet, language, attempts FROM jobs WHERE id = %s",
            (job_id,)
        )
        job = cur.fetchone()

        if job is None:
            print(f"Job {job_id} not found in database")
            cur.close()
            return_connection(conn)
            return

        code_snippet = job[1]
        language = job[2]
        attempts = job[3]

        cur.execute(
            "UPDATE jobs SET status = 'processing', attempts = %s, started_at = NOW() WHERE id = %s",
            (attempts + 1, job_id)
        )
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"Database error on job fetch: {e}")
        return_connection(conn)
        return

    return_connection(conn)
    print(f"Processing job {job_id} | language: {language} | attempts: {attempts + 1}")

    # generate cache key
    cache_key = "cache:" + hashlib.sha256(code_snippet.encode()).hexdigest()

    # check cache
    redis_client = get_redis()
    cached_result = redis_client.get(cache_key)

    if cached_result:
        print(f"Cache HIT for job {job_id} - returning cached result")
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE jobs SET status = 'done', result = %s, completed_at = NOW() WHERE id = %s",
                (cached_result.decode("utf-8"), job_id)
            )
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"Database error on cache hit: {e}")
        finally:
            return_connection(conn)
        return

    print(f"Cache MISS for job {job_id} - calling LLM")

    # retry with exponential backoff
    max_attempts = 3
    wait_time = 2
    result = None
    last_error = None

    for attempt in range(max_attempts):
        try:
            result = review_code(code_snippet, language)
            break
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                print(f"LLM attempt {attempt + 1} failed, retrying in {wait_time}s...")
                time.sleep(wait_time)
                wait_time *= 2

    if result is None:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE jobs SET status = 'failed', completed_at = NOW() WHERE id = %s",
                (job_id,)
            )
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"Database error while marking failed: {e}")
        finally:
            return_connection(conn)
        print(f"Job {job_id} failed after {max_attempts} attempts: {last_error}")
        return

    # store result and cache it
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE jobs SET status = 'done', result = %s, completed_at = NOW() WHERE id = %s",
            (result, job_id)
        )
        conn.commit()
        redis_client.set(cache_key, result, ex=86400)
        cur.close()
    except Exception as e:
        print(f"Database error while storing result: {e}")
    finally:
        return_connection(conn)

    print(f"Job {job_id} completed successfully")


def recover_stuck_jobs():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE jobs
            SET status = 'pending'
            WHERE status = 'processing'
            AND started_at < NOW() - INTERVAL '5 minutes'
            RETURNING id
        """)
        stuck_jobs = cur.fetchall()
        conn.commit()
        cur.close()

        if stuck_jobs:
            redis_client = get_redis()
            for job in stuck_jobs:
                redis_client.rpush("review_queue", job[0])
                print(f"Requeued stuck job: {job[0]}")
        else:
            print("No stuck jobs found")
    except Exception as e:
        print(f"Error recovering stuck jobs: {e}")
    finally:
        return_connection(conn)


def main():
    print("Worker started, waiting for jobs...")
    redis_client = get_redis()
    last_recovery_check = 0

    while True:
        try:
            # check for stuck jobs every 60 seconds
            current_time = time.time()
            if current_time - last_recovery_check > 60:
                recover_stuck_jobs()
                last_recovery_check = current_time

            job = redis_client.blpop("review_queue", timeout=5)
            if job is None:
                continue

            job_id = job[1].decode("utf-8")
            print(f"Picked up job: {job_id}")
            process_job(job_id)

        except Exception as e:
            print(f"Worker error: {e}")


if __name__ == "__main__":
    main()