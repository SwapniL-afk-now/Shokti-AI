"""Student stats router: overall, per-topic, weak topics, coverage."""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from shokti.api.deps import get_db_dep, get_current_student_dep
from shokti.api.models import Student
from shokti.api.schemas import (
    StudentStatsResponse,
    TopicHistoryEntry,
    TopicHistoryResponse,
    WeakTopicEntry,
    ConfidenceProfileResponse,
)

router = APIRouter(prefix="/api/student", tags=["student"])


@router.get("/stats", response_model=StudentStatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    result = await db.execute(
        text("""
            SELECT COUNT(*) as total,
                   CAST(SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS INTEGER) as correct,
                   CAST(SUM(CASE WHEN time_spent_seconds IS NOT NULL THEN time_spent_seconds ELSE 0 END) AS INTEGER) as total_time
            FROM student_answer_log
            WHERE student_id = :sid
        """),
        {"sid": student.id},
    )
    row = result.fetchone()
    total = row.total if row and row.total else 0
    correct = row.correct if row and row.correct else 0
    total_time = row.total_time if row and row.total_time else 0
    avg_time = (total_time / total) if total > 0 else 0.0

    # Number of distinct exams completed
    exam_count_res = await db.execute(
        text("""
            SELECT COUNT(DISTINCT session_type)
            FROM student_answer_log
            WHERE student_id = :sid AND session_type LIKE 'exam%'
        """),
        {"sid": student.id},
    )
    exams_taken = exam_count_res.scalar() or 0

    # Strongest & Weakest topics
    topic_accs_res = await db.execute(
        text("""
            SELECT qb.topic_name,
                   COUNT(*) as attempts,
                   CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS INTEGER) as correct
            FROM student_answer_log sal
            JOIN question_bank qb ON sal.mcq_id = qb.id
            WHERE sal.student_id = :sid
            GROUP BY qb.topic_name
        """),
        {"sid": student.id},
    )
    topic_rows = topic_accs_res.fetchall()
    
    strongest_topic = None
    weakest_topic = None
    if topic_rows:
        topic_accs = [
            (r.topic_name, (r.correct / r.attempts))
            for r in topic_rows if r.attempts > 0
        ]
        if topic_accs:
            topic_accs_sorted = sorted(topic_accs, key=lambda x: x[1])
            weakest_topic = topic_accs_sorted[0][0]
            strongest_topic = topic_accs_sorted[-1][0]

    # Current streak: consecutive calendar days with at least one correct answer,
    # counting back from today
    streak_result = await db.execute(
        text("""
            SELECT DATE(answered_at, 'utc') as day,
                   CAST(SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS INTEGER) as day_correct
            FROM student_answer_log
            WHERE student_id = :sid
            GROUP BY DATE(answered_at, 'utc')
            ORDER BY day DESC
            LIMIT 90
        """),
        {"sid": student.id},
    )
    days = streak_result.fetchall()
    streak = 0
    today = datetime.now(timezone.utc).date()
    for i, d in enumerate(days):
        expected_day = (today - timedelta(days=i)).isoformat()
        if d.day != expected_day:
            break
        if d.day_correct and d.day_correct > 0:
            streak += 1
        else:
            break

    return StudentStatsResponse(
        total_answered=total,
        correct_count=correct,
        accuracy=(correct / total * 100) if total > 0 else 0.0,
        current_streak=streak,
        total_time_seconds=total_time,
        avg_time_seconds=avg_time,
        exams_taken=exams_taken,
        strongest_topic=strongest_topic,
        weakest_topic=weakest_topic,
    )


@router.get("/topics/{topic_id}/history", response_model=TopicHistoryResponse)
async def get_topic_history(
    topic_id: str,
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    result = await db.execute(
        text("""
            SELECT sal.answered_at, sal.is_correct, qb.difficulty, qb.topic_name
            FROM student_answer_log sal
            JOIN question_bank qb ON qb.id = sal.mcq_id
            WHERE sal.student_id = :sid AND qb.topic_id = :tid
            ORDER BY sal.answered_at DESC
            LIMIT 50
        """),
        {"sid": student.id, "tid": topic_id},
    )
    rows = result.fetchall()
    topic_name = ""
    entries = []
    for r in rows:
        if not topic_name:
            topic_name = r.topic_name or ""
        entries.append(TopicHistoryEntry(
            answered_at=r.answered_at,
            is_correct=bool(r.is_correct),
            difficulty=r.difficulty,
        ))

    return TopicHistoryResponse(topic_id=topic_id, topic_name=topic_name, entries=entries)


@router.get("/weak-topics", response_model=list[WeakTopicEntry])
async def get_weak_topics(
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    result = await db.execute(
        text("""
            SELECT
                qb.topic_id,
                qb.topic_name,
                qb.chapter_id,
                qb.chapter_name,
                COUNT(*) as attempt_count,
                CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS INTEGER) as correct_count
            FROM student_answer_log sal
            JOIN question_bank qb ON qb.id = sal.mcq_id
            WHERE sal.student_id = :sid
            GROUP BY qb.topic_id, qb.topic_name, qb.chapter_id, qb.chapter_name
            HAVING attempt_count >= 3 AND
                   (CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*)) < 0.5
            ORDER BY
                   (CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*)) ASC,
                   attempt_count DESC
            LIMIT 100
        """),
        {"sid": student.id},
    )
    rows = result.fetchall()
    return [
        WeakTopicEntry(
            topic_id=r.topic_id or "",
            topic_name=r.topic_name or "",
            chapter_id=r.chapter_id or "",
            chapter_name=r.chapter_name or "",
            accuracy=(r.correct_count / r.attempt_count * 100) if r.attempt_count > 0 else 0.0,
            attempt_count=r.attempt_count,
        )
        for r in rows
    ]


@router.get("/coverage", response_model=list[WeakTopicEntry])
async def get_coverage(
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    result = await db.execute(
        text("""
            SELECT DISTINCT topic_id, topic_name, chapter_id, chapter_name
            FROM question_bank qb
            WHERE NOT EXISTS (
                SELECT 1 FROM student_answer_log sal
                WHERE sal.mcq_id = qb.id AND sal.student_id = :sid
            )
            ORDER BY chapter_id, topic_name
            LIMIT 50
        """),
        {"sid": student.id},
    )
    rows = result.fetchall()
    return [
        WeakTopicEntry(
            topic_id=r.topic_id or "",
            topic_name=r.topic_name or "",
            chapter_id=r.chapter_id or "",
            chapter_name=r.chapter_name or "",
            accuracy=0.0,
            attempt_count=0,
        )
        for r in rows
    ]


@router.get("/topic-stats")
async def get_topic_stats(
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    result = await db.execute(
        text("""
            SELECT
                qb.topic_id,
                qb.topic_name,
                COUNT(*) as attempt_count,
                CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS INTEGER) as correct_count
            FROM student_answer_log sal
            JOIN question_bank qb ON qb.id = sal.mcq_id
            WHERE sal.student_id = :sid
            GROUP BY qb.topic_id, qb.topic_name
        """),
        {"sid": student.id},
    )
    rows = result.fetchall()
    return [
        {
            "topic_id": r.topic_id or "",
            "topic_name": r.topic_name or "",
            "attempt_count": r.attempt_count,
            "correct_count": r.correct_count,
            "accuracy": (r.correct_count / r.attempt_count * 100) if r.attempt_count > 0 else 0.0,
        }
        for r in rows
    ]


@router.get("/confidence-profile", response_model=ConfidenceProfileResponse)
async def get_confidence_profile(
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    result = await db.execute(
        text("""
            SELECT is_correct, time_spent_seconds
            FROM student_answer_log
            WHERE student_id = :sid AND time_spent_seconds IS NOT NULL
        """),
        {"sid": student.id},
    )
    rows = result.fetchall()

    if not rows:
        return ConfidenceProfileResponse(
            lucky_guess=0,
            confident_master=0,
            confident_mistake=0,
            no_knowledge=0
        )

    times = [r.time_spent_seconds for r in rows]
    median_time = sorted(times)[len(times) // 2]

    lucky_guess = sum(1 for r in rows if r.is_correct and r.time_spent_seconds <= median_time)
    confident_master = sum(1 for r in rows if r.is_correct and r.time_spent_seconds > median_time)
    confident_mistake = sum(1 for r in rows if not r.is_correct and r.time_spent_seconds <= median_time)
    no_knowledge = sum(1 for r in rows if not r.is_correct and r.time_spent_seconds > median_time)

    return ConfidenceProfileResponse(
        lucky_guess=lucky_guess,
        confident_master=confident_master,
        confident_mistake=confident_mistake,
        no_knowledge=no_knowledge
    )


@router.get("/confusion-clusters")
async def get_confusion_clusters(
    min_wrong: int = 2,
    student: Student = Depends(get_current_student_dep),
):
    """Retrieve semantically similar MCQ pairs that the student confuses in weak topics."""
    from shokti.confusion_map import detect_confusion_clusters
    return detect_confusion_clusters(student.id, min_wrong=min_wrong)


@router.get("/timeline")
async def get_student_timeline(
    db: AsyncSession = Depends(get_db_dep),
    student: Student = Depends(get_current_student_dep),
):
    """Retrieve daily accuracy logs for the last 30 calendar days to plot actual progress."""
    result = await db.execute(
        text("""
            SELECT DATE(answered_at) as date,
                   COUNT(*) as total,
                   CAST(SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS INTEGER) as correct
            FROM student_answer_log
            WHERE student_id = :sid
            GROUP BY DATE(answered_at)
            ORDER BY DATE(answered_at) ASC
            LIMIT 30
        """),
        {"sid": student.id}
    )
    rows = result.fetchall()
    return [
        {
            "date": r.date,
            "total": r.total,
            "correct": r.correct,
            "accuracy": (r.correct / r.total * 100) if r.total > 0 else 0.0
        }
        for r in rows
    ]