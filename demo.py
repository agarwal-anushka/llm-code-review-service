import requests
import time
import json
import hashlib

BASE_URL = "http://localhost:8000"

CODE = """
def divide(a, b):
    password = "abc123"
    return a / b
"""

LANGUAGE = "python"


def submit_job(code, language):
    response = requests.post(f"{BASE_URL}/review", json={
        "code_snippet": code,
        "language": language
    })
    return response.json()


def poll_job(job_id, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        response = requests.get(f"{BASE_URL}/review/{job_id}")
        data = response.json()
        if data["status"] in ("done", "failed"):
            return data
        print(f"  status: {data['status']}... waiting")
        time.sleep(2)
    raise TimeoutError("Job did not complete in time")


def print_result(data):
    result = data.get("result")
    if not result:
        print("  No result available")
        return
    print(f"\n  BUGS:")
    for item in result.get("bugs", []):
        print(f"    - {item}")
    print(f"\n  SECURITY:")
    for item in result.get("security", []):
        print(f"    - {item}")
    print(f"\n  STYLE:")
    for item in result.get("style", []):
        print(f"    - {item}")
    print(f"\n  SUMMARY: {result.get('summary', '')}")


def main():
    print("=" * 50)
    print("AI CODE REVIEWER DEMO")
    print("=" * 50)

    # run 1 - fresh submission
    print("\n[1] Submitting code for review (fresh)...")
    start = time.time()
    data = submit_job(CODE, LANGUAGE)

    if data.get("cached"):
        elapsed = time.time() - start
        print(f"  API-level Cache HIT in {elapsed:.3f}s - returned instantly")
        print_result(data)
    else:
        job_id = data["job_id"]
        print(f"  Job ID: {job_id}")
        print(f"  Polling for result...")
        result_data = poll_job(job_id)
        elapsed = time.time() - start
        print(f"  Completed in {elapsed:.2f}s | status: {result_data['status']}")
        print_result(result_data)

    # run 2 - same code, should be cache hit
    print("\n[2] Submitting SAME code again (expect instant cache hit)...")
    start = time.time()
    data = submit_job(CODE, LANGUAGE)
    elapsed = time.time() - start

    if data.get("cached"):
        print(f"  API-level Cache HIT in {elapsed:.3f}s - LLM skipped entirely")
        print_result(data)
    else:
        job_id = data["job_id"]
        print(f"  Cache miss - polling for result...")
        result_data = poll_job(job_id)
        elapsed = time.time() - start
        print(f"  Completed in {elapsed:.2f}s")
        print_result(result_data)

    # run 3 - different code to show fresh review
    print("\n[3] Submitting different code (fresh LLM review)...")
    different_code = "def add(a, b):\n    return a + b"
    start = time.time()
    data = submit_job(different_code, LANGUAGE)

    if data.get("cached"):
        elapsed = time.time() - start
        print(f"  Cache HIT in {elapsed:.3f}s")
        print_result(data)
    else:
        job_id = data["job_id"]
        print(f"  Job ID: {job_id}")
        print(f"  Polling for result...")
        result_data = poll_job(job_id)
        elapsed = time.time() - start
        print(f"  Completed in {elapsed:.2f}s | status: {result_data['status']}")
        print_result(result_data)

    print("\n" + "=" * 50)
    print("Demo complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()