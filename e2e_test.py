"""E2E test harness for Shokti MCQ System."""
import sys
sys.path.insert(0, ".")

import random
import datetime
import sqlite3
from shokti.core.config import DB_PATH


# ---------------------------------------------------------------------------
# Robust input() auto-patch — saves original before any patching
# ---------------------------------------------------------------------------
import builtins
_orig_input = builtins.input

_mock_on = False
def _auto_input(prompt=""):
    if _mock_on:
        choice = random.choice(["A", "B", "C", "D"])
        print(f"  [AUTO: {choice}]")
        return choice
    return _orig_input(prompt)

builtins.input = _auto_input


def _patch_on():
    global _mock_on
    _mock_on = True

def _patch_off():
    global _mock_on
    _mock_on = False


# ---------------------------------------------------------------------------
# Session runners
# ---------------------------------------------------------------------------
def run_exam(exam_id: str, student_id: str):
    from shokti.exams.exam_runner import run_exam as _run
    _patch_on()
    try:
        result = _run(exam_id, student_id)
    finally:
        _patch_off()

def run_adaptive(student_id: str, count=30, mode="adaptive", topic=None, chapter=None):
    from shokti.adaptive_practice import run_adaptive_session
    _patch_on()
    try:
        run_adaptive_session(
            student_id=student_id, count=count, mode=mode,
            topic=topic, chapter=chapter,
        )
    finally:
        _patch_off()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(label: str, student_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT session_type, COUNT(*) as total,
               SUM(is_correct) as correct
        FROM student_answer_log
        WHERE student_id=?
        GROUP BY session_type
    """, (student_id,)).fetchall()

    print(f"\n  [{label}] answer log:")
    for r in rows:
        pct = r["correct"] / r["total"] * 100 if r["total"] > 0 else 0
        print(f"    {r['session_type']}: {r['correct']}/{r['total']} ({pct:.0f}%)")

    sm2 = conn.execute(
        "SELECT COUNT(*) as c FROM student_mcq_stats WHERE student_id=?",
        (student_id,),
    ).fetchone()
    print(f"  [{label}] SM-2 entries: {sm2['c']}")

    top = conn.execute(
        "SELECT id, topic_name, appearance_counter FROM question_bank ORDER BY appearance_counter DESC LIMIT 3"
    ).fetchall()
    print(f"  [{label}] top appearance_counter: {[(r['id'], r['topic_name'][:15], r['appearance_counter']) for r in top]}")

    gen = conn.execute(
        "SELECT COUNT(*) as c FROM question_bank WHERE origin='generated'"
    ).fetchone()
    print(f"  [{label}] generated MCQs: {gen['c']}")

    tsl = conn.execute(
        "SELECT COUNT(*) as c FROM topic_sampling_log WHERE student_id=?",
        (student_id,),
    ).fetchone()
    print(f"  [{label}] topic_sampling_log entries: {tsl['c']}")

    conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ts = datetime.datetime.now().strftime("%H%M%S")
    student_id = f"e2e_{ts}"

    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM student_answer_log WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM student_mcq_stats WHERE student_id=?", (student_id,))
    conn.execute("DELETE FROM topic_sampling_log WHERE student_id=?", (student_id,))
    conn.commit()
    conn.close()
    print(f"Student: {student_id}")

    # ── PHASE 1: 3 Fixed Exams ────────────────────────────────────────────
    print("\n" + "="*60)
    print("PHASE 1 — FIXED EXAMS (3 diagnostic)")
    print("="*60)

    for exam_id in ["1", "2", "3"]:
        print(f"\n--- Exam {exam_id} ---")
        run_exam(exam_id, student_id)
        verify(f"post-exam{exam_id}", student_id)

    # ── PHASE 2: Student picks topic (AI generates if gap) ─────────────────
    print("\n" + "="*60)
    print("PHASE 2 — STUDENT PICKS TOPIC")
    print("="*60)

    print("\n--- Adaptive: topic=Pteris (10q) [has 31 MCQs — no gap] ---")
    run_adaptive(student_id, count=10, mode="adaptive", topic="Pteris")
    verify("post-Pteris", student_id)

    print("\n--- Adaptive: topic=ব্রায়োফাইটা (10q) [has 15 MCQs — no gap] ---")
    run_adaptive(student_id, count=10, mode="adaptive", topic="ব্রায়োফাইটা")
    verify("post-Bryophyta", student_id)

    print("\n--- Adaptive: chapter=টিস্যু ও টিস্যুতন্ত্র (10q) ---")
    run_adaptive(student_id, count=10, mode="adaptive", chapter="টিস্যু ও টিস্যুতন্ত্র")
    verify("post-Chapter08", student_id)

    # ── PHASE 3: Mode variations ─────────────────────────────────────────
    print("\n" + "="*60)
    print("PHASE 3 — MODE VARIATIONS")
    print("="*60)

    print("\n--- Weakness mode (10q) ---")
    run_adaptive(student_id, count=10, mode="weakness")
    verify("post-weakness", student_id)

    print("\n--- Coverage mode (10q) ---")
    run_adaptive(student_id, count=10, mode="coverage")
    verify("post-coverage", student_id)

    # ── PHASE 4: AI picks topic ────────────────────────────────────────────
    print("\n" + "="*60)
    print("PHASE 4 — AI PICKS TOPIC (adaptive mode)")
    print("="*60)

    print("\n--- Adaptive: AI picks (20q) ---")
    run_adaptive(student_id, count=20, mode="adaptive")
    verify("post-AI-picks", student_id)

    # ── Final ──────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("E2E TEST COMPLETE")
    print("="*60)
    verify("FINAL", student_id)


if __name__ == "__main__":
    main()
