"""Exam router: list exams, start session, submit."""
import uuid
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Path, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from shokti.api.deps import get_db_dep, get_current_student_dep
from shokti.api.models import Student
from shokti.api.schemas import (
    ExamListItem,
    ExamDetailResponse,
    ExamStartResponse,
    ExamAnswerSubmission,
    ExamSubmissionResponse,
    ExamAnswerDetail,
    MCQListItem,
    ExamSubmissionResponseWithFeedback,
    ExamAnswerDetailWithPractice,
    ExamFeedback,
    StrongTopicFeedback,
    WeakTopicFeedback,
)
from shokti.exams.exam_config import load_exam, get_exam_files
from shokti.services.exam_feedback_service import ExamFeedbackService
from shokti.core.config import ENV_FILE

from fastapi.security import HTTPBearer
from shokti.api.auth import get_current_student

security_opt = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/api/exams", tags=["exams"])


@router.get("", response_model=list[ExamListItem])
async def list_exams(
    db: AsyncSession = Depends(get_db_dep),
    credentials = Depends(security_opt),
):
    student = None
    if credentials:
        try:
            student = await get_current_student(credentials.credentials, db)
        except Exception:
            pass

    completed_sessions = set()
    if student:
        result = await db.execute(
            text("""
                SELECT DISTINCT session_type
                FROM student_answer_log
                WHERE student_id = :sid AND session_type LIKE 'exam%'
            """),
            {"sid": student.id}
        )
        completed_sessions = {row[0] for row in result.fetchall()}

    files = get_exam_files()
    exams = []
    for exam_id, filename in files.items():
        exam = load_exam(exam_id)
        is_completed = f"exam{exam_id}" in completed_sessions
        exams.append(ExamListItem(
            exam_id=exam_id,
            title=exam.get("title", f"Exam {exam_id}"),
            mcq_count=exam.get("total_mcqs", 30),
            duration_minutes=exam.get("duration_minutes", 30),
            is_completed=is_completed,
        ))
    return exams


@router.get("/{exam_id}", response_model=ExamDetailResponse)
async def get_exam(exam_id: str = Path(pattern=r"^\d+$")):
    if not exam_id.isdigit():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="exam_id must be a positive integer")
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
    mcq_count = exam.get("total_mcqs", 30)

    # Fetch MCQs from question bank for this exam's chapter_ids / topic_ids
    chapter_ids = exam.get("chapter_ids", [])
    topic_ids = exam.get("topic_ids", [])

    from sqlalchemy import text
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

    query = f"""
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
    """
    params["limit"] = mcq_count
    result = await db.execute(text(query), params)
    rows = result.fetchall()

    session_id = str(uuid.uuid4())
    return ExamStartResponse(
        session_id=session_id,
        exam_id=exam_id,
        mcqs=[MCQListItem.model_validate(dict(r._mapping)) for r in rows],
        duration_minutes=exam.get("duration_minutes", 30),
    )


