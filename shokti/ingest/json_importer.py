"""JSON to SQLite importer."""

import hashlib
import json
import sqlite3
from pathlib import Path

from shokti.core.config import ROOT_DIR, DB_PATH


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS question_bank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            book_id TEXT,
            chapter_id TEXT,
            chapter_name TEXT,
            book_page_range TEXT,
            source_file TEXT,
            topic_id TEXT,
            topic_name TEXT,
            question TEXT,
            options TEXT,
            correct_answer TEXT,
            source_quote TEXT DEFAULT '',
            pdf_page_number INTEGER,
            practice_related_questions TEXT DEFAULT '[]',
            appearance_counter INTEGER DEFAULT 0,
            question_hash TEXT UNIQUE,
            difficulty TEXT DEFAULT 'medium',
            origin TEXT DEFAULT 'question_bank',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS student_answer_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            mcq_id INTEGER,
            is_correct BOOLEAN,
            confidence_rating INTEGER,
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            time_spent_seconds INTEGER,
            FOREIGN KEY (mcq_id) REFERENCES question_bank(id)
        );

        CREATE TABLE IF NOT EXISTS student_mcq_stats (
            student_id TEXT,
            mcq_id INTEGER,
            correct_count INTEGER DEFAULT 0,
            wrong_count INTEGER DEFAULT 0,
            last_seen_at TIMESTAMP,
            next_review_at TIMESTAMP,
            FOREIGN KEY (mcq_id) REFERENCES question_bank(id),
            PRIMARY KEY (student_id, mcq_id)
        );

        CREATE INDEX IF NOT EXISTS idx_chapter ON question_bank(chapter_id);
        CREATE INDEX IF NOT EXISTS idx_topic ON question_bank(topic_id);
        CREATE INDEX IF NOT EXISTS idx_hash ON question_bank(question_hash);
        CREATE INDEX IF NOT EXISTS idx_student_answer ON student_answer_log(student_id, mcq_id);
    """)
    conn.commit()


def question_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def import_chapter(conn, chapter_data, subject, book_id):
    chapter_id = chapter_data["chapter_id"]
    chapter_name = chapter_data["chapter_name"]
    book_page_range = chapter_data.get("book_page_range", "")
    source_file = chapter_data.get("source_file", "")

    count = 0
    for topic in chapter_data.get("topics", []):
        topic_id = topic["topic_id"]
        topic_name = topic["topic_name"]
        for mcq in topic.get("mcqs", []):
            qh = question_hash(mcq["question"])
            conn.execute(
                """
                INSERT OR IGNORE INTO question_bank
                (subject, chapter_id, chapter_name, book_page_range, source_file,
                 topic_id, topic_name, question, options, correct_answer, question_hash,
                 origin, book_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject, chapter_id, chapter_name, book_page_range, source_file,
                    topic_id, topic_name,
                    mcq["question"],
                    json.dumps(mcq["options"]),
                    json.dumps(mcq["correct_answer"]),
                    qh,
                    "question_bank",
                    book_id,
                ),
            )
            count += 1
    conn.commit()
    return count


def main():
    print("Creating schema...")
    conn = get_db()
    create_schema(conn)

    qbank_dir = ROOT_DIR / "question_bank"
    total = 0

    for path in sorted(qbank_dir.glob("chapter_*.json")):
        with open(path) as f:
            data = json.load(f)

        # Top-level subject from JSON (lowercase for DB)
        raw_subject = data.get("subject", "biology")
        subject = raw_subject.lower().replace(" ", "_")

        # book_id: try chapter-level then top-level
        book_id = None
        for ch in data.get("chapters", []):
            cid = ch.get("chapter_id", "")
            if cid:
                book_id = f"{subject}_chapter_{cid}"
                break

        for chapter in data.get("chapters", []):
            cid = chapter["chapter_id"]
            cname = chapter["chapter_name"]
            cnt = import_chapter(conn, chapter, subject, book_id)
            print(f"  Chapter {cid} ({cname}): {cnt} MCQs imported")
            total += cnt

    row = conn.execute("SELECT COUNT(*) as c FROM question_bank").fetchone()
    print(f"\nTotal MCQs in DB: {row['c']}")
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()