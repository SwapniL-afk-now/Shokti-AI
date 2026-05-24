"""Exam router: list exams, start sessions, submit attempts, and fetch feedback."""
import asyncio
import json
import logging
import re
import sqlite3
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from fastapi.security import HTTPBearer
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shokti.api.auth import get_current_student
from shokti.api.db import async_session
from shokti.api.deps import get_current_student_dep, get_db_dep
from shokti.core.config import DB_PATH, MCQ
from shokti.api.models import Student
from shokti.api.schemas import (
    ExamAnswerDetailWithPractice,
    ExamAnswerSubmission,
    ExamAttemptDetail,
    ExamAttemptListItem,
    ExamDetailResponse,
    ExamFeedback,
    ExamFeedbackStatusResponse,
    ExamListItem,
    ExamStartResponse,
    ExamSubmissionResponseWithFeedback,
    ExamSubmitRequest,
    MCQListItem,
    RelatedPracticeQuestion,
    StrongTopicFeedback,
    WeakTopicFeedback,
)
from shokti.core.config import ENV_FILE
from shokti.exams.exam_config import get_exam_files, load_exam
from shokti.services.exam_feedback_service import ExamFeedbackService

security_opt = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/exams", tags=["exams"])
exam_feedback_adapter = TypeAdapter(ExamFeedback)

CONFIDENCE_LUCKY_GUESS = 1
CONFIDENCE_MASTER = 2
CONFIDENCE_MISTAKE = 3
CONFIDENCE_NO_KNOWLEDGE = 4


@router.get("", response_model=list[ExamListItem])
async def list_exams(
    db: AsyncSession = Depends(get_db_dep),
    credentials=Depends(security_opt),
):
    student = None
    if credentials:
        try:
            student = await get_current_student(credentials.credentials, db)
        except Exception:
            pass

    completed_sessions = set()
    attempt_stats: dict[str, dict] = {}
    if student:
        result = await db.execute(
            text("""
                SELECT DISTINCT session_type
                FROM student_answer_log
                WHERE student_id = :sid AND session_type LIKE 'exam%'
            """),
            {"sid": student.id},
        )
        completed_sessions = {row[0] for row in result.fetchall()}

        attempts_result = await db.execute(
            text("""
                SELECT ea.exam_id,
                       COUNT(*) AS attempt_count,
                       (
                           SELECT latest.attempt_id
                           FROM exam_attempts latest
                           WHERE latest.student_id = ea.student_id AND latest.exam_id = ea.exam_id
                           ORDER BY latest.submitted_at DESC
                           LIMIT 1
                       ) AS latest_attempt_id,
                       (
                           SELECT latest.score_percentage
                           FROM exam_attempts latest
                           WHERE latest.student_id = ea.student_id AND latest.exam_id = ea.exam_id
                           ORDER BY latest.submitted_at DESC
                           LIMIT 1
                       ) AS latest_score_percentage
                FROM exam_attempts ea
                WHERE ea.student_id = :sid
                GROUP BY ea.exam_id, ea.student_id
            """),
            {"sid": student.id},
        )
        attempt_stats = {
            row.exam_id: {
                "attempt_count": row.attempt_count or 0,
                "latest_attempt_id": row.latest_attempt_id,
                "latest_score_percentage": row.latest_score_percentage,
            }
            for row in attempts_result.fetchall()
        }

    files = get_exam_files()
    exams = []
    for exam_id in files:
        exam = load_exam(exam_id)
        stats = attempt_stats.get(exam_id, {})
        is_completed = bool(stats.get("attempt_count")) or f"exam{exam_id}" in completed_sessions
        exams.append(ExamListItem(
            exam_id=exam_id,
            title=exam.get("title", f"Exam {exam_id}"),
            mcq_count=exam.get("total_mcqs", 30),
            duration_minutes=exam.get("duration_minutes", 30),
            is_completed=is_completed,
            attempt_count=stats.get("attempt_count", 0),
            latest_attempt_id=stats.get("latest_attempt_id"),
            latest_score_percentage=stats.get("latest_score_percentage"),
        ))
    return exams


