"""Spaced repetition scheduler using SM-2 algorithm."""
import argparse
import sqlite3
import datetime
from shokti.core.config import DB_PATH, MCQ


def update_review_date(conn, student_id: str, mcq_id: int, is_correct: bool, quality: int):
    """Update next_review_at in student_mcq_stats.
    quality: 0-5 (0=complete blackout, 5=perfect response)
    Uses simplified SM-2:
    - If quality < 3: reset interval to 1 day
    - If quality >= 3: interval = interval * EF (Easiness Factor)
    - EF starts at 2.5, min 1.3
    """
    cursor = conn.cursor()

    # Get current stats for this MCQ
    cursor.execute("""
        SELECT easiness_factor, interval_days
        FROM student_mcq_stats
        WHERE student_id = ? AND mcq_id = ?
    """, (student_id, mcq_id))
    row = cursor.fetchone()

    if row is None:
        # Initialize new record
        ef = MCQ.SM2_INITIAL_EF
        interval = 1
    else:
        ef, interval = row

    # Ensure ef is a float
    ef = float(ef) if ef is not None else MCQ.SM2_INITIAL_EF
    interval = int(interval) if interval is not None else 1

    # SM-2 update rules
    if quality < 3:
        # Failed: reset interval to 1 day, reduce EF slightly
        interval = 1
        ef = max(1.3, ef - 0.2)
    else:
        # Passed: increase interval by EF
        if interval == 0:
            interval = 1
        interval = max(1, int(interval * ef))
        # Adjust EF based on quality
        ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        ef = max(1.3, ef)

    # Calculate next review date
    next_review = datetime.datetime.now() + datetime.timedelta(days=interval)

    # Upsert student_mcq_stats
    cursor.execute("""
        INSERT INTO student_mcq_stats
          (student_id, mcq_id, correct_count, wrong_count, easiness_factor,
           interval_days, next_review_at, last_reviewed_at, last_seen_at)
        VALUES (?, ?, 0, 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(student_id, mcq_id) DO UPDATE SET
            easiness_factor = excluded.easiness_factor,
            interval_days = excluded.interval_days,
            next_review_at = excluded.next_review_at,
            last_reviewed_at = excluded.last_reviewed_at
    """, (student_id, mcq_id, ef, interval, next_review.isoformat(), datetime.datetime.now().isoformat()))

    conn.commit()


def get_due_mcqs(conn, student_id: str, limit: int = 30) -> list:
    """Return MCQs where next_review_at <= now, ordered by most overdue."""
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()
    cursor.execute("""
        SELECT
            sms.mcq_id,
            q.question,
            q.topic_name,
            q.correct_answer,
            q.options,
            sms.next_review_at,
            sms.easiness_factor,
            sms.interval_days
        FROM student_mcq_stats sms
        JOIN question_bank q ON sms.mcq_id = q.id
        WHERE sms.student_id = ?
          AND sms.next_review_at IS NOT NULL
          AND sms.next_review_at <= ?
        ORDER BY sms.next_review_at ASC
        LIMIT ?
    """, (student_id, now, limit))
    rows = cursor.fetchall()
    return [
        {
            "mcq_id": r[0],
            "question_text": r[1],
            "topic": r[2],
            "correct_option": r[3],
            "options": r[4],
            "next_review_at": r[5],
            "easiness_factor": r[6],
            "interval_days": r[7],
        }
        for r in rows
    ]


def print_due_mcqs(student_id: str, limit: int = 30):
    """CLI: show due MCQs for review."""
    conn = sqlite3.connect(DB_PATH)
    due = get_due_mcqs(conn, student_id, limit)
    conn.close()

    if not due:
        print(f"\nNo MCQs due for review for student {student_id}.\n")
        return

    import json
    print(f"\n{'='*60}")
    print(f"  DUE MCQs FOR REVIEW ({len(due)} total)")
    print(f"{'='*60}")
    for i, mcq in enumerate(due, 1):
        opts = json.loads(mcq['options']) if isinstance(mcq['options'], str) else mcq['options']
        ca = json.loads(mcq['correct_option']) if isinstance(mcq['correct_option'], str) else mcq['correct_option']
        correct_letter = ca.get('option', '?') if isinstance(ca, dict) else ca
        print(f"\n  [{i}] Topic: {mcq['topic']}")
        print(f"      Q: {mcq['question_text'][:80]}...")
        print(f"      A: {opts.get('A', 'N/A')}")
        print(f"      B: {opts.get('B', 'N/A')}")
        print(f"      C: {opts.get('C', 'N/A')}")
        print(f"      D: {opts.get('D', 'N/A')}")
        print(f"      Correct: {correct_letter}")
        print(f"      EF={mcq['easiness_factor']:.2f}  Interval={mcq['interval_days']}d  Due={mcq['next_review_at']}")
    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Spaced repetition scheduler (SM-2)")
    parser.add_argument("student_id", nargs="?", default="STUDENT_001",
                        help="Student ID (default: STUDENT_001)")
    parser.add_argument("--limit", type=int, default=30,
                        help="Max MCQs to return (default: 30)")
    parser.add_argument("--mcq-id", type=int, dest="mcq_id",
                        help="MCQ ID to update review for")
    parser.add_argument("--quality", type=int, default=4,
                        help="Response quality 0-5 (default: 4)")
    parser.add_argument("--correct", action="store_true",
                        help="Mark last answer as correct (equivalent to quality=4)")
    parser.add_argument("--wrong", action="store_true",
                        help="Mark last answer as wrong (equivalent to quality=1)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    if args.mcq_id is not None:
        quality = 4 if args.correct else (1 if args.wrong else args.quality)
        is_correct = quality >= 3
        update_review_date(conn, args.student_id, args.mcq_id, is_correct, quality)
        print(f"Updated MCQ {args.mcq_id} for student {args.student_id}: "
              f"quality={quality}, correct={is_correct}")
    else:
        print_due_mcqs(args.student_id, args.limit)

    conn.close()


if __name__ == "__main__":
    main()