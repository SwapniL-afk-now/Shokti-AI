"""Schema migration v2: Add Phase 0 tracking columns and tables.

Adds:
- `session_type` + `session_id` columns to student_answer_log
- `topic_sampling_log` table (per-session topic frequency)
- `exam_trend` table (topic importance from exam data)
- Backfill existing rows with session_type='diagnostic'
- Reset appearance_counter to 0 for all existing MCQs

Usage:
    python shokti/ingest/schema_migration_v2.py
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shokti.core.config import DB_PATH


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def column_exists(conn, table, column):
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c["name"] == column for c in cols)


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def index_exists(conn, index_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row is not None


def migrate_student_answer_log(conn):
    """Add session_type and session_id columns to student_answer_log."""
    print("  Adding session_type column...", end=" ")
    if not column_exists(conn, "student_answer_log", "session_type"):
        conn.execute(
            "ALTER TABLE student_answer_log ADD COLUMN session_type TEXT DEFAULT 'diagnostic'"
        )
        conn.execute(
            "UPDATE student_answer_log SET session_type = 'diagnostic' WHERE session_type IS NULL"
        )
        print("done")
    else:
        print("already exists")

    print("  Adding session_id column...", end=" ")
    if not column_exists(conn, "student_answer_log", "session_id"):
        conn.execute(
            "ALTER TABLE student_answer_log ADD COLUMN session_id TEXT"
        )
        print("done")
    else:
        print("already exists")

    print("  Adding selected_option column...", end=" ")
    if not column_exists(conn, "student_answer_log", "selected_option"):
        conn.execute(
            "ALTER TABLE student_answer_log ADD COLUMN selected_option TEXT DEFAULT ''"
        )
        print("done")
    else:
        print("already exists")

    conn.commit()


def create_topic_sampling_log_table(conn):
    """Create topic_sampling_log table with indexes."""
    print("  Creating topic_sampling_log table...", end=" ")
    if not table_exists(conn, "topic_sampling_log"):
        conn.executescript("""
            CREATE TABLE topic_sampling_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                student_id TEXT,
                session_type TEXT,
                chapter_id TEXT,
                topic_id TEXT,
                times_sampled INTEGER DEFAULT 0,
                session_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_tsl_student ON topic_sampling_log(student_id);
            CREATE INDEX IF NOT EXISTS idx_tsl_session ON topic_sampling_log(session_id);
        """)
        print("done")
    else:
        print("already exists")
    conn.commit()


def create_exam_trend_table(conn):
    """Create exam_trend table."""
    print("  Creating exam_trend table...", end=" ")
    if not table_exists(conn, "exam_trend"):
        conn.executescript("""
            CREATE TABLE exam_trend (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id TEXT,
                topic_id TEXT,
                appearance_frequency REAL DEFAULT 0.0,
                trend_direction TEXT DEFAULT 'stable',
                last_analyzed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("done")
    else:
        print("already exists")
    conn.commit()


def reset_appearance_counter(conn):
    """Set appearance_counter=0 for all existing question_bank rows."""
    print("  Resetting appearance_counter to 0...", end=" ")
    conn.execute("UPDATE question_bank SET appearance_counter = 0")
    conn.commit()
    count = conn.execute("SELECT COUNT(*) as c FROM question_bank").fetchone()["c"]
    print(f"done ({count} MCQs reset)")


def create_indexes(conn):
    """Create index on student_answer_log.session_id."""
    print("  Creating idx_student_answer_session index...", end=" ")
    if not index_exists(conn, "idx_student_answer_session"):
        conn.execute("""
            CREATE INDEX idx_student_answer_session
            ON student_answer_log(session_id)
        """)
        conn.commit()
        print("done")
    else:
        print("already exists")


def check_migration_status(conn):
    """Print current migration state - what has been applied."""
    print("\n=== Migration Status ===\n")

    # student_answer_log columns
    log_cols = conn.execute("PRAGMA table_info(student_answer_log)").fetchall()
    col_names = [c["name"] for c in log_cols]
    has_session_type = "session_type" in col_names
    has_session_id = "session_id" in col_names
    print(f"student_answer_log:")
    print(f"  - session_type column: {'YES' if has_session_type else 'NO'}")
    print(f"  - session_id column: {'YES' if has_session_id else 'NO'}")

    # Tables
    for table in ["topic_sampling_log", "exam_trend"]:
        exists = table_exists(conn, table)
        if exists:
            count = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()["c"]
            print(f"  {table}: EXISTS ({count} rows)")
        else:
            print(f"  {table}: MISSING")

    # Index on session_id
    has_idx = index_exists(conn, "idx_student_answer_session")
    print(f"  idx_student_answer_session on student_answer_log: {'YES' if has_idx else 'NO'}")

    # appearance_counter status
    total = conn.execute("SELECT COUNT(*) as c FROM question_bank").fetchone()["c"]
    at_zero = conn.execute(
        "SELECT COUNT(*) as c FROM question_bank WHERE appearance_counter = 0"
    ).fetchone()["c"]
    print(f"\nquestion_bank: {total} MCQs, {at_zero}/{total} with appearance_counter=0")


def print_summary(conn):
    """Show final state after migration."""
    print("\n=== Migration Summary ===\n")

    log_cols = conn.execute("PRAGMA table_info(student_answer_log)").fetchall()
    print(f"student_answer_log columns: {', '.join(c['name'] for c in log_cols)}")

    for table in ["topic_sampling_log", "exam_trend"]:
        exists = table_exists(conn, table)
        if exists:
            count = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()["c"]
            print(f"{table}: {count} rows")
        else:
            print(f"{table}: MISSING")

    qb_count = conn.execute("SELECT COUNT(*) as c FROM question_bank").fetchone()["c"]
    at_zero = conn.execute(
        "SELECT COUNT(*) as c FROM question_bank WHERE appearance_counter = 0"
    ).fetchone()["c"]
    print(f"question_bank: {at_zero}/{qb_count} with appearance_counter=0")

    has_idx = index_exists(conn, "idx_student_answer_session")
    print(f"idx_student_answer_session: {'YES' if has_idx else 'NO'}")


def main():
    print("=" * 50)
    print("Schema Migration v2")
    print("=" * 50)

    conn = get_db()

    print("\n[1/5] Migrating student_answer_log...")
    migrate_student_answer_log(conn)

    print("\n[2/5] Creating topic_sampling_log table...")
    create_topic_sampling_log_table(conn)

    print("\n[3/5] Creating exam_trend table...")
    create_exam_trend_table(conn)

    print("\n[4/5] Resetting appearance_counter...")
    reset_appearance_counter(conn)

    print("\n[5/5] Creating session_id index...")
    create_indexes(conn)

    print("\n" + "=" * 50)
    print_summary(conn)
    print("=" * 50)

    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