@router.get("/{exam_id}/attempts", response_model=list[ExamAttemptListItem])
async def list_exam_attempts(
    exam_id: str = Path(pattern=r"^\d+$"),
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    files = get_exam_files()
    if exam_id not in files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")

    result = await db.execute(
        text("""
            SELECT attempt_id, exam_id, exam_title, total, correct, score_percentage,
                   time_taken_seconds, feedback_status, submitted_at
            FROM exam_attempts
            WHERE student_id = :sid AND exam_id = :exam_id
            ORDER BY submitted_at DESC
        """),
        {"sid": student.id, "exam_id": exam_id},
    )
    return [ExamAttemptListItem(**dict(row._mapping)) for row in result.fetchall()]


@router.get("/attempts/{attempt_id}", response_model=ExamAttemptDetail)
async def get_exam_attempt(
    attempt_id: str,
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    row = await _get_attempt_row(db, student.id, attempt_id)
    return _attempt_row_to_detail(row)


@router.get("/attempts/{attempt_id}/feedback", response_model=ExamFeedbackStatusResponse)
async def get_exam_attempt_feedback(
    attempt_id: str,
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    row = await _get_attempt_row(db, student.id, attempt_id)
    return ExamFeedbackStatusResponse(
        attempt_id=row.attempt_id,
        feedback_status=row.feedback_status or "pending",
        feedback=_parse_feedback(row.feedback_json),
        feedback_source=row.feedback_source,
        feedback_error=row.feedback_error,
    )


@router.get("/{exam_id}", response_model=ExamDetailResponse)
async def get_exam(exam_id: str = Path(pattern=r"^\d+$")):
    files = get_exam_files()
    if exam_id not in files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    exam = load_exam(exam_id)
    return ExamDetailResponse(
        exam_id=exam_id,
        title=exam.get("title", f"Exam {exam_id}"),
        mcq_count=exam.get("total_mcqs", 30),
        duration_minutes=exam.get("duration_minutes", 30),
        description=exam.get("description"),
        instructions=exam.get("instructions"),
    )


logger = logging.getLogger(__name__)


def _should_generate_for_exam(exam_id: str) -> bool:
    """Return True if Gemini generation is enabled for this exam type."""
    if not getattr(MCQ, "ENABLE_GEMINI_GENERATION_FOR_EXAMS", True):
        return False
    if exam_id in ("1", "2", "3"):
        return getattr(MCQ, "ENABLE_GEMINI_GENERATION_FOR_FIXED_EXAMS", True)
    # All other exam IDs (including 'mock' references via startShortcutExam) use mock test logic
    return getattr(MCQ, "ENABLE_GEMINI_GENERATION_FOR_MOCK_TEST", True)


async def _generate_exam_mcqs(db: AsyncSession, exam: dict) -> list:
    """
    Generate MCQs for an exam using Gemini. Runs in a thread pool since gap_filler
    uses synchronous sqlite3. Returns list of MCQListItem dicts on success,
    or raises an exception on failure.
    """
    target_count = exam.get("total_mcqs", 30)
    chapter_ids = exam.get("chapter_ids", [])
    chapter_names = exam.get("chapter_names", [])
    # Build a topic filter from available topics in DB, or use first available
    topic_filter = None
    if chapter_ids:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT topic_id, topic_name, chapter_id, chapter_name, book_page_range, source_file "
            "FROM question_bank WHERE chapter_id = ? LIMIT 1",
            (chapter_ids[0],),
        ).fetchone()
        conn.close()
        if row:
            topic_filter = dict(row)

    if not topic_filter:
        raise RuntimeError("No topic data available for generation")

    def _worker() -> list[dict]:
        from shokti.generators.gap_filler import generate_fresh_mcqs, setup_generator
        import json

        worker_conn = sqlite3.connect(DB_PATH)
        worker_conn.row_factory = sqlite3.Row
        try:
            client, store_name, gen_config, cite_config = setup_generator()
            _, mcq_rows = generate_fresh_mcqs(
                topic_name=topic_filter["topic_name"],
                chapter_id=topic_filter["chapter_id"],
                chapter_name=topic_filter["chapter_name"],
                book_page_range=topic_filter.get("book_page_range") or "",
                source_file=topic_filter.get("source_file") or "",
                count=target_count,
                conn=worker_conn,
                client=client,
                store_name=store_name,
                gen_config=gen_config,
                cite_config=cite_config,
                practice_context=None,
                allow_existing_fallback=False,
            )
            return mcq_rows
        finally:
            worker_conn.close()

    logger.info(f"Starting Gemini question generation for exam {exam.get('exam_id', '?')}")
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_worker)
        timeout = getattr(MCQ, "FRESH_GENERATION_MAX_WAIT_SECONDS", 120)
        mcq_rows = future.result(timeout=timeout)
        executor.shutdown(wait=False, cancel_futures=True)
    except TimeoutError:
        executor.shutdown(wait=False, cancel_futures=True)
        logger.warning(f"Gemini generation timed out for exam {exam.get('exam_id', '?')}")
        raise RuntimeError("Generation timed out")
    except Exception as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        logger.warning(f"Gemini generation failed for exam {exam.get('exam_id', '?')}: {exc}")
        raise

    logger.info(f"Generated {len(mcq_rows)} questions from Gemini for exam {exam.get('exam_id', '?')}")

    # Normalize rows into MCQListItem format
    result = []
    for m in mcq_rows:
        opts = m.get("options")
        if isinstance(opts, str):
            opts = json.loads(opts)
        ca = m.get("correct_answer")
        if isinstance(ca, str):
            ca = json.loads(ca)
        result.append({
            "id": m["id"],
            "chapter_id": m.get("chapter_id") or "",
            "chapter_name": m.get("chapter_name") or "",
            "topic_id": m.get("topic_id") or "",
            "topic_name": m.get("topic_name") or "",
            "difficulty": m.get("difficulty", "medium"),
            "book_page_range": m.get("book_page_range") or "",
        })
    return result


@router.post("/{exam_id}/start", response_model=ExamStartResponse)
async def start_exam(
    exam_id: str = Path(pattern=r"^\d+$"),
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    files = get_exam_files()
    if exam_id not in files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")

    exam = load_exam(exam_id)

    # Attempt Gemini generation first
    if _should_generate_for_exam(exam_id):
        try:
            rows = await _generate_exam_mcqs(db, exam)
            if rows:
                return ExamStartResponse(
                    session_id=str(uuid.uuid4()),
                    exam_id=exam_id,
                    mcqs=[MCQListItem.model_validate(r) for r in rows],
                    duration_minutes=exam.get("duration_minutes", 30),
                )
        except Exception as e:
            logger.warning(f"Gemini generation failed for exam {exam_id}, falling back to DB: {e}")

    # Fallback: load from DB
    rows = await _load_fixed_exam_mcqs(db, exam)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not enough questions are available yet. Please add questions or try again later.",
        )
    return ExamStartResponse(
        session_id=str(uuid.uuid4()),
        exam_id=exam_id,
        mcqs=[MCQListItem.model_validate(dict(row._mapping)) for row in rows],
        duration_minutes=exam.get("duration_minutes", 30),
    )


@router.post("/{exam_id}/submit", response_model=ExamSubmissionResponseWithFeedback)
async def submit_exam(
    exam_id: str = Path(pattern=r"^\d+$"),
    submission: ExamSubmitRequest | list[ExamAnswerSubmission] = Body(...),
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    files = get_exam_files()
    if exam_id not in files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")

    exam = load_exam(exam_id)
    if isinstance(submission, list):
        answers = submission
        session_id = str(uuid.uuid4())
        time_taken_seconds = 0
    else:
        answers = submission.answers
        session_id = submission.session_id or str(uuid.uuid4())
        time_taken_seconds = submission.time_taken_seconds

    mcq_meta = await _load_mcq_meta(db, [answer.mcq_id for answer in answers])
    prq_map = _build_related_question_map(mcq_meta)
    # Track seen texts globally so related questions don't repeat across the review page
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
                "session_type": f"exam{exam_id}",
                "session_id": session_id,
                "selected_option": selected_option,
            },
        )

        chapter = row.get("chapter_name") or "Unknown"
        topic = row.get("topic_name") or "Unknown"
        chapter_results.setdefault(chapter, {}).setdefault(topic, _empty_topic_result())
        chapter_results[chapter][topic]["total"] += 1
        if is_correct:
            chapter_results[chapter][topic]["correct"] += 1
        _apply_timing_to_topic_result(chapter_results[chapter][topic], time_spent, confidence_rating)

        # Fixed exam questions are stable, so always attach their stable related
        # practice set instead of only showing practice after a wrong answer.
        related_prqs = []
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
            "exam_title": exam.get("title", f"Exam {exam_id}"),
            "exam_kind": "fixed_model_test",
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
        exam_title=exam.get("title", f"Exam {exam_id}"),
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


