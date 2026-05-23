"""Multi-subject migration: add subjects and books tables.

Run once to migrate the database from single-Biology to multi-subject architecture.
Safe to re-run — uses CREATE TABLE IF NOT EXISTS and ON CONFLICT clauses.

Usage:
    python3 db/migrate_multi_subject.py
"""

import sys
import sqlite3
from pathlib import Path

from shokti.core.config import GEMINI

DB_PATH = Path(__file__).resolve().parents[1] / "db" / "question_bank.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    # ── 1. Create subjects table ─────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            display_name TEXT,
            language    TEXT DEFAULT 'bn',
            gemini_store_name TEXT,
            gemini_store_display_name TEXT,
            sort_order  INTEGER DEFAULT 0
        )
    """)
    print("✓ subjects table created")

    # ── 2. Create books table ──────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id          TEXT PRIMARY KEY,
            subject_id  TEXT NOT NULL,
            title       TEXT NOT NULL,
            source_file TEXT,
            chapter_count INTEGER,
            sort_order  INTEGER DEFAULT 0,
            FOREIGN KEY (subject_id) REFERENCES subjects(id)
        )
    """)
    print("✓ books table created")

    # ── 3. Add book_id column to question_bank ────────────────────────────
    # Check if column already exists
    cols = [r[1] for r in conn.execute("PRAGMA table_info(question_bank)").fetchall()]
    if "book_id" not in cols:
        conn.execute("ALTER TABLE question_bank ADD COLUMN book_id TEXT REFERENCES books(id)")
        print("✓ book_id column added to question_bank")
    else:
        print("  book_id column already exists")

    # ── 4. Insert Biology subject (default, matches current config) ───────
    conn.execute(f"""
        INSERT OR IGNORE INTO subjects
          (id, name, display_name, language,
           gemini_store_name, gemini_store_display_name, sort_order)
        VALUES
          ('biology', 'Biology', 'Biology (Hasan Sir)', 'bn',
           '{GEMINI.STORE_NAME}',
           '{GEMINI.STORE_DISPLAY_NAME}', 0)
    """)
    print("✓ biology subject inserted")

    # ── 5. Insert default book ─────────────────────────────────────────────
    conn.execute("""
        INSERT OR IGNORE INTO books
          (id, subject_id, title, source_file, chapter_count, sort_order)
        VALUES
          ('bio_hasan_sir_1st', 'biology',
           'Biology 1st Paper (Hasan Sir)',
           'biology_hasan_sir_1st.pdf', 2, 0)
    """)
    print("✓ bio_hasan_sir_1st book inserted")

    # ── 6. Backfill existing rows with biology default ─────────────────────
    # Rows with no book_id get the default
    updated = conn.execute("""
        UPDATE question_bank
        SET book_id = 'bio_hasan_sir_1st',
            subject = 'biology'
        WHERE book_id IS NULL
    """).rowcount
    conn.commit()
    print(f"✓ backfilled {updated} rows with default subject/book")

    # ── 7. Verify ─────────────────────────────────────────────────────────
    subject_count = conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
    book_count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    backfill_check = conn.execute(
        "SELECT COUNT(*) FROM question_bank WHERE book_id IS NOT NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM question_bank").fetchone()[0]

    print(f"\n=== Migration Complete ===")
    print(f"  subjects: {subject_count}")
    print(f"  books: {book_count}")
    print(f"  question_bank rows with book_id: {backfill_check}/{total}")

    conn.close()


if __name__ == "__main__":
    migrate()