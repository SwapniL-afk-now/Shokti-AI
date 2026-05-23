"""Exam router: list exams, start sessions, submit attempts, and fetch feedback."""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Path, status
from fastapi.security import HTTPBearer
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shokti.api.auth import get_current_student
from shokti.api.db import async_session
from shokti.api.deps import get_current_student_dep, get_db_dep
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
    StrongTopicFeedback,
    WeakTopicFeedback,
)
from shokti.core.config import ENV_FILE
from shokti.exams.exam_config import get_exam_files, load_exam
from shokti.services.exam_feedback_service import ExamFeedbackService

security_opt = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/exams", tags=["exams"])
exam_feedback_adapter = TypeAdapter(ExamFeedback)


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
    rows = await _load_fixed_exam_mcqs(db, exam)
    return ExamStartResponse(
        session_id=str(uuid.uuid4()),
        exam_id=exam_id,
        mcqs=[MCQListItem.model_validate(dict(row._mapping)) for row in rows],
        duration_minutes=exam.get("duration_minutes", 30),
    )


@router.post("/{exam_id}/submit", response_model=ExamSubmissionResponseWithFeedback)
async def submit_exam(
    background_tasks: BackgroundTasks,
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
    chapter_results: dict[str, dict[str, dict[str, int]]] = {}
    details = []
    correct_count = 0

    for answer in answers:
        row = mcq_meta.get(answer.mcq_id)
        if not row:
            continue

        correct_parsed = json.loads(row["correct_answer"]) if isinstance(row["correct_answer"], str) else (row["correct_answer"] or {})
        correct_opt = correct_parsed.get("option", "A") if isinstance(correct_parsed, dict) else "A"
        is_correct = answer.selected_option.upper() == correct_opt.upper()
        if is_correct:
            correct_count += 1

        await db.execute(
            text("""
                INSERT INTO student_answer_log (
                    student_id, mcq_id, is_correct, answered_at, session_type,
                    session_id, selected_option
                )
                VALUES (
                    :student_id, :mcq_id, :is_correct, :answered_at, :session_type,
                    :session_id, :selected_option
                )
            """),
            {
                "student_id": student.id,
                "mcq_id": answer.mcq_id,
                "is_correct": is_correct,
                "answered_at": datetime.now(timezone.utc),
                "session_type": f"exam{exam_id}",
                "session_id": session_id,
                "selected_option": answer.selected_option,
            },
        )

        chapter = row.get("chapter_name") or "Unknown"
        topic = row.get("topic_name") or "Unknown"
        chapter_results.setdefault(chapter, {}).setdefault(topic, {"total": 0, "correct": 0})
        chapter_results[chapter][topic]["total"] += 1
        if is_correct:
            chapter_results[chapter][topic]["correct"] += 1

        details.append(ExamAnswerDetailWithPractice(
            mcq_id=answer.mcq_id,
            selected_option=answer.selected_option,
            correct_option=correct_opt,
            is_correct=is_correct,
            practice_related_questions=[] if is_correct else prq_map.get(answer.mcq_id, []),
        ))

    total = len(details)
    score_pct = (correct_count / total * 100) if total > 0 else 0.0
    topic_breakdown = [
        {"chapter": chapter, "topic": topic, "total": values["total"], "correct": values["correct"]}
        for chapter, topics in chapter_results.items()
        for topic, values in topics.items()
    ]

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
            "details_json": json.dumps([detail.model_dump() for detail in details], ensure_ascii=False),
            "topic_breakdown_json": json.dumps(topic_breakdown, ensure_ascii=False),
            "submitted_at": datetime.now(timezone.utc),
        },
    )
    await db.commit()

    background_tasks.add_task(
        _generate_and_store_feedback,
        attempt_id,
        total,
        correct_count,
        score_pct,
        topic_breakdown,
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
                   correct_answer, practice_related_questions
            FROM question_bank
            WHERE id IN ({placeholders})
        """),
        {f"id{i}": mid for i, mid in enumerate(mcq_ids)},
    )
    return {dict(row._mapping)["id"]: dict(row._mapping) for row in result.fetchall()}


def _build_related_question_map(mcq_meta: dict[int, dict]) -> dict[int, list[str]]:
    prq_map = {}
    for mcq_id, meta in mcq_meta.items():
        try:
            raw = meta.get("practice_related_questions", "[]")
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or [])
            prq_map[mcq_id] = parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            prq_map[mcq_id] = []
    return prq_map


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
            weak_topics.append(WeakTopicFeedback(
                topic_name=topic_name,
                chapter_name=chapter_name,
                accuracy_percentage=accuracy,
                focus_recommendations=[
                    "Review the textbook explanation for the exact concept tested here.",
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

    return ExamFeedback(
        overall_summary=summary,
        weak_topics=weak_topics[:5],
        strong_topics=strong_topics[:5],
        personalized_study_recommendations=[
            "Start with the wrong-answer review and write the reason for the correct option in one sentence.",
            "Spend 15 minutes on the weakest topic before attempting fresh questions.",
            "Reattempt this exam's incorrect questions after a short break without looking at the answers.",
            "Use related practice questions to separate similar concepts that caused mistakes.",
            "Keep strong topics in rotation with quick spaced-repetition reviews.",
        ],
    )
