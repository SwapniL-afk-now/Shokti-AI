"""Practice router: start session, submit answer."""
import uuid
import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import logging
logger = logging.getLogger(__name__)

from shokti.api.deps import get_db_dep, get_current_student_dep
from shokti.api.models import Student
from shokti.api.schemas import (
    ExamAnswerDetailWithPractice,
    ExamAnswerSubmission,
    ExamSubmissionResponseWithFeedback,
    ExamSubmitRequest,
    MCQListItem,
    PracticeSessionCreate,
    PracticeSessionResponse,
    AnswerSubmission,
    AnswerResponse,
)
from shokti.core.config import DB_PATH, MCQ as MCQ_CONFIG
from shokti.api.routers.exams import (
    _build_related_question_map,
    _apply_timing_to_topic_result,
    _classify_confidence,
    _empty_topic_result,
    _fill_missing_related_practice,
    _generate_and_store_feedback,
    _get_related_practice_questions,
    _get_student_median_time,
    _load_mcq_meta,
    _normalize_question_text,
    _topic_result_to_breakdown,
)

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

    if not mcqs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not enough questions are available yet. Please add questions or try another topic.",
        )

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


@router.post("/sessions/{session_id}/submit", response_model=ExamSubmissionResponseWithFeedback)
async def submit_practice_session(
    session_id: str,
    submission: ExamSubmitRequest | list[ExamAnswerSubmission] = Body(...),
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    if isinstance(submission, list):
        answers = submission
        time_taken_seconds = 0
    else:
        answers = submission.answers
        time_taken_seconds = submission.time_taken_seconds

    mcq_meta = await _load_mcq_meta(db, [answer.mcq_id for answer in answers])
    prq_map = _build_related_question_map(mcq_meta)
    seen_prq_texts: set[str] = set()
    await _fill_missing_related_practice(db, mcq_meta, prq_map, seen_prq_texts)
    median_time = await _get_student_median_time(db, student.id, [max(0, answer.time_spent_seconds or 0) for answer in answers])

    chapter_results: dict[str, dict[str, dict[str, int]]] = {}
    details = []
    correct_count = 0

    for answer in answers:
        row = mcq_meta.get(answer.mcq_id)
        if not row:
            continue

        correct_parsed = json.loads(row["correct_answer"]) if isinstance(row["correct_answer"], str) else (row["correct_answer"] or {})
        correct_opt = correct_parsed.get("option", "A") if isinstance(correct_parsed, dict) else "A"
        selected_option = (answer.selected_option or "").upper()
        time_spent = max(0, answer.time_spent_seconds or 0)
        is_correct = bool(selected_option) and selected_option == correct_opt.upper()
        confidence_rating = _classify_confidence(is_correct, time_spent, median_time)
        if is_correct:
            correct_count += 1

        await db.execute(
            text("""
                INSERT INTO student_answer_log (
                    student_id, mcq_id, is_correct, confidence_rating, answered_at,
                    time_spent_seconds, session_type, session_id, selected_option
                )
                VALUES (
                    :student_id, :mcq_id, :is_correct, :confidence_rating, :answered_at,
                    :time_spent_seconds, :session_type, :session_id, :selected_option
                )
            """),
            {
                "student_id": student.id,
                "mcq_id": answer.mcq_id,
                "is_correct": is_correct,
                "confidence_rating": confidence_rating,
                "answered_at": datetime.now(timezone.utc),
                "time_spent_seconds": time_spent,
                "session_type": "practice_exam",
                "session_id": session_id,
                "selected_option": selected_option,
            },
        )
        await _update_sm2(db, student.id, answer.mcq_id, is_correct)

        chapter = row.get("chapter_name") or "Unknown"
        topic = row.get("topic_name") or "Unknown"
        chapter_results.setdefault(chapter, {}).setdefault(topic, _empty_topic_result())
        chapter_results[chapter][topic]["total"] += 1
        if is_correct:
            chapter_results[chapter][topic]["correct"] += 1
        _apply_timing_to_topic_result(chapter_results[chapter][topic], time_spent, confidence_rating)

        related_prqs = []
        if not is_correct:
            try:
                related_prqs = await _get_related_practice_questions(
                    db, answer.mcq_id, row.get("question", ""), seen_prq_texts, limit=2
                )
            except Exception as e:
                logger.warning(f"Failed to fetch related questions for mcq_id={answer.mcq_id}: {e}")
                related_prqs = []

        details.append(ExamAnswerDetailWithPractice(
            mcq_id=answer.mcq_id,
            selected_option=selected_option,
            correct_option=correct_opt,
            is_correct=is_correct,
            time_spent_seconds=time_spent,
            confidence_rating=confidence_rating,
            practice_related_questions=related_prqs,
        ))

    total = len(details)
    score_pct = (correct_count / total * 100) if total > 0 else 0.0
    topic_breakdown = _topic_result_to_breakdown(chapter_results)

    attempt_id = str(uuid.uuid4())
    exam_id = f"practice-{session_id}"
    exam_title = "Practice Exam"
    await db.execute(
        text("""
            INSERT INTO exam_attempts (
                attempt_id, student_id, exam_id, exam_title, exam_kind, session_id,
                total, correct, score_percentage, time_taken_seconds, answers_json,
                details_json, topic_breakdown_json, feedback_status, submitted_at
            )
            VALUES (
                :attempt_id, :student_id, :exam_id, :exam_title, :exam_kind, :session_id,
                :total, :correct, :score_percentage, :time_taken_seconds, :answers_json,
                :details_json, :topic_breakdown_json, 'pending', :submitted_at
            )
        """),
        {
            "attempt_id": attempt_id,
            "student_id": student.id,
            "exam_id": exam_id,
            "exam_title": exam_title,
            "exam_kind": "practice_exam",
            "session_id": session_id,
            "total": total,
            "correct": correct_count,
            "score_percentage": score_pct,
            "time_taken_seconds": time_taken_seconds,
            "answers_json": json.dumps([answer.model_dump() for answer in answers], ensure_ascii=False),
            "details_json": json.dumps([detail.model_dump(mode='json') for detail in details], ensure_ascii=False),
            "topic_breakdown_json": json.dumps(topic_breakdown, ensure_ascii=False),
            "submitted_at": datetime.now(timezone.utc),
        },
    )
    await db.commit()

    asyncio.create_task(
        _generate_and_store_feedback(
            attempt_id,
            total,
            correct_count,
            score_pct,
            topic_breakdown,
        )
    )

    return ExamSubmissionResponseWithFeedback(
        attempt_id=attempt_id,
        exam_id=exam_id,
        exam_title=exam_title,
        session_id=session_id,
        time_taken_seconds=time_taken_seconds,
        total=total,
        correct=correct_count,
        score_percentage=score_pct,
        details=details,
        topic_breakdown=topic_breakdown,
        feedback_status="pending",
        feedback=None,
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
