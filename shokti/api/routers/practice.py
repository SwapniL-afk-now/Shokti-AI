"""Practice router: start session, submit answer."""
import uuid
import json
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from shokti.api.deps import get_db_dep, get_current_student_dep
from shokti.api.models import Student
from shokti.api.schemas import (
    PracticeSessionCreate,
    PracticeSessionResponse,
    AnswerSubmission,
    AnswerResponse,
    MCQListItem,
)
from shokti.core.config import DB_PATH, MCQ as MCQ_CONFIG

router = APIRouter(prefix="/api/practice", tags=["practice"])


@router.post("/session", response_model=PracticeSessionResponse)
async def start_session(
    req: PracticeSessionCreate,
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    import sqlite3
    import asyncio
    from shokti.question_selectors.session_builder import build_session

    session_id = str(uuid.uuid4())

    def _sync_build():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return build_session(
                conn=conn,
                student_id=student.id,
                count=req.count,
                mode=req.mode,
                topic=req.topic_name,
                chapter=req.chapter_name,
            )
        finally:
            conn.close()

    mcqs, comp = await asyncio.to_thread(_sync_build)

    return PracticeSessionResponse(
        session_id=session_id,
        mode=req.mode,
        count=len(mcqs),
        mcqs=[
            MCQListItem(
                id=m["id"],
                chapter_id=m["chapter_id"] or "",
                chapter_name=m["chapter_name"] or "",
                topic_id=m["topic_id"] or "",
                topic_name=m["topic_name"] or "",
                difficulty=m.get("difficulty"),
                book_page_range=m.get("book_page_range") or "",
            )
            for m in mcqs
        ],
    )


@router.post("/sessions/{session_id}/answer", response_model=AnswerResponse)
async def submit_answer(
    session_id: str,
    req: AnswerSubmission,
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    # Fetch MCQ to get correct answer
    result = await db.execute(
        text("SELECT * FROM question_bank WHERE id = :id"),
        {"id": req.mcq_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCQ not found")

    r = dict(row._mapping)
    try:
        correct = json.loads(r["correct_answer"]) if isinstance(r["correct_answer"], str) else r["correct_answer"]
    except (json.JSONDecodeError, TypeError):
        correct = {"option": "A", "text": ""}
    correct_opt = correct.get("option", "A") if isinstance(correct, dict) else "A"
    is_correct = req.selected_option.upper() == correct_opt.upper()

    # Log answer
    await db.execute(
        text("""
            INSERT INTO student_answer_log
            (student_id, mcq_id, is_correct, answered_at, time_spent_seconds)
            VALUES (:student_id, :mcq_id, :is_correct, :answered_at, :time_spent)
        """),
        {
            "student_id": student.id,
            "mcq_id": req.mcq_id,
            "is_correct": is_correct,
            "answered_at": datetime.now(timezone.utc),
            "time_spent": req.time_spent_seconds or 0,
        },
    )

    # Update SM-2 stats
    await _update_sm2(db, student.id, req.mcq_id, is_correct)

    await db.commit()
    return AnswerResponse(
        mcq_id=req.mcq_id,
        is_correct=is_correct,
        correct_option=correct_opt,
        explanation=r.get("source_quote"),
    )


async def _update_sm2(db: AsyncSession, student_id: str, mcq_id: int, is_correct: bool) -> None:
    """Update SM-2 spaced repetition parameters after each answer."""
    result = await db.execute(
        text("SELECT * FROM student_mcq_stats WHERE student_id = :sid AND mcq_id = :mid"),
        {"sid": student_id, "mid": mcq_id},
    )
    existing = result.fetchone()

    if existing:
        r = existing._mapping
        ef = r["easiness_factor"]
        interval = r["interval_days"]
        correct_count = r["correct_count"]
        wrong_count = r["wrong_count"]
    else:
        ef = MCQ_CONFIG.SM2_INITIAL_EF
        interval = 0
        correct_count = 0
        wrong_count = 0

    if is_correct:
        correct_count += 1
        if interval == 0:
            interval = 1
        elif interval == 1:
            interval = 6
        else:
            interval = round(interval * ef)
        ef = max(1.3, ef + 0.1)
    else:
        wrong_count += 1
        interval = 1
        ef = max(1.3, ef - 0.2)

    next_review = datetime.now(timezone.utc).replace(microsecond=0)
    if interval > 0:
        next_review = next_review + timedelta(days=interval)

    if existing:
        await db.execute(
            text("""
                UPDATE student_mcq_stats
                SET correct_count = :cc, wrong_count = :wc,
                    easiness_factor = :ef, interval_days = :intv,
                    last_seen_at = :last, next_review_at = :next
                WHERE student_id = :sid AND mcq_id = :mid
            """),
            {
                "sid": student_id, "mid": mcq_id,
                "cc": correct_count, "wc": wrong_count,
                "ef": ef, "intv": interval,
                "last": datetime.now(timezone.utc), "next": next_review,
            },
        )
    else:
        await db.execute(
            text("""
                INSERT INTO student_mcq_stats
                (student_id, mcq_id, correct_count, wrong_count,
                 easiness_factor, interval_days, last_seen_at, next_review_at)
                VALUES (:sid, :mid, :cc, :wc, :ef, :intv, :last, :next)
            """),
            {
                "sid": student_id, "mid": mcq_id,
                "cc": correct_count, "wc": wrong_count,
                "ef": ef, "intv": interval,
                "last": datetime.now(timezone.utc), "next": next_review,
            },
        )