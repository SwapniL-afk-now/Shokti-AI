"""Weak topic tracker for student performance."""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from shokti.core.config import DB_PATH, MCQ


@dataclass
class TopicStats:
    topic_key: tuple[str, str]  # (chapter_id, topic_id) — globally unique
    topic_id: str
    topic_name: str
    chapter_name: str
    attempts: int
    correct: int
    accuracy: float
    wrong_count: int
    confident_mistake_count: int = 0
    no_knowledge_count: int = 0
    last_seen_at: Optional[str] = None


def get_topic_stats(conn: sqlite3.Connection, student_id: str) -> list[TopicStats]:
    """Compute per-topic stats for a student. Uses (chapter_id, topic_id) as unique key."""
    rows = conn.execute("""
        SELECT
            qb.chapter_id, qb.topic_id, qb.topic_name, qb.chapter_name,
            COUNT(*) as attempts,
            SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) as correct,
            SUM(CASE WHEN sal.is_correct = 0 THEN 1 ELSE 0 END) as wrong_count,
            SUM(CASE WHEN sal.confidence_rating = 3 THEN 1 ELSE 0 END) as confident_mistake_count,
            SUM(CASE WHEN sal.confidence_rating = 4 THEN 1 ELSE 0 END) as no_knowledge_count,
            MAX(sal.answered_at) as last_seen_at
        FROM student_answer_log sal
        JOIN question_bank qb ON sal.mcq_id = qb.id
        WHERE sal.student_id = ?
        GROUP BY qb.chapter_id, qb.topic_id
    """, (student_id,)).fetchall()

    return [
        TopicStats(
            topic_key=(row['chapter_id'], row['topic_id']),
            topic_id=row['topic_id'],
            topic_name=row['topic_name'],
            chapter_name=row['chapter_name'],
            attempts=row['attempts'],
            correct=row['correct'],
            accuracy=row['correct'] / row['attempts'] if row['attempts'] > 0 else 0.0,
            wrong_count=row['wrong_count'],
            confident_mistake_count=row['confident_mistake_count'] or 0,
            no_knowledge_count=row['no_knowledge_count'] or 0,
            last_seen_at=row['last_seen_at'],
        )
        for row in rows
    ]


def get_weak_topics(conn: sqlite3.Connection, student_id: str) -> list[TopicStats]:
    """Return topics where accuracy < WEAK_THRESHOLD."""
    all_stats = get_topic_stats(conn, student_id)
    return [t for t in all_stats if t.accuracy < MCQ.WEAK_THRESHOLD]


def get_strong_topics(conn: sqlite3.Connection, student_id: str) -> list[TopicStats]:
    """Return topics where accuracy >= WEAK_THRESHOLD."""
    all_stats = get_topic_stats(conn, student_id)
    return [t for t in all_stats if t.accuracy >= MCQ.WEAK_THRESHOLD]


def get_unseen_topics(conn: sqlite3.Connection, student_id: str) -> list[tuple[str, str]]:
    """Return (chapter_id, topic_id) pairs the student has never answered."""
    rows = conn.execute("""
        SELECT DISTINCT qb.chapter_id, qb.topic_id
        FROM question_bank qb
        LEFT JOIN student_answer_log sal
            ON sal.mcq_id = qb.id AND sal.student_id = ?
        WHERE sal.id IS NULL
    """, (student_id,)).fetchall()
    return [(r['chapter_id'], r['topic_id']) for r in rows]


def get_persistent_weak_topics(conn: sqlite3.Connection, student_id: str) -> list[TopicStats]:
    """Return topics with <60% accuracy across 3+ sessions."""
    rows = conn.execute("""
        WITH session_accuracy AS (
            SELECT
                qb.chapter_id, qb.topic_id,
                date(sal.answered_at) as session_date,
                SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) * 1.0 /
                    COUNT(*) as accuracy
            FROM student_answer_log sal
            JOIN question_bank qb ON sal.mcq_id = qb.id
            WHERE sal.student_id = ?
            GROUP BY qb.chapter_id, qb.topic_id, date(sal.answered_at)
        )
        SELECT
            qb.chapter_id, qb.topic_id, qb.topic_name, qb.chapter_name,
            COUNT(*) as sessions,
            AVG(sa.accuracy) as avg_accuracy,
            SUM(CASE WHEN sa.accuracy < 0.6 THEN 1 ELSE 0 END) as weak_sessions
        FROM session_accuracy sa
        JOIN question_bank qb ON qb.chapter_id = sa.chapter_id AND qb.topic_id = sa.topic_id
        GROUP BY sa.chapter_id, sa.topic_id
        HAVING avg_accuracy < 0.6 AND COUNT(*) >= 3
    """, (student_id,)).fetchall()

    return [
        TopicStats(
            topic_key=(row['chapter_id'], row['topic_id']),
            topic_id=row['topic_id'],
            topic_name=row['topic_name'],
            chapter_name=row['chapter_name'],
            attempts=row['sessions'],
            correct=0,
            accuracy=row['avg_accuracy'],
            wrong_count=0,
            confident_mistake_count=0,
            no_knowledge_count=0,
            last_seen_at=None,
        )
        for row in rows
    ]


def needs_generation(conn: sqlite3.Connection, topic_id: str) -> bool:
    """Check if topic needs new MCQ generation (fewer than GAP_THRESHOLD)."""
    count = conn.execute(
        "SELECT COUNT(*) as c FROM question_bank WHERE topic_id = ?",
        (topic_id,),
    ).fetchone()['c']
    return count < MCQ.GAP_THRESHOLD