async def _load_fixed_exam_mcqs(db: AsyncSession, exam: dict):
    mcq_ids = [int(mid) for mid in exam.get("mcq_ids", []) if str(mid).isdigit()]
    if mcq_ids:
        placeholders = ",".join(f":id{i}" for i in range(len(mcq_ids)))
        order_cases = " ".join(f"WHEN :id{i} THEN {i}" for i in range(len(mcq_ids)))
        result = await db.execute(
            text(f"""
                SELECT id, chapter_id, chapter_name, topic_id, topic_name,
                       difficulty, book_page_range
                FROM question_bank
                WHERE id IN ({placeholders})
                ORDER BY CASE id {order_cases} ELSE {len(mcq_ids)} END
            """),
            {f"id{i}": mid for i, mid in enumerate(mcq_ids)},
        )
        return result.fetchall()

    mcq_count = exam.get("total_mcqs", 30)
    chapter_ids = exam.get("chapter_ids", [])
    topic_ids = exam.get("topic_ids", [])
    conditions = []
    params: dict = {}
    if chapter_ids:
        placeholders = ",".join(f":ch{i}" for i in range(len(chapter_ids)))
        conditions.append(f"chapter_id IN ({placeholders})")
        for i, cid in enumerate(chapter_ids):
            params[f"ch{i}"] = cid
    if topic_ids:
        placeholders = ",".join(f":t{i}" for i in range(len(topic_ids)))
        conditions.append(f"topic_id IN ({placeholders})")
        for i, tid in enumerate(topic_ids):
            params[f"t{i}"] = tid

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    params["limit"] = mcq_count
    result = await db.execute(
        text(f"""
            SELECT id, chapter_id, chapter_name, topic_id, topic_name,
                   difficulty, book_page_range
            FROM question_bank
            {where_clause}
            ORDER BY
                CASE difficulty
                    WHEN 'easy' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'hard' THEN 3
                    ELSE 2
                END,
                RANDOM()
            LIMIT :limit
        """),
        params,
    )
    return result.fetchall()


