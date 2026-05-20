"""Bank statistics overview."""

import sqlite3
from itertools import groupby

from shokti.core.config import DB_PATH, MCQ


def main():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        total = conn.execute("SELECT COUNT(*) as c FROM question_bank").fetchone()["c"]

        # Single query: chapters with topic counts
        chapters = conn.execute("""
            SELECT chapter_id, chapter_name,
                   COUNT(DISTINCT topic_id) as topic_count,
                   COUNT(*) as mcq_count
            FROM question_bank
            GROUP BY chapter_id
            ORDER BY chapter_id
        """).fetchall()

        # Single query: all topics grouped by chapter
        all_topics = conn.execute("""
            SELECT chapter_id, topic_id, topic_name, COUNT(*) as mcq_count
            FROM question_bank
            GROUP BY chapter_id, topic_id
            ORDER BY chapter_id, mcq_count ASC
        """).fetchall()

        topics_by_chapter = {}
        for key, group in groupby(all_topics, key=lambda r: r['chapter_id']):
            topics_by_chapter[key] = list(group)

        print("=" * 50)
        print("=== Question Bank Overview ===")
        print(f"Total MCQs: {total}")
        print(f"Total Chapters: {len(chapters)}")
        print()

        for ch in chapters:
            print(f"Chapter {ch['chapter_id']} — {ch['chapter_name']}")
            print(f"  Topics: {ch['topic_count']} | MCQs: {ch['mcq_count']}")

            topics = topics_by_chapter.get(ch['chapter_id'], [])
            print("  Topics:")
            for t in topics:
                flag = " ← FEW MCQs" if t["mcq_count"] < MCQ.GAP_THRESHOLD else ""
                print(f"    {t['topic_name']}: {t['mcq_count']} MCQs{flag}")
            print()

        print("=== Coverage Gaps ===")
        gaps = conn.execute("""
            SELECT chapter_id, topic_name, COUNT(*) as mcq_count
            FROM question_bank
            GROUP BY chapter_id, topic_id
            HAVING mcq_count < ?
            ORDER BY mcq_count ASC
        """, (MCQ.GAP_THRESHOLD,)).fetchall()

        if not gaps:
            print("  No gaps found — all topics have 5+ MCQs")
        else:
            for g in gaps:
                print(f"  {g['topic_name']} (Chapter {g['chapter_id']}): {g['mcq_count']} MCQs")
    finally:
        conn.close()


if __name__ == "__main__":
    main()