@router.post("/{exam_id}/submit", response_model=ExamSubmissionResponseWithFeedback)
async def submit_exam(
    exam_id: str = Path(pattern=r"^\d+$"),
    answers: list[ExamAnswerSubmission] = Body(...),
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    files = get_exam_files()
    if exam_id not in files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")

    exam = load_exam(exam_id)
    mcq_ids = [a.mcq_id for a in answers]

    if not mcq_ids:
        return ExamSubmissionResponseWithFeedback(total=0, correct=0, score_percentage=0.0, details=[], feedback=None)

    from sqlalchemy import text

    # Pre-fetch meta + practice_related_questions for all mcq_ids
    placeholders = ",".join(f":id{i}" for i in range(len(mcq_ids)))
    meta_result = await db.execute(
        text(f"""
            SELECT id, chapter_id, chapter_name, topic_id, topic_name,
                   correct_answer, practice_related_questions
            FROM question_bank
            WHERE id IN ({placeholders})
        """),
        {f"id{i}": mid for i, mid in enumerate(mcq_ids)},
    )
    mcq_meta = {}
    for row in meta_result.fetchall():
        row_dict = dict(row._mapping)
        mcq_meta[row_dict["id"]] = row_dict

    # Pre-fetch prq for wrong answers (will be filtered later)
    prq_map: dict[int, list[str]] = {}
    for mid, meta in mcq_meta.items():
        try:
            prq_raw = meta.get("practice_related_questions", "[]")
            prq = json.loads(prq_raw) if isinstance(prq_raw, str) else (prq_raw or [])
            prq_map[mid] = prq if isinstance(prq, list) else []
        except (json.JSONDecodeError, TypeError):
            prq_map[mid] = []

    # Aggregate chapter results
    chapter_results: dict[str, dict[str, dict[str, int]]] = {}

    details = []
    correct_count = 0
    for answer in answers:
        row = mcq_meta.get(answer.mcq_id)
        if not row:
            continue
        correct = row["correct_answer"]
        correct_parsed = json.loads(correct) if isinstance(correct, str) else (correct or {})
        correct_opt = correct_parsed.get("option", "A") if isinstance(correct_parsed, dict) else "A"
        is_correct = answer.selected_option.upper() == correct_opt.upper()
        if is_correct:
            correct_count += 1

        # Log answer
        await db.execute(
            text("""
                INSERT INTO student_answer_log (student_id, mcq_id, is_correct, answered_at, session_type, selected_option)
                VALUES (:student_id, :mcq_id, :is_correct, :answered_at, :session_type, :selected_option)
            """),
            {
                "student_id": student.id,
                "mcq_id": answer.mcq_id,
                "is_correct": is_correct,
                "answered_at": datetime.now(timezone.utc),
                "session_type": f"exam{exam_id}",
                "selected_option": answer.selected_option,
            },
        )

        # Track chapter results
        chapter = row.get("chapter_name") or "Unknown"
        topic = row.get("topic_name") or "Unknown"
        if chapter not in chapter_results:
            chapter_results[chapter] = {}
        if topic not in chapter_results[chapter]:
            chapter_results[chapter][topic] = {"total": 0, "correct": 0}
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

    await db.commit()

    total = len(details)
    score_pct = (correct_count / total * 100) if total > 0 else 0.0

    # Build chapter_results flat list for Gemini
    chapter_results_flat = [
        {"chapter": ch, "topic": topic, "total": v["total"], "correct": v["correct"]}
        for ch, topics in chapter_results.items()
        for topic, v in topics.items()
    ]

    # Generate Gemini feedback (graceful degradation)
    feedback = None
    try:
        api_key = _load_gemini_api_key()
        service = ExamFeedbackService(api_key)
        feedback = service.get_feedback(total, correct_count, score_pct, chapter_results_flat)
    except Exception as exc:
        import logging
        logging.warning("ExamFeedbackService failed: %s", exc)

    if feedback is None:
        feedback = _build_local_exam_feedback(total, correct_count, score_pct, chapter_results_flat)

    return ExamSubmissionResponseWithFeedback(
        total=total,
        correct=correct_count,
        score_percentage=score_pct,
        details=details,
        feedback=feedback,
    )


def _load_gemini_api_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "GEMINI_API_KEY":
            return v.strip().strip("\"'")
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

    recommendations = [
        "Start with the wrong-answer review and write the reason for the correct option in one sentence.",
        "Spend 15 minutes on the weakest topic before attempting fresh questions.",
        "Reattempt this exam's incorrect questions after a short break without looking at the answers.",
        "Use related practice questions to separate similar concepts that caused mistakes.",
        "Keep strong topics in rotation with quick spaced-repetition reviews.",
    ]

    return ExamFeedback(
        overall_summary=summary,
        weak_topics=weak_topics[:5],
        strong_topics=strong_topics[:5],
        personalized_study_recommendations=recommendations,
    )
