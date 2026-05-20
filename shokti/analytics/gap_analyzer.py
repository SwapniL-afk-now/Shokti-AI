"""Coverage gap analyzer."""

import sqlite3

from shokti.core.config import DB_PATH, MCQ


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("=" * 50)
    print("=== Coverage Gap Analysis ===")
    print(f"Gap threshold: < {MCQ.GAP_THRESHOLD} MCQs per topic\n")

    gaps = conn.execute("""
        SELECT
            qb.chapter_id, qb.chapter_name, qb.topic_id, qb.topic_name,
            COUNT(*) as mcq_count, qb.book_page_range, qb.source_file
        FROM question_bank qb
        GROUP BY qb.chapter_id, qb.topic_id
        HAVING COUNT(*) < ?
        ORDER BY COUNT(*) ASC, qb.topic_name
    """, (MCQ.GAP_THRESHOLD,)).fetchall()

    if not gaps:
        print("No gaps found — all topics have enough MCQs.")
        conn.close()
        return

    print(f"Found {len(gaps)} gap topics:\n")
    for i, g in enumerate(gaps, 1):
        print(f"{i}. Chapter {g['chapter_id']} — {g['topic_name']}")
        print(f"   MCQs: {g['mcq_count']} (need {MCQ.GAP_THRESHOLD - g['mcq_count']} more)")
        print(f"   Book pages: {g['book_page_range']}")
        print(f"   Source: {g['source_file']}")
        print()

    print("=== Priority Order for MCQ Generation ===")
    for i, g in enumerate(gaps, 1):
        print(f"  {i}. {g['topic_name']} (Chapter {g['chapter_id']}) — {g['mcq_count']} MCQs")

    conn.close()


if __name__ == "__main__":
    main()