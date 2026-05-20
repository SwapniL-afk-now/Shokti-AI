"""Generation router: trigger Gemini MCQ generation for a topic."""
import uuid
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from shokti.api.deps import get_current_student_dep
from shokti.api.models import Student
from shokti.api.schemas import GenerateTopicRequest, GenerateJobResponse

router = APIRouter(prefix="/api/generate", tags=["generate"])

# Thread-safe in-memory job store
_jobs: dict[str, dict] = {}
_jobs_lock = asyncio.Lock()


def _run_generation(job_id: str, req: GenerateTopicRequest) -> None:
    """Background task — runs after request is sent. Updates _jobs dict."""
    import sqlite3
    from shokti.api.db import async_session
    from shokti.generators.gap_filler import (
        setup_generator,
        generate_fresh_mcqs,
    )

    # Sync write to _jobs — but since this runs in a background thread (not async),
    # we use a synchronous lock approach. For multi-worker, replace with Redis.
    import threading
    with threading.Lock():
        _jobs[job_id]["status"] = "running"

    try:
        client, store_name, gen_config, cite_config = setup_generator()
        conn = sqlite3.connect(__import__("shokti.core.config", fromlist=["DB_PATH"]).DB_PATH)
        conn.row_factory = sqlite3.Row
        count = generate_fresh_mcqs(
            topic_name=req.topic_name,
            chapter_id=req.chapter_id,
            chapter_name=req.chapter_name,
            book_page_range=req.book_page_range,
            source_file=req.source_file,
            count=req.count,
            conn=conn,
            client=client,
            store_name=store_name,
            gen_config=gen_config,
            cite_config=cite_config,
        )
        conn.close()
        with threading.Lock():
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["message"] = f"Generated {count} MCQs"
    except Exception as e:
        with threading.Lock():
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["message"] = str(e)


@router.post("/topic", response_model=GenerateJobResponse)
async def generate_topic(
    req: GenerateTopicRequest,
    background_tasks: BackgroundTasks,
    student: Student = Depends(get_current_student_dep),
):
    job_id = str(uuid.uuid4())
    async with _jobs_lock:
        _jobs[job_id] = {"status": "pending", "message": "Queued"}
    background_tasks.add_task(_run_generation, job_id, req)
    return GenerateJobResponse(job_id=job_id, status="pending", message="Queued")


@router.get("/status/{job_id}", response_model=GenerateJobResponse)
async def get_job_status(
    job_id: str,
    student: Student = Depends(get_current_student_dep),
):
    async with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return GenerateJobResponse(job_id=job_id, status=job["status"], message=job["message"])