async def _load_mcq_meta(db: AsyncSession, mcq_ids: list[int]) -> dict[int, dict]:
    if not mcq_ids:
        return {}

    placeholders = ",".join(f":id{i}" for i in range(len(mcq_ids)))
    result = await db.execute(
        text(f"""
            SELECT id, chapter_id, chapter_name, topic_id, topic_name,
                   question, correct_answer, practice_related_questions
            FROM question_bank
            WHERE id IN ({placeholders})
        """),
        {f"id{i}": mid for i, mid in enumerate(mcq_ids)},
    )
    return {dict(row._mapping)["id"]: dict(row._mapping) for row in result.fetchall()}


async def _get_student_median_time(db: AsyncSession, student_id: str, new_times: list[int]) -> float:
    result = await db.execute(
        text("""
            SELECT time_spent_seconds
            FROM student_answer_log
            WHERE student_id = :sid AND time_spent_seconds IS NOT NULL AND time_spent_seconds > 0
        """),
        {"sid": student_id},
    )
    times = [int(row.time_spent_seconds) for row in result.fetchall() if row.time_spent_seconds]
    times.extend(int(t) for t in new_times if t and t > 0)
    if not times:
        return 0.0
    times.sort()
    return float(times[len(times) // 2])


def _classify_confidence(is_correct: bool, time_spent: int, median_time: float) -> int | None:
    if time_spent <= 0 or median_time <= 0:
        return None
    is_fast = time_spent <= median_time
    if is_correct and is_fast:
        return CONFIDENCE_LUCKY_GUESS
    if is_correct:
        return CONFIDENCE_MASTER
    if is_fast:
        return CONFIDENCE_MISTAKE
    return CONFIDENCE_NO_KNOWLEDGE


def _empty_topic_result() -> dict[str, int]:
    return {
        "total": 0,
        "correct": 0,
        "time_total_seconds": 0,
        "timed_count": 0,
        "lucky_guess_count": 0,
        "confident_master_count": 0,
        "confident_mistake_count": 0,
        "no_knowledge_count": 0,
    }


def _apply_timing_to_topic_result(topic_result: dict, time_spent: int, confidence_rating: int | None) -> None:
    if time_spent > 0:
        topic_result["time_total_seconds"] = topic_result.get("time_total_seconds", 0) + time_spent
        topic_result["timed_count"] = topic_result.get("timed_count", 0) + 1
    if confidence_rating == CONFIDENCE_LUCKY_GUESS:
        topic_result["lucky_guess_count"] = topic_result.get("lucky_guess_count", 0) + 1
    elif confidence_rating == CONFIDENCE_MASTER:
        topic_result["confident_master_count"] = topic_result.get("confident_master_count", 0) + 1
    elif confidence_rating == CONFIDENCE_MISTAKE:
        topic_result["confident_mistake_count"] = topic_result.get("confident_mistake_count", 0) + 1
    elif confidence_rating == CONFIDENCE_NO_KNOWLEDGE:
        topic_result["no_knowledge_count"] = topic_result.get("no_knowledge_count", 0) + 1


def _topic_result_to_breakdown(chapter_results: dict[str, dict[str, dict]]) -> list[dict]:
    breakdown = []
    for chapter, topics in chapter_results.items():
        for topic, values in topics.items():
            timed_count = values.get("timed_count", 0) or 0
            avg_time = (values.get("time_total_seconds", 0) / timed_count) if timed_count else 0.0
            breakdown.append({
                "chapter": chapter,
                "topic": topic,
                "total": values.get("total", 0),
                "correct": values.get("correct", 0),
                "avg_time_seconds": avg_time,
                "lucky_guess_count": values.get("lucky_guess_count", 0),
                "confident_master_count": values.get("confident_master_count", 0),
                "confident_mistake_count": values.get("confident_mistake_count", 0),
                "no_knowledge_count": values.get("no_knowledge_count", 0),
            })
    return breakdown


def _normalize_question_text(text: str) -> str:
    if not text:
        return ""
    # Normalize unicode (NFC → NFC, collapse all-composed forms)
    text = unicodedata.normalize("NFC", text)
    # Lowercase
    text = text.lower()
    # Collapse whitespace/newlines to single spaces
    text = re.sub(r"\s+", " ", text)
    # Strip leading/trailing whitespace
    return text.strip()


def _is_disallowed_related_practice_question(text: str) -> bool:
    """Reject bare objective stems that do not work as related practice prompts."""
    normalized = _normalize_question_text(text)
    if not normalized:
        return True

    disallowed_patterns = (
        "কোনটি",
        "কোনটিকে",
        "কোনটিতে",
        "কোন ধরনের",
        "নিচের কোনটি",
        "নিম্নের কোনটি",
        "নিচের কোনটিকে",
        "নিম্নের কোনটিকে",
        "which one",
        "which of the following",
    )
    return any(pattern in normalized for pattern in disallowed_patterns)


def _dedupe_related_questions(
    questions: list[str],
    original_question: str = "",
    seen_texts: set[str] | None = None,
    max_items: int = 3,
) -> list[str]:
    """
    Deduplicate a list of related practice question strings.

    - Removes null/empty values
    - Removes duplicates by normalized text (case-insensitive, whitespace-collapsed)
    - Removes questions matching the original question text
    - Tracks globally-seen texts to avoid repeats across the review page
    - Returns at most `max_items` unique questions
    """
    if seen_texts is None:
        seen_texts = set()

    original_norm = _normalize_question_text(original_question)
    if original_norm:
        seen_texts.add(original_norm)

    unique: list[str] = []
    for q in questions:
        if not q or not q.strip():
            continue
        norm = _normalize_question_text(q)
        if not norm:
            continue
        if norm in seen_texts:
            continue
        seen_texts.add(norm)
        unique.append(q)
        if len(unique) >= max_items:
            break

    return unique


async def _get_related_practice_questions(
    db: AsyncSession,
    mcq_id: int,
    original_question_text: str,
    seen_prq_texts: set[str],
    limit: int = 2,
) -> list[RelatedPracticeQuestion]:
    """
    Fetch related practice questions for a given MCQ from both qbank and generated sources.
    Returns up to `limit` unique RelatedPracticeQuestion objects.
    """
    # Get topic/chapter filters from the original MCQ
    row = await db.execute(
        text("""
            SELECT chapter_id, chapter_name, topic_id, topic_name,
                   subject, difficulty
            FROM question_bank
            WHERE id = :mcq_id
        """),
        {"mcq_id": mcq_id},
    )
    orig = row.fetchone()
    if not orig:
        return []

    orig_dict = dict(orig._mapping)
    base_conditions = ["id != :mcq_id", "question IS NOT NULL", "TRIM(question) != ''"]
    base_params = {"mcq_id": mcq_id}

    scopes: list[tuple[str, dict]] = []
    if orig_dict.get("topic_id"):
        scopes.append(("topic_id = :topic_id", {"topic_id": orig_dict["topic_id"]}))
    if orig_dict.get("chapter_id"):
        scopes.append(("chapter_id = :chapter_id", {"chapter_id": orig_dict["chapter_id"]}))
    if orig_dict.get("subject"):
        scopes.append(("subject = :subject", {"subject": orig_dict["subject"]}))
    scopes.append(("1 = 1", {}))

    candidates_raw = []
    for scope_condition, scope_params in scopes:
        params = {**base_params, **scope_params}
        result = await db.execute(
            text(f"""
                SELECT id, chapter_id, chapter_name, topic_id, topic_name,
                       question, options, correct_answer,
                       subject, difficulty, origin
                FROM question_bank
                WHERE {' AND '.join(base_conditions + [scope_condition])}
                ORDER BY
                    CASE
                        WHEN origin = 'question_bank' THEN 1
                        WHEN origin = 'generated' THEN 2
                        ELSE 3
                    END,
                    CASE difficulty
                        WHEN 'easy' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'hard' THEN 3
                        ELSE 2
                    END,
                    id
                LIMIT 80
            """),
            params,
        )
        candidates_raw = result.fetchall()
        if candidates_raw:
            break

    logger.info(f"Fetching related practice questions: {len(candidates_raw)} candidates for mcq_id={mcq_id}")

    # Build RelatedPracticeQuestion objects and deduplicate
    original_norm = _normalize_question_text(original_question_text)
    if original_norm:
        seen_prq_texts.add(original_norm)

    unique_by_id: dict[int, RelatedPracticeQuestion] = {}
    deferred_seen_candidates: list[RelatedPracticeQuestion] = []

    for r in candidates_raw:
        rd = dict(r._mapping)
        q_id = rd["id"]
        q_text = rd.get("question", "")
        q_norm = _normalize_question_text(q_text)

        if q_id in unique_by_id:
            continue
        if _is_disallowed_related_practice_question(q_text):
            continue

        # Parse options and correct_answer
        try:
            opts = json.loads(rd["options"]) if isinstance(rd["options"], str) else (rd["options"] or {})
        except Exception:
            opts = {}
        try:
            ca = json.loads(rd["correct_answer"]) if isinstance(rd["correct_answer"], str) else (rd["correct_answer"] or {})
        except Exception:
            ca = {}

        rp_q = RelatedPracticeQuestion(
            id=q_id,
            source="qbank" if (rd.get("origin") or "question_bank") == "question_bank" else (rd.get("origin") or "qbank"),
            question=q_text,
            options=opts,
            correct_answer=ca,
            subject=rd.get("subject"),
            chapter=rd.get("chapter_name"),
            topic=rd.get("topic_name"),
            difficulty=rd.get("difficulty"),
        )

        if q_norm in seen_prq_texts:
            deferred_seen_candidates.append(rp_q)
            continue

        unique_by_id[q_id] = rp_q
        seen_prq_texts.add(q_norm)

        if len(unique_by_id) >= limit:
            break

    # Never show an empty related section just because earlier questions already
    # consumed the closest texts. Reuse deterministic candidates only as a last resort.
    if len(unique_by_id) < limit:
        for rp_q in deferred_seen_candidates:
            if rp_q.id in unique_by_id:
                continue
            unique_by_id[rp_q.id] = rp_q
            if len(unique_by_id) >= limit:
                break

    result_list = list(unique_by_id.values())
    logger.info(
        f"Combined related candidate count: {len(candidates_raw)}, "
        f"Unique related candidate count after dedupe: {len(result_list)}, "
        f"Returning {len(result_list)} related questions for question ID: {mcq_id}"
    )
    return result_list


def _build_related_question_map(
    mcq_meta: dict[int, dict],
    seen_prq_texts: set[str] | None = None,
) -> dict[int, list[str]]:
    if seen_prq_texts is None:
        seen_prq_texts = set()
    prq_map = {}
    for mcq_id, meta in mcq_meta.items():
        try:
            raw = meta.get("practice_related_questions", "[]")
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
            if not isinstance(parsed, list):
                parsed = []
        except (json.JSONDecodeError, TypeError):
            parsed = []
        # Deduplicate: remove empty/null, normalize, dedupe within list and against global seen set
        original_norm = _normalize_question_text(meta.get("question", ""))
        if original_norm:
            seen_prq_texts.add(original_norm)
        unique = []
        for q in parsed:
            norm = _normalize_question_text(q)
            if norm and norm not in seen_prq_texts:
                seen_prq_texts.add(norm)
                unique.append(q)
                if len(unique) >= 3:
                    break
        prq_map[mcq_id] = unique
    return prq_map


async def _fill_missing_related_practice(
    db: AsyncSession,
    mcq_meta: dict[int, dict],
    prq_map: dict[int, list[str]],
    seen_prq_texts: set[str] | None = None,
) -> None:
    if seen_prq_texts is None:
        seen_prq_texts = set()
    for mcq_id, meta in mcq_meta.items():
        if prq_map.get(mcq_id):
            continue

        params = {"mcq_id": mcq_id}
        conditions = ["id != :mcq_id", "question IS NOT NULL", "TRIM(question) != ''"]
        topic_id = meta.get("topic_id")
        chapter_id = meta.get("chapter_id")

        if topic_id:
            conditions.append("topic_id = :topic_id")
            params["topic_id"] = topic_id
        elif chapter_id:
            conditions.append("chapter_id = :chapter_id")
            params["chapter_id"] = chapter_id
        else:
            prq_map[mcq_id] = []
            continue

        result = await db.execute(
            text(f"""
                SELECT question
                FROM question_bank
                WHERE {' AND '.join(conditions)}
                ORDER BY
                    CASE difficulty
                        WHEN 'easy' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'hard' THEN 3
                        ELSE 2
                    END,
                    id
                LIMIT 6
            """),
            params,
        )
        raw_questions = [row.question for row in result.fetchall() if row.question]
        # Deduplicate against seen texts and within this MCQ's list
        original_norm = _normalize_question_text(meta.get("question", ""))
        if original_norm:
            seen_prq_texts.add(original_norm)
        unique = []
        for q in raw_questions:
            norm = _normalize_question_text(q)
            if norm and norm not in seen_prq_texts:
                seen_prq_texts.add(norm)
                unique.append(q)
                if len(unique) >= 3:
                    break
        prq_map[mcq_id] = unique


async def _get_attempt_row(db: AsyncSession, student_id: str, attempt_id: str):
    result = await db.execute(
        text("""
            SELECT *
            FROM exam_attempts
            WHERE attempt_id = :attempt_id AND student_id = :student_id
        """),
        {"attempt_id": attempt_id, "student_id": student_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam attempt not found")
    return row


def _parse_feedback(raw: str | None) -> ExamFeedback | None:
    if not raw:
        return None
    try:
        return exam_feedback_adapter.validate_python(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _attempt_row_to_detail(row) -> ExamAttemptDetail:
    details = [
        ExamAnswerDetailWithPractice(**item)
        for item in json.loads(row.details_json or "[]")
    ]
    return ExamAttemptDetail(
        attempt_id=row.attempt_id,
        exam_id=row.exam_id,
        exam_title=row.exam_title,
        session_id=row.session_id,
        time_taken_seconds=row.time_taken_seconds or 0,
        total=row.total or 0,
        correct=row.correct or 0,
        score_percentage=row.score_percentage or 0.0,
        details=details,
        topic_breakdown=json.loads(row.topic_breakdown_json or "[]"),
        feedback_status=row.feedback_status or "pending",
        feedback=_parse_feedback(row.feedback_json),
        submitted_at=row.submitted_at,
        feedback_source=row.feedback_source,
        feedback_error=row.feedback_error,
    )


async def _generate_and_store_feedback(
    attempt_id: str,
    total: int,
    correct: int,
    score_pct: float,
    chapter_results: list[dict],
) -> None:
    feedback = None
    source = "gemini"
    error = None
    try:
        api_key = _load_gemini_api_key()
        service = ExamFeedbackService(api_key)
        feedback = service.get_feedback(total, correct, score_pct, chapter_results)
    except Exception as exc:
        import logging
        logging.warning("ExamFeedbackService failed: %s", exc)
        source = "local_fallback"
        error = str(exc)

    if feedback is None:
        feedback = _build_local_exam_feedback(total, correct, score_pct, chapter_results)
        source = "local_fallback"

    async with async_session() as db:
        await db.execute(
            text("""
                UPDATE exam_attempts
                SET feedback_status = 'ready',
                    feedback_source = :source,
                    feedback_error = :error,
                    feedback_json = :feedback_json,
                    feedback_updated_at = :feedback_updated_at
                WHERE attempt_id = :attempt_id
            """),
            {
                "attempt_id": attempt_id,
                "source": source,
                "error": error,
                "feedback_json": feedback.model_dump_json(),
                "feedback_updated_at": datetime.now(timezone.utc),
            },
        )
        await db.commit()


def _load_gemini_api_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "GEMINI_API_KEY":
            return value.strip().strip("\"'")
    raise RuntimeError("GEMINI_API_KEY not found in .env")


def _build_local_exam_feedback(
    total: int,
    correct: int,
    score_pct: float,
    chapter_results: list[dict],
) -> ExamFeedback:
    """Create deterministic feedback so every submitted exam has analysis."""
    weak_topics = []
    strong_topics = []

    for item in chapter_results:
        topic_total = item.get("total", 0) or 0
        topic_correct = item.get("correct", 0) or 0
        accuracy = (topic_correct / topic_total * 100) if topic_total else 0.0
        topic_name = item.get("topic") or "General"
        chapter_name = item.get("chapter") or "Unknown"

        if accuracy < 60:
            fast_wrong = item.get("confident_mistake_count", 0) or 0
            slow_wrong = item.get("no_knowledge_count", 0) or 0
            timing_tip = "Review the textbook explanation for the exact concept tested here."
            if fast_wrong > 0:
                timing_tip = "Slow down on this topic: your fast wrong answers suggest a confident misconception."
            elif slow_wrong > 0:
                timing_tip = "Rebuild the basics for this topic: slow wrong answers suggest a missing-knowledge gap."
            weak_topics.append(WeakTopicFeedback(
                topic_name=topic_name,
                chapter_name=chapter_name,
                accuracy_percentage=accuracy,
                focus_recommendations=[
                    timing_tip,
                    "Redo the wrong MCQs and say why each incorrect option is wrong.",
                    "Practice related questions from this topic before the next mock exam.",
                ],
            ))
        else:
            strong_topics.append(StrongTopicFeedback(
                topic_name=topic_name,
                chapter_name=chapter_name,
                accuracy_percentage=accuracy,
                encouragement="You are handling this topic well. Keep it warm with short review sessions.",
            ))

    weak_topics.sort(key=lambda topic: topic.accuracy_percentage)
    strong_topics.sort(key=lambda topic: topic.accuracy_percentage, reverse=True)

    if total == 0:
        summary = "No answers were submitted, so there is not enough data to analyze this exam."
    elif score_pct >= 80:
        summary = f"You answered {correct}/{total} correctly. Strong performance overall; focus on polishing the few gaps shown below."
    elif score_pct >= 60:
        summary = f"You answered {correct}/{total} correctly. The foundation is solid, and the fastest gains will come from fixing the weak topics below."
    else:
        summary = f"You answered {correct}/{total} correctly. Treat this as a diagnostic map: review the incorrect answers first, then drill the related practice questions."

    fast_wrong_total = sum(item.get("confident_mistake_count", 0) or 0 for item in chapter_results)
    slow_wrong_total = sum(item.get("no_knowledge_count", 0) or 0 for item in chapter_results)
    if fast_wrong_total:
        summary += f" {fast_wrong_total} fast wrong answer(s) suggest confident misconceptions to slow down and verify."
    if slow_wrong_total:
        summary += f" {slow_wrong_total} slow wrong answer(s) suggest knowledge gaps that need concept rebuilding."

    return ExamFeedback(
        overall_summary=summary,
        weak_topics=weak_topics[:5],
        strong_topics=strong_topics[:5],
        personalized_study_recommendations=[
            "Start with the wrong-answer review and write the reason for the correct option in one sentence.",
            "Spend 15 minutes on the weakest topic before attempting fresh questions.",
            "Reattempt this exam's incorrect questions after a short break without looking at the answers.",
            "Flag fast wrong answers as misconception drills and slow wrong answers as basics-first review.",
            "Use related practice questions to separate similar concepts that caused mistakes.",
        ],
    )
