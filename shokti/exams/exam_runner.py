"""Fixed exam session runner."""
import uuid
import time
import sqlite3
import json
import datetime
from shokti.core.config import DB_PATH, MCQ
from shokti.exams.exam_config import load_exam, get_student_exam_status, has_completed_all_exams


def _upsert_stats(conn, student_id, mcq_id, is_correct):
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


def run_exam(exam_id: str, student_id: str):
    """Run a fixed exam for a student.
    1. Load exam JSON
    2. Fetch MCQ rows from DB by mcq_ids
    3. Serve MCQs one at a time
    4. Record to student_answer_log with session_type=f'exam{exam_id}' and session_id=uuid4()
    5. Increment appearance_counter for each MCQ
    6. Write topic_sampling_log row
    7. Print score + time at end
    """
    exam = load_exam(exam_id)
    mcq_ids = exam["mcq_ids"]
    total_mcqs = len(mcq_ids)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Fetch MCQ rows from DB
    placeholders = ",".join(["?" for _ in mcq_ids])
    rows = conn.execute(
        f"SELECT id, topic_name, question, options, correct_answer, difficulty FROM question_bank WHERE id IN ({placeholders})",
        mcq_ids,
    ).fetchall()
    mcqs = []
    for r in rows:
        mcqs.append({
            "id": r[0],
            "topic_name": r[1],
            "question": r[2],
            "options": json.loads(r[3]) if isinstance(r[3], str) else r[3],
            "correct_answer": json.loads(r[4]) if isinstance(r[4], str) else r[4],
            "difficulty": r[5],
        })

    # Sort by difficulty: easy first
    difficulty_order = {"easy": 0, "medium": 1, "hard": 2}
    mcqs.sort(key=lambda x: difficulty_order.get(x["difficulty"], 1))

    session_id = str(uuid.uuid4())
    start_time = time.time()
    answers = []

    print(f"\n{'='*60}")
    print(f"  {exam['title']}")
    print(f"{'='*60}")
    print(f"  Total MCQs: {total_mcqs}")
    print(f"  Duration: {exam['duration_minutes']} minutes")
    print(f"{'='*60}\n")

    for i, mcq in enumerate(mcqs):
        print(f"\n--- Question {i+1}/{total_mcqs} ---")
        print(f"[{mcq['difficulty'].upper()}] {mcq['question']}")
        options = mcq["options"]
        for opt_key, opt_val in options.items():
            print(f"  {opt_key}) {opt_val}")

        while True:
            question_start = time.time()
            answer = input("\nYour answer (A/B/C/D): ").strip().upper()
            if answer in ["A", "B", "C", "D"]:
                break
            print("Invalid answer. Please enter A, B, C, or D.")

        correct_opt = mcq["correct_answer"]["option"]
        is_correct = answer == correct_opt
        time_spent = time.time() - question_start

        # Record to student_answer_log
        conn.execute(
            """
            INSERT INTO student_answer_log
            (student_id, mcq_id, is_correct, confidence_rating, answered_at, time_spent_seconds, session_type, session_id, selected_option)
            VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)
            """,
            (student_id, mcq["id"], 1 if is_correct else 0, None, time_spent, f"exam{exam_id}", session_id, answer),
        )

        # Update SM-2 stats
        _upsert_stats(conn, student_id, mcq["id"], is_correct)

        # Increment appearance_counter
        conn.execute(
            "UPDATE question_bank SET appearance_counter = appearance_counter + 1 WHERE id = ?",
            (mcq["id"],),
        )

        answers.append({
            "mcq_id": mcq["id"],
            "topic_name": mcq["topic_name"],
            "user_answer": answer,
            "correct_answer": correct_opt,
            "is_correct": is_correct,
        })

    conn.commit()
    end_time = time.time()
    elapsed = end_time - start_time

    # Print score
    correct_count = sum(1 for a in answers if a["is_correct"])
    score_pct = (correct_count / total_mcqs) * 100
    print(f"\n{'='*60}")
    print(f"  EXAM COMPLETED")
    print(f"{'='*60}")
    print(f"  Score: {correct_count}/{total_mcqs} ({score_pct:.1f}%)")
    print(f"  Time taken: {elapsed:.1f} seconds")
    print(f"{'='*60}")

    # Per-topic breakdown
    print("\n  Per-Topic Breakdown:")
    topic_stats = {}
    for a in answers:
        topic = a["topic_name"]
        if topic not in topic_stats:
            topic_stats[topic] = {"correct": 0, "total": 0}
        topic_stats[topic]["total"] += 1
        if a["is_correct"]:
            topic_stats[topic]["correct"] += 1

    for topic, stats in topic_stats.items():
        pct = (stats["correct"] / stats["total"]) * 100
        print(f"    {topic}: {stats['correct']}/{stats['total']} ({pct:.1f}%)")

    print(f"\n{'='*60}\n")

    # Pre-fetch all topic metadata in one query — avoids N+1
    topic_names = list(topic_stats.keys())
    if topic_names:
        placeholders = ",".join(["?"] * len(topic_names))
        all_topics = {r['topic_name']: (r['topic_id'], r['chapter_id'])
                      for r in conn.execute(
            f"SELECT topic_id, topic_name, chapter_id FROM question_bank WHERE topic_name IN ({placeholders})",
            topic_names
        ).fetchall()}
    else:
        all_topics = {}

    for topic, stats in topic_stats.items():
        actual_topic_id, actual_chapter_id = all_topics.get(topic, (topic, exam["chapter_ids"][0]))
        conn.execute(
            """
            INSERT INTO topic_sampling_log
            (session_id, student_id, session_type, chapter_id, topic_id, times_sampled, session_date)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (session_id, student_id, f"exam{exam_id}", actual_chapter_id, actual_topic_id, stats["total"]),
        )
    conn.commit()
    conn.close()

    return {
        "session_id": session_id,
        "score": correct_count,
        "total": total_mcqs,
        "elapsed_seconds": elapsed,
        "topic_breakdown": topic_stats,
    }