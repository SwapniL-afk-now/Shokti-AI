"""Exam definitions, loading, and unlock logic."""
from pathlib import Path
import json

EXAM_DIR = Path(__file__).parent


def get_exam_files() -> dict[str, str]:
    """Discover all exam_*.json files dynamically. No hardcoded exam IDs."""
    files = {}
    for path in sorted(EXAM_DIR.glob("exam_*.json")):
        exam_id = path.stem.replace("exam_", "")
        files[exam_id] = path.name
    return files


# Lazily computed — call get_exam_files() each time to pick up new files
def EXAM_IDS() -> list[str]:
    return list(get_exam_files().keys())


def load_exam(exam_id: str) -> dict:
    """Load exam JSON definition."""
    files = get_exam_files()
    path = EXAM_DIR / files[exam_id]
    with open(path) as f:
        return json.load(f)


def get_student_exam_status(conn, student_id: str) -> dict:
    """Return which exams the student has completed."""
    exam_ids = EXAM_IDS()
    status = {eid: False for eid in exam_ids}
    placeholders = ",".join([f"'exam{eid}'" for eid in exam_ids])
    cursor = conn.execute(
        f"""
        SELECT session_type
        FROM student_answer_log
        WHERE student_id = ? AND session_type IN ({placeholders})
        GROUP BY session_type
        """,
        (student_id,),
    )
    for row in cursor:
        stype = row[0]
        for eid in exam_ids:
            if stype == f"exam{eid}":
                status[eid] = True
    return status


def has_completed_all_exams(conn, student_id: str) -> bool:
    """Return True if student finished all available exams."""
    status = get_student_exam_status(conn, student_id)
    return bool(status) and all(status.values())


def get_next_incomplete_exam(conn, student_id: str) -> str | None:
    """Return the next exam_id the student hasn't taken, or None."""
    status = get_student_exam_status(conn, student_id)
    for exam_id in EXAM_IDS():
        if not status[exam_id]:
            return exam_id
    return None