"""Exam trend analyzer — updates exam_trend table."""
import argparse
import sqlite3
import datetime
from shokti.core.config import DB_PATH


def update_exam_trends(conn):
    """Read all exam sessions (session_type LIKE 'exam%').
    Count topic appearance frequency = times_topic_appeared / total_exam_questions.
    Compare last 30 days vs previous 30 days for trend direction.
    Write to exam_trend table."""
    cursor = conn.cursor()
    now = datetime.datetime.now()
    last_30 = now - datetime.timedelta(days=30)
    prev_30 = last_30 - datetime.timedelta(days=30)

    # Get topics and their exam frequency in the last 30 days
    cursor.execute("""
        SELECT
            q.topic_name,
            COUNT(*) as recent_count
        FROM student_answer_log sal
        JOIN question_bank q ON sal.mcq_id = q.id
        WHERE sal.session_type LIKE 'exam%'
          AND sal.answered_at >= ?
        GROUP BY q.topic_name
    """, (last_30.isoformat(),))
    recent = {row[0]: row[1] for row in cursor.fetchall()}

    # Get topics and their exam frequency in the previous 30 days
    cursor.execute("""
        SELECT
            q.topic_name,
            COUNT(*) as prev_count
        FROM student_answer_log sal
        JOIN question_bank q ON sal.mcq_id = q.id
        WHERE sal.session_type LIKE 'exam%'
          AND sal.answered_at >= ?
          AND sal.answered_at < ?
        GROUP BY q.topic_name
    """, (prev_30.isoformat(), last_30.isoformat()))
    prev = {row[0]: row[1] for row in cursor.fetchall()}

    # Total exam questions in each period
    cursor.execute("""
        SELECT COUNT(*)
        FROM student_answer_log
        WHERE session_type LIKE 'exam%'
          AND answered_at >= ?
    """, (last_30.isoformat(),))
    total_recent = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT COUNT(*)
        FROM student_answer_log
        WHERE session_type LIKE 'exam%'
          AND answered_at >= ?
          AND answered_at < ?
    """, (prev_30.isoformat(), last_30.isoformat()))
    total_prev = cursor.fetchone()[0] or 0

    # Get all topics seen in any exam
    all_topics = set(recent.keys()) | set(prev.keys())

    updated = 0
    for topic_name in all_topics:
        r = recent.get(topic_name, 0)
        p = prev.get(topic_name, 0)
        recent_freq = r / total_recent if total_recent > 0 else 0.0

        # Trend direction
        if r > p:
            direction = "rising"
        elif r < p:
            direction = "declining"
        else:
            direction = "stable"

        # Get chapter_id and topic_id for this topic (from question_bank)
        cursor.execute("SELECT chapter_id, topic_id FROM question_bank WHERE topic_name = ? LIMIT 1", (topic_name,))
        row = cursor.fetchone()
        chapter_id = row[0] if row else ""
        actual_topic_id = row[1] if row else topic_name

        # Upsert: UPDATE if exists, INSERT if not
        existing = conn.execute("SELECT id FROM exam_trend WHERE topic_name = ?", (topic_name,)).fetchone()
        if existing:
            conn.execute("""
                UPDATE exam_trend SET
                    topic_id = ?,
                    appearance_frequency = ?,
                    trend_direction = ?,
                    last_analyzed = ?
                WHERE topic_name = ?
            """, (actual_topic_id, recent_freq, direction, now.isoformat(), topic_name))
        else:
            conn.execute("""
                INSERT INTO exam_trend (topic_id, topic_name, chapter_id, appearance_frequency, trend_direction, last_analyzed)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (actual_topic_id, topic_name, chapter_id, recent_freq, direction, now.isoformat()))
        updated += 1

    conn.commit()
    return updated


def print_topic_priority():
    """Print topic importance table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT topic_name, appearance_frequency, trend_direction
        FROM exam_trend
        ORDER BY appearance_frequency DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("\nNo exam trend data available. Run --update first.\n")
        return

    print(f"\n{'='*70}")
    print(f"  TOPIC EXAM PRIORITY TABLE")
    print(f"{'='*70}")
    print(f"  {'Topic':<35} {'Freq%':>7} {'Trend':>7}")
    print(f"  {'-'*35} {'-'*7} {'-'*7}")
    for topic_name, recent, direction in rows:
        trend_icon = {"rising": "+", "declining": "-", "stable": "~"}.get(direction, "?")
        topic_label = topic_name or "N/A"
        topic_short = topic_label[:32] + "..." if len(topic_label) > 35 else topic_label
        print(f"  {topic_short:<35} {recent*100:>7.2f}% {trend_icon:>7} ")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Exam trend analyzer")
    parser.add_argument("--update", action="store_true",
                        help="Update exam trends from answer log")
    parser.add_argument("--print", action="store_true", dest="do_print",
                        help="Print topic priority table")
    args = parser.parse_args()

    if args.update:
        conn = sqlite3.connect(DB_PATH)
        n = update_exam_trends(conn)
        conn.close()
        print(f"Updated {n} topic trends.")

    if args.do_print or not args.update:
        print_topic_priority()


if __name__ == "__main__":
    main()