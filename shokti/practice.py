"""
Practice router — detects student state and routes to the right mode.

Modes:
  diagnostic — old-style random baseline (preserve existing logic)
  exam        — fixed exam session (from exams/exam_runner)
  adaptive    — weighted question selection (from adaptive_practice)
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running as: python practice.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shokti.core.config import DB_PATH, MCQ
from shokti.exams.exam_config import has_completed_all_exams, get_next_incomplete_exam
from shokti.exams.exam_runner import run_exam
from shokti.adaptive_practice import run_adaptive_session


# ---------------------------------------------------------------------------
# Existing diagnostic baseline logic (kept intact)
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_diagnostic_mcqs(conn, count=30):
    """Pull random MCQs from across all topics. No tag-based sampling."""
    rows = conn.execute("""
        SELECT id, chapter_id, chapter_name, topic_id, topic_name,
               question, options, correct_answer
        FROM question_bank
        ORDER BY RANDOM()
        LIMIT ?
    """, (count,)).fetchall()
    return [dict(r) for r in rows]


def display_mcq(mcq, number, total, blind=False):
    print(f"\n{'='*50}")
    print(f"Question {number}/{total}")
    if not blind:
        print(f"Topic: {mcq['topic_name']} ({mcq['chapter_name']})")
    print(f"{'='*50}")
    print(mcq['question'])
    print()

    import json
    options = json.loads(mcq['options'])
    for key in ['A', 'B', 'C', 'D']:
        print(f"  {key}. {options.get(key, 'N/A')}")


def record_answer(conn, student_id, mcq_id, is_correct, time_spent,
                  selected_option="", session_id="", session_type="diagnostic"):
    conn.execute("""
        INSERT INTO student_answer_log
        (student_id, mcq_id, is_correct, time_spent_seconds, session_type,
         session_id, selected_option)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (student_id, mcq_id, is_correct, time_spent, session_type,
          session_id, selected_option))
    conn.commit()


def upsert_stats(conn, student_id, mcq_id, is_correct):
    import datetime
    now = datetime.datetime.now().isoformat()
    if is_correct:
        conn.execute("""
            INSERT INTO student_mcq_stats
              (student_id, mcq_id, correct_count, wrong_count, last_seen_at,
               next_review_at, last_reviewed_at, easiness_factor, interval_days)
            VALUES (?, ?, 1, 0, CURRENT_TIMESTAMP, ?, ?, ?, 1)
            ON CONFLICT(student_id, mcq_id) DO UPDATE SET
                correct_count = correct_count + 1,
                last_seen_at = CURRENT_TIMESTAMP,
                next_review_at = CASE WHEN excluded.next_review_at IS NOT NULL THEN excluded.next_review_at ELSE next_review_at END,
                last_reviewed_at = CASE WHEN excluded.last_reviewed_at IS NOT NULL THEN excluded.last_reviewed_at ELSE last_reviewed_at END
        """, (student_id, mcq_id, now, now, MCQ.SM2_INITIAL_EF))
    else:
        conn.execute("""
            INSERT INTO student_mcq_stats
              (student_id, mcq_id, correct_count, wrong_count, last_seen_at,
               next_review_at, last_reviewed_at, easiness_factor, interval_days)
            VALUES (?, ?, 0, 1, CURRENT_TIMESTAMP, ?, ?, ?, 1)
            ON CONFLICT(student_id, mcq_id) DO UPDATE SET
                wrong_count = wrong_count + 1,
                last_seen_at = CURRENT_TIMESTAMP,
                next_review_at = CASE WHEN excluded.next_review_at IS NOT NULL THEN excluded.next_review_at ELSE next_review_at END,
                last_reviewed_at = CASE WHEN excluded.last_reviewed_at IS NOT NULL THEN excluded.last_reviewed_at ELSE last_reviewed_at END
        """, (student_id, mcq_id, now, now, MCQ.SM2_INITIAL_EF))
    conn.commit()


def show_answer(mcq, student_answer, correct_option):
    import json
    print(f"\nYour answer: {student_answer} | Correct: {correct_option}")
    is_correct = student_answer.upper() == correct_option.upper()
    if is_correct:
        print("  Correct!")
    else:
        print(f"  Wrong. The correct answer was {correct_option}.")

    correct_answer = json.loads(mcq['correct_answer'])
    print(f"\n  Explanation: {correct_answer.get('text', 'N/A')}")


def session_summary(conn, student_id, total_answered):
    """Print weak/strong profile after session ends."""
    print(f"\n{'='*50}")
    print(f"SESSION SUMMARY — {student_id}")
    print(f"{'='*50}")

    rows = conn.execute("""
        SELECT
            qb.topic_name, qb.chapter_name,
            COUNT(*) as total,
            SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log sal
        JOIN question_bank qb ON sal.mcq_id = qb.id
        WHERE sal.student_id = ?
        GROUP BY qb.topic_name
        ORDER BY (CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*)) ASC
    """, (student_id,)).fetchall()

    print(f"\nPer-topic accuracy:\n")
    for r in rows:
        pct = (r['correct'] / r['total']) * 100 if r['total'] > 0 else 0
        bar = '█' * int(pct // 10) + '░' * (10 - int(pct // 10))
        flag = " <- WEAK" if pct < 60 else " <- STRONG"
        print(f"  {r['topic_name']}: {bar} {pct:.0f}%{flag}")

    overall = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log
        WHERE student_id = ?
    """, (student_id,)).fetchone()

    if overall['total'] > 0:
        pct = (overall['correct'] / overall['total']) * 100
        print(f"\nOverall: {overall['correct']}/{overall['total']} = {pct:.1f}%")

    weak_topics = [r['topic_name'] for r in rows if (r['correct'] / r['total']) < 0.6]
    if weak_topics:
        print(f"\nWeak areas to focus: {', '.join(weak_topics)}")

    cal = conn.execute("""
        SELECT is_correct, time_spent_seconds
        FROM student_answer_log
        WHERE student_id = ?
    """, (student_id,)).fetchall()

    if cal:
        times = [r['time_spent_seconds'] for r in cal]
        median_time = sorted(times)[len(times) // 2]

        fast_correct = sum(1 for r in cal if r['is_correct'] and r['time_spent_seconds'] <= median_time)
        slow_correct = sum(1 for r in cal if r['is_correct'] and r['time_spent_seconds'] > median_time)
        fast_wrong = sum(1 for r in cal if not r['is_correct'] and r['time_spent_seconds'] <= median_time)
        slow_wrong = sum(1 for r in cal if not r['is_correct'] and r['time_spent_seconds'] > median_time)

        print(f"\n=== Confidence Profile (inferred from response time) ===")
        print(f"  Fast + Correct  -> Lucky guess:      {fast_correct}")
        print(f"  Slow + Correct  -> Confident:        {slow_correct}")
        print(f"  Fast + Wrong    -> Confident wrong:  {fast_wrong}")
        print(f"  Slow + Wrong    -> No knowledge:    {slow_wrong}")


def run_diagnostic(conn, student_id, count=30):
    """Phase A: 30-40 random MCQs, no Gemini, build student profile."""
    print(f"\n=== Diagnostic Baseline ===")
    print(f"Questions: {count} | Student: {student_id}")
    print(f"No Gemini calls — purely DB pull.\n")

    mcqs = get_diagnostic_mcqs(conn, count)
    if not mcqs:
        print("ERROR: No MCQs found in database.")
        return

    print(f"Loaded {len(mcqs)} questions. Starting...\n")
    print("Type the option letter (A/B/C/D) and press Enter.\n")

    import json
    answered = 0
    for i, mcq in enumerate(mcqs, 1):
        display_mcq(mcq, i, len(mcqs), blind=True)

        import time
        start = time.time()
        student_answer = input("Your answer (A/B/C/D): ").strip().upper()
        elapsed = int(time.time() - start)

        correct_answer = json.loads(mcq['correct_answer'])
        correct_option = correct_answer['option']

        show_answer(mcq, student_answer, correct_option)

        is_correct = student_answer.upper() == correct_option.upper()

        record_answer(conn, student_id, mcq['id'], is_correct, elapsed)
        upsert_stats(conn, student_id, mcq['id'], is_correct)
        answered += 1

    session_summary(conn, student_id, answered)


# ---------------------------------------------------------------------------
# Router main()
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Shokti Practice Router")
    parser.add_argument("--student-id", default="testrun")
    parser.add_argument(
        "--mode",
        choices=["diagnostic", "exam", "adaptive"],
        default=None,
    )
    parser.add_argument("--exam-id", type=str, default=None)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--topic", type=str, default=None, help="Filter session to this topic name")
    parser.add_argument("--chapter", type=str, default=None, help="Filter session to this chapter name")
    args = parser.parse_args()

    student_id = args.student_id
    mode = args.mode
    count = args.count

    # Auto-detect mode if not specified
    if mode is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        if has_completed_all_exams(conn, student_id):
            mode = "adaptive"
        else:
            mode = "exam"
        conn.close()

    print(f"\n{'='*50}")
    print(f"  Shokti Practice Router")
    print(f"  student_id={student_id} | mode={mode}")
    print(f"{'='*50}\n")

    if mode == "diagnostic":
        conn = get_db()
        run_diagnostic(conn, student_id, count=count)
        conn.close()

    elif mode == "exam":
        exam_id = args.exam_id
        if exam_id is None:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            exam_id = get_next_incomplete_exam(conn, student_id)
            conn.close()
        if exam_id is None:
            print("ERROR: No incomplete exams found. Use --mode adaptive instead.")
        else:
            run_exam(exam_id, student_id)

    elif mode == "adaptive":
        run_adaptive_session(student_id, count=count, mode="adaptive",
                             topic=args.topic, chapter=args.chapter)

    print("\nSession complete.")


if __name__ == "__main__":
    main()