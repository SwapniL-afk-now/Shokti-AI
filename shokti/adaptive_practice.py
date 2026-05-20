"""Adaptive practice runner — Stage 2."""
import uuid
import sqlite3
import json
import time

from shokti.core.config import DB_PATH, MCQ
from shokti.sampling_weights import get_combined_weights, log_session_sampling
from shokti.question_selectors.session_builder import build_session, get_topic_list
from shokti.question_selectors.weak_topic_tracker import get_topic_stats
from shokti.generators.gap_filler import setup_generator, fill_topic, generate_fresh_mcqs


# ---------------------------------------------------------------------------
# Helpers (mirrors practice.py)
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def display_mcq(mcq, number, total, blind=False):
    print(f"\n{'='*50}")
    print(f"Question {number}/{total}")
    if not blind:
        print(f"Topic: {mcq['topic_name']} ({mcq['chapter_name']})")
    print(f"{'='*50}")
    print(mcq['question'])
    print()

    options = json.loads(mcq['options']) if isinstance(mcq['options'], str) else mcq['options']
    for key in ['A', 'B', 'C', 'D']:
        print(f"  {key}. {options.get(key, 'N/A')}")


def record_answer(conn, student_id, mcq_id, is_correct, time_spent, session_id, session_type, selected_option=""):
    conn.execute("""
        INSERT INTO student_answer_log
        (student_id, mcq_id, is_correct, time_spent_seconds, session_type,
         session_id, selected_option)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (student_id, mcq_id, is_correct, time_spent, session_type, session_id, selected_option))


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


def increment_appearance_counter(conn, mcq_id):
    conn.execute(
        "UPDATE question_bank SET appearance_counter = appearance_counter + 1 WHERE id = ?",
        (mcq_id,),
    )


def show_answer(mcq, student_answer, correct_option):
    print(f"\nYour answer: {student_answer} | Correct: {correct_option}")
    is_correct = student_answer.upper() == correct_option.upper()
    if is_correct:
        print("  Correct!")
    else:
        print(f"  Wrong. The correct answer was {correct_option}.")

    correct_answer = json.loads(mcq['correct_answer']) if isinstance(mcq['correct_answer'], str) else mcq['correct_answer']
    print(f"\n  Explanation: {correct_answer.get('text', 'N/A')}")


def get_baseline_accuracy(conn, student_id) -> float | None:
    """Average accuracy across completed exam sessions."""
    row = conn.execute("""
        SELECT
            AVG(acc) as avg_accuracy
        FROM (
            SELECT
                session_id,
                SUM(is_correct) * 1.0 / COUNT(*) as acc
            FROM student_answer_log
            WHERE student_id = ?
              AND session_type LIKE 'exam%'
            GROUP BY session_id
        )
    """, (student_id,)).fetchone()
    return row['avg_accuracy'] if row and row['avg_accuracy'] is not None else None


# ---------------------------------------------------------------------------
# Session runner
# ---------------------------------------------------------------------------

def run_adaptive_session(
    student_id: str,
    count: int = 30,
    mode: str = "adaptive",
    topic: str | None = None,
    chapter: str | None = None,
):
    """Run an adaptive practice session.

    Modes:
    - adaptive:  combined weights (weakness*0.40 + debt*0.35 + importance*0.25)
    - weakness:  pure weakness signal (1.0 - accuracy per topic)
    - coverage: pure debt signal (how unseen a topic is)
    - random:    uniform random selection

    If topic or chapter is set, AI generates MCQs for that topic first if it has
    fewer than GAP_THRESHOLD MCQs, then builds the session.

    After all MCQs, prints per-topic accuracy vs exam baseline.
    """
    conn = get_db()
    session_id = str(uuid.uuid4())

    print(f"\n{'='*50}")
    print(f"  Adaptive Practice — mode={mode}")
    print(f"  Student: {student_id} | Questions: {count}")
    if topic:
        print(f"  Topic: {topic}")
    if chapter:
        print(f"  Chapter: {chapter}")
    print(f"{'='*50}\n")

    # --- Generate fresh MCQs when student explicitly picks topic/chapter ---
    if (topic or chapter) and MCQ.ENABLE_GENERATION_ON_SELECTION:
        topics_to_generate = []
        if topic:
            row = conn.execute(
                "SELECT topic_id, topic_name, chapter_id, chapter_name, book_page_range, source_file "
                "FROM question_bank WHERE LOWER(topic_name)=LOWER(?) LIMIT 1",
                (topic,),
            ).fetchone()
            if row:
                topics_to_generate.append(dict(row))
        elif chapter:
            rows = conn.execute(
                "SELECT DISTINCT topic_id, topic_name, chapter_id, chapter_name, book_page_range, source_file "
                "FROM question_bank WHERE LOWER(chapter_name)=LOWER(?)",
                (chapter,),
            ).fetchall()
            topics_to_generate = [dict(r) for r in rows]

        if topics_to_generate:
            print(f"[Generation] Creating {MCQ.MIN_GENERATED_ON_SELECTION} fresh MCQs per topic...\n")
            client, store_name, gen_config, cite_config = setup_generator()
            for t in topics_to_generate:
                n = generate_fresh_mcqs(
                    t["topic_name"], t["chapter_id"], t["chapter_name"],
                    t.get("book_page_range", "") or "", t.get("source_file", "") or "",
                    MCQ.MIN_GENERATED_ON_SELECTION,
                    conn, client, store_name, gen_config, cite_config,
                )
                print(f"  [Generated] {n} fresh MCQs for '{t['topic_name']}'")
            print()

    # --- Build session ---
    mcqs, _ = build_session(conn, student_id, count=count, mode=mode, topic=topic, chapter=chapter)
    if not mcqs:
        print("ERROR: No MCQs available for this session.")
        conn.close()
        return

    print(f"Loaded {len(mcqs)} questions. Starting...\n")
    print("Type the option letter (A/B/C/D) and press Enter.\n")

    # --- Log session sampling ---
    log_session_sampling(conn, session_id, student_id, f"adaptive_{mode}", mcqs)

    # --- Serve MCQs ---
    for i, mcq in enumerate(mcqs, 1):
        # Ensure options is a dict
        opts = mcq.get('options', '{}')
        if isinstance(opts, str):
            opts = json.loads(opts)
        mcq['options'] = opts

        display_mcq(mcq, i, len(mcqs), blind=False)

        start = time.time()
        student_answer = input("Your answer (A/B/C/D): ").strip().upper()
        elapsed = int(time.time() - start)

        # Resolve correct option
        ca = mcq.get('correct_answer', '{}')
        if isinstance(ca, str):
            ca = json.loads(ca)
        correct_option = ca.get('option', 'A')

        show_answer(mcq, student_answer, correct_option)
        is_correct = student_answer.upper() == correct_option.upper()

        # Persist
        record_answer(conn, student_id, mcq['id'], is_correct, elapsed, session_id, f"adaptive_{mode}", student_answer)
        upsert_stats(conn, student_id, mcq['id'], is_correct)
        increment_appearance_counter(conn, mcq['id'])

    conn.commit()
    conn.close()

    # --- Print summary ---
    _print_summary(student_id, count, mode)


def _print_summary(student_id: str, count: int, mode: str):
    """Print per-topic accuracy and comparison to baseline."""
    conn = get_db()

    print(f"\n{'='*50}")
    print(f"  ADAPTIVE SESSION SUMMARY — {student_id} (mode={mode})")
    print(f"{'='*50}")

    # Per-topic accuracy for this session
    rows = conn.execute("""
        SELECT
            qb.topic_name, qb.chapter_name,
            COUNT(*) as total,
            SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log sal
        JOIN question_bank qb ON sal.mcq_id = qb.id
        WHERE sal.student_id = ?
          AND sal.session_type = ?
        GROUP BY qb.topic_name
        ORDER BY (CAST(SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*)) ASC
    """, (student_id, f"adaptive_{mode}")).fetchall()

    print(f"\nPer-topic accuracy (this session):\n")
    for r in rows:
        pct = (r['correct'] / r['total']) * 100 if r['total'] > 0 else 0
        bar = '█' * int(pct // 10) + '░' * (10 - int(pct // 10))
        flag = "  <- WEAK" if pct < 60 else "  <- STRONG"
        print(f"  {r['topic_name']}: {bar} {pct:.0f}%{flag}")

    # Overall session accuracy
    overall = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct
        FROM student_answer_log
        WHERE student_id = ?
          AND session_type = ?
    """, (student_id, f"adaptive_{mode}")).fetchone()

    if overall['total'] > 0:
        session_pct = (overall['correct'] / overall['total']) * 100
        print(f"\nSession accuracy: {overall['correct']}/{overall['total']} = {session_pct:.1f}%")

    # Compare to exam baseline
    baseline = get_baseline_accuracy(conn, student_id)
    if baseline is not None and overall['total'] > 0:
        delta = session_pct - (baseline * 100)
        sign = "+" if delta >= 0 else ""
        print(f"Exam baseline: {baseline*100:.1f}%")
        print(f"vs baseline:   {sign}{delta:.1f}%")
    else:
        print("\n(No exam sessions completed yet — no baseline to compare against.)")

    # Weak topics for next session
    weak_topics = [r['topic_name'] for r in rows if (r['correct'] / r['total']) < 0.6]
    if weak_topics:
        print(f"\nFocus areas for next session: {', '.join(weak_topics)}")

    conn.close()
