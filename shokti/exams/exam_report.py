"""Exam report generator."""
import sqlite3
import json
from datetime import datetime
from shokti.core.config import DB_PATH


def print_exam_report(exam_id: str, student_id: str):
    """After each exam, print:
    - Score: X/30 (Y%)
    - Time taken
    - Per-topic breakdown
    - Rank among all students who took this exam
    """
    conn = sqlite3.connect(DB_PATH)
    session_type = f"exam{exam_id}"

    # Get all sessions for this exam
    sessions = conn.execute(
        """
        SELECT session_id, answered_at, time_spent_seconds
        FROM student_answer_log
        WHERE student_id = ? AND session_type = ?
        GROUP BY session_id
        """,
        (student_id, session_type),
    ).fetchall()

    if not sessions:
        print(f"\nNo exam record found for student {student_id}, exam {exam_id}\n")
        conn.close()
        return

    # Use the most recent session
    latest_session = sessions[-1]
    session_id = latest_session[0]
    time_taken = latest_session[2] or 0.0

    # Get all answers for this session
    answers = conn.execute(
        """
        SELECT mcq_id, is_correct
        FROM student_answer_log
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchall()

    total_mcqs = len(answers)
    correct_count = sum(1 for a in answers if a[1])
    score_pct = (correct_count / total_mcqs) * 100 if total_mcqs > 0 else 0

    print(f"\n{'='*60}")
    print(f"  EXAM REPORT")
    print(f"{'='*60}")
    print(f"  Exam ID: {exam_id}")
    print(f"  Student ID: {student_id}")
    print(f"  Score: {correct_count}/{total_mcqs} ({score_pct:.1f}%)")
    print(f"  Time taken: {time_taken:.1f} seconds")
    print(f"{'='*60}")

    # Per-topic breakdown
    print("\n  Per-Topic Breakdown:")

    # Get mcq_ids for topic mapping
    mcq_ids = [a[0] for a in answers]
    placeholders = ",".join(["?" for _ in mcq_ids])
    mcq_topics = conn.execute(
        f"SELECT id, topic_name FROM question_bank WHERE id IN ({placeholders})",
        mcq_ids,
    ).fetchall()
    topic_map = {r[0]: r[1] for r in mcq_topics}

    topic_stats = {}
    for mcq_id, is_correct in answers:
        topic = topic_map.get(mcq_id, "Unknown")
        if topic not in topic_stats:
            topic_stats[topic] = {"correct": 0, "total": 0}
        topic_stats[topic]["total"] += 1
        if is_correct:
            topic_stats[topic]["correct"] += 1

    for topic, stats in topic_stats.items():
        pct = (stats["correct"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        print(f"    {topic}: {stats['correct']}/{stats['total']} ({pct:.1f}%)")

    # Rank calculation
    all_student_scores = conn.execute(
        """
        SELECT student_id, session_id
        FROM student_answer_log
        WHERE session_type = ?
        GROUP BY student_id, session_id
        """,
        (session_type,),
    ).fetchall()

    # Calculate score for each student
    rank_data = []
    for sid, sess_id in all_student_scores:
        sess_answers = conn.execute(
            "SELECT is_correct FROM student_answer_log WHERE session_id = ?",
            (sess_id,),
        ).fetchall()
        sess_correct = sum(1 for a in sess_answers if a[0])
        sess_total = len(sess_answers)
        sess_pct = (sess_correct / sess_total) * 100 if sess_total > 0 else 0
        rank_data.append((sid, sess_correct, sess_total, sess_pct))

    rank_data.sort(key=lambda x: x[3], reverse=True)

    # Find rank of current student
    student_rank = None
    for idx, (sid, *_ ) in enumerate(rank_data):
        if sid == student_id:
            student_rank = idx + 1
            break

    if student_rank is not None:
        total_students = len(rank_data)
        print(f"\n  Rank: {student_rank}/{total_students}")
        print(f"  Percentile: {((total_students - student_rank) / total_students) * 100:.1f}%")

    print(f"\n{'='*60}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Exam report generator")
    parser.add_argument("--exam-id", required=True)
    parser.add_argument("--student-id", default="STUDENT_001")
    args = parser.parse_args()
    print_exam_report(args.exam_id, args.student_id)


if __name__ == "__main__":
    main()