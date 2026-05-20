"""Student performance dashboard."""
import argparse
import sqlite3
from shokti.core.config import DB_PATH


def print_dashboard(student_id: str):
    """Print full student profile:
    - Exams completed: X/3
    - Adaptive sessions: N
    - Baseline accuracy (exams)
    - Current accuracy (adaptive)
    - Improvement
    - Per-topic accuracy history
    - Weakest topic
    - Most improved
    - Next adaptive focus
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # --- Exams completed (out of 3 diagnostic) ---
    cursor.execute("""
        SELECT COUNT(DISTINCT session_id)
        FROM student_answer_log
        WHERE student_id = ? AND session_type LIKE 'exam%'
    """, (student_id,))
    exams_completed = cursor.fetchone()[0] or 0

    # --- Baseline accuracy from exams ---
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log
        WHERE student_id = ? AND session_type LIKE 'exam%'
    """, (student_id,))
    exam_row = cursor.fetchone()
    exam_total = exam_row[0] or 0
    exam_correct = exam_row[1] or 0
    baseline_accuracy = (exam_correct / exam_total * 100) if exam_total > 0 else 0.0

    # --- Adaptive sessions count ---
    cursor.execute("""
        SELECT COUNT(DISTINCT session_id)
        FROM topic_sampling_log
        WHERE student_id = ? AND session_type LIKE 'adaptive%'
    """, (student_id,))
    adaptive_sessions = cursor.fetchone()[0] or 0

    # --- Current accuracy from adaptive sessions ---
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log
        WHERE student_id = ? AND session_type LIKE 'adaptive%'
    """, (student_id,))
    adapt_row = cursor.fetchone()
    adapt_total = adapt_row[0] or 0
    adapt_correct = adapt_row[1] or 0
    current_accuracy = (adapt_correct / adapt_total * 100) if adapt_total > 0 else 0.0

    # --- Improvement ---
    improvement = current_accuracy - baseline_accuracy

    # --- Per-topic accuracy history ---
    cursor.execute("""
        SELECT
            q.topic_name,
            COUNT(*) as total,
            SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) as correct,
            ROUND(CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100, 1) as accuracy
        FROM student_answer_log sal
        JOIN question_bank q ON sal.mcq_id = q.id
        WHERE sal.student_id = ?
        GROUP BY q.topic_name
        ORDER BY accuracy ASC
    """, (student_id,))
    topic_rows = cursor.fetchall()

    # --- Weakest topic ---
    weakest = topic_rows[0] if topic_rows else (None, 0, 0, 0.0)

    # --- Most improved (compare first 50% of adaptive sessions vs last 50%) ---
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log
        WHERE student_id = ? AND session_type LIKE 'adaptive%'
        AND rowid <= (SELECT MAX(rowid) FROM student_answer_log WHERE student_id = ? AND session_type LIKE 'adaptive%') / 2
    """, (student_id, student_id))
    first_half = cursor.fetchone()
    first_total = first_half[0] or 0
    first_correct = first_half[1] or 0
    first_half_accuracy = (first_correct / first_total * 100) if first_total > 0 else 0.0

    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log
        WHERE student_id = ? AND session_type LIKE 'adaptive%'
        AND rowid > (SELECT MAX(rowid) FROM student_answer_log WHERE student_id = ? AND session_type LIKE 'adaptive%') / 2
    """, (student_id, student_id))
    second_half = cursor.fetchone()
    second_total = second_half[0] or 0
    second_correct = second_half[1] or 0
    second_half_accuracy = (second_correct / second_total * 100) if second_total > 0 else 0.0

    most_improved = second_half_accuracy - first_half_accuracy

    # --- Next adaptive focus (topics with lowest accuracy, weighted by recency) ---
    cursor.execute("""
        SELECT q.topic_name, COUNT(*) as attempts
        FROM student_answer_log sal
        JOIN question_bank q ON sal.mcq_id = q.id
        WHERE sal.student_id = ? AND sal.session_type LIKE 'adaptive%'
        GROUP BY q.topic_name
        ORDER BY attempts DESC
        LIMIT 5
    """, (student_id,))
    recent_topics = [row[0] for row in cursor.fetchall()]

    lowest_accuracy_topics = [r[0] for r in topic_rows[:3] if r[0] not in recent_topics]
    next_focus = lowest_accuracy_topics[0] if lowest_accuracy_topics else (topic_rows[0][0] if topic_rows else "N/A")

    conn.close()

    # --- Print dashboard ---
    print(f"\n{'='*55}")
    print(f"  STUDENT PERFORMANCE DASHBOARD")
    print(f"{'='*55}")
    print(f"  Student ID: {student_id}")
    print(f"{'='*55}")
    print(f"  Exams Completed:       {exams_completed}/3")
    print(f"  Adaptive Sessions:     {adaptive_sessions}")
    print(f"  Baseline Accuracy:    {baseline_accuracy:.1f}%  (exams)")
    print(f"  Current Accuracy:     {current_accuracy:.1f}%  (adaptive)")
    print(f"  Improvement:           {'+' if improvement >= 0 else ''}{improvement:.1f} pp")
    print(f"{'='*55}")
    print(f"  PER-TOPIC ACCURACY HISTORY")
    print(f"{'-'*55}")
    if topic_rows:
        print(f"  {'Topic':<35} {'Att':>4} {'Corr':>4} {'Acc %':>6}")
        print(f"  {'-'*35} {'-'*4} {'-'*4} {'-'*6}")
        for topic, total, correct, accuracy in topic_rows:
            topic_short = topic[:32] + "..." if len(topic) > 35 else topic
            print(f"  {topic_short:<35} {total:>4} {correct:>4} {accuracy:>6.1f}")
    else:
        print(f"  No data available.")
    print(f"{'='*55}")
    print(f"  WEAKEST TOPIC:     {weakest[0] or 'N/A'} ({weakest[3]:.1f}%)")
    print(f"  MOST IMPROVED:     {'+' if most_improved >= 0 else ''}{most_improved:.1f} pp (adaptive sessions)")
    print(f"  NEXT FOCUS:        {next_focus}")
    print(f"{'='*55}\n")


def main():
    parser = argparse.ArgumentParser(description="Student performance dashboard")
    parser.add_argument("student_id", nargs="?", default="STUDENT_001",
                        help="Student ID (default: STUDENT_001)")
    args = parser.parse_args()
    print_dashboard(args.student_id)


if __name__ == "__main__":
    main()