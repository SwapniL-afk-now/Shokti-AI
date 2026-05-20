"""Sampling weights engine for adaptive practice."""
import sqlite3
from shokti.core.config import DB_PATH, GEMINI

# Default weights
WEAKNESS_WEIGHT = 0.40
DEBT_WEIGHT = 0.35
IMPORTANCE_WEIGHT = 0.25
DEBT_PEER_WINDOW_DAYS = 30


def _get_all_topic_ids(conn: sqlite3.Connection) -> list[str]:
    """Return all distinct topic_ids in the question bank."""
    rows = conn.execute(
        "SELECT DISTINCT topic_id FROM question_bank ORDER BY topic_id"
    ).fetchall()
    return [r["topic_id"] for r in rows]


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalize weights so they sum to 1.0."""
    total = sum(weights.values())
    if total == 0:
        # All topics unseen — equal weight
        n = len(weights) if weights else 1
        return {k: 1.0 / n for k in weights}
    return {k: v / total for k, v in weights.items()}


def compute_weakness_signal(conn: sqlite3.Connection, student_id: str) -> dict[str, float]:
    """
    Low accuracy → high weight.
    weight = 1.0 - accuracy, where accuracy = correct/(correct+wrong) per topic.
    Topics with no data get weight 1.0.
    """
    topic_ids = _get_all_topic_ids(conn)
    result = {}

    # accuracy per topic from student_answer_log joined with question_bank
    rows = conn.execute("""
        SELECT
            q.topic_id,
            SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) as correct,
            SUM(CASE WHEN sal.is_correct = 0 THEN 1 ELSE 0 END) as wrong
        FROM student_answer_log sal
        JOIN question_bank q ON q.id = sal.mcq_id
        WHERE sal.student_id = ?
        GROUP BY q.topic_id
    """, (student_id,)).fetchall()

    accuracy_map = {}
    for r in rows:
        total = r["correct"] + r["wrong"]
        accuracy_map[r["topic_id"]] = r["correct"] / total if total > 0 else 0.0

    for topic_id in topic_ids:
        accuracy = accuracy_map.get(topic_id, None)
        if accuracy is None:
            result[topic_id] = 1.0
        else:
            result[topic_id] = 1.0 - accuracy

    return result


def compute_sampling_debt(conn: sqlite3.Connection, student_id: str) -> dict[str, float]:
    """
    Fewer times sampled → higher weight (relative to peer avg).
    debt = max(0, peer_avg - student_times) / peer_avg.
    Topics student never seen get debt=1.0.
    """
    topic_ids = _get_all_topic_ids(conn)

    # peer_avg per topic
    peer_rows = conn.execute("""
        SELECT topic_id, AVG(times_sampled) as peer_avg
        FROM topic_sampling_log
        GROUP BY topic_id
    """).fetchall()
    peer_avg_map = {r["topic_id"]: r["peer_avg"] for r in peer_rows}

    # student times per topic
    student_rows = conn.execute("""
        SELECT topic_id, SUM(times_sampled) as student_times
        FROM topic_sampling_log
        WHERE student_id = ?
        GROUP BY topic_id
    """, (student_id,)).fetchall()
    student_times_map = {r["topic_id"]: r["student_times"] for r in student_rows}

    result = {}
    for topic_id in topic_ids:
        peer_avg = peer_avg_map.get(topic_id, 0.0)
        student_times = student_times_map.get(topic_id, 0)

        if peer_avg == 0:
            result[topic_id] = 1.0
        else:
            debt = max(0, peer_avg - student_times) / peer_avg
            result[topic_id] = debt

    return result


def compute_exam_importance(conn: sqlite3.Connection) -> dict[str, float]:
    """
    From exam_trend table.
    Higher appearance_frequency → higher weight.
    Normalize so max=1.0.
    """
    topic_ids = _get_all_topic_ids(conn)

    rows = conn.execute("""
        SELECT topic_id, appearance_frequency
        FROM exam_trend
    """).fetchall()

    freq_map = {r["topic_id"]: r["appearance_frequency"] for r in rows}
    max_freq = max((v for v in freq_map.values() if v > 0), default=1.0)

    result = {}
    for topic_id in topic_ids:
        freq = freq_map.get(topic_id, 0.0)
        result[topic_id] = freq / max_freq

    return result


def get_combined_weights(conn: sqlite3.Connection, student_id: str) -> dict[str, float]:
    """
    Combine all 3 signals: weakness×W1 + debt×W2 + importance×W3.
    Normalize so weights sum to 1.0.
    Returns {topic_id: weight}.
    """
    weakness = compute_weakness_signal(conn, student_id)
    debt = compute_sampling_debt(conn, student_id)
    importance = compute_exam_importance(conn)

    combined = {}
    for topic_id in weakness:
        combined[topic_id] = (
            weakness[topic_id] * WEAKNESS_WEIGHT
            + debt[topic_id] * DEBT_WEIGHT
            + importance[topic_id] * IMPORTANCE_WEIGHT
        )

    return _normalize_weights(combined)


def log_session_sampling(
    conn: sqlite3.Connection,
    session_id: str,
    student_id: str,
    session_type: str,
    mcqs: list[dict],
) -> None:
    """
    mcqs is a list of dicts with topic_id.
    Group by topic_id → count times_sampled per topic.
    Upsert into topic_sampling_log so times_sampled accumulates.
    """
    # Count occurrences per topic
    topic_counts: dict[str, int] = {}
    topic_chapters: dict[str, str | None] = {}
    for mcq in mcqs:
        tid = mcq.get("topic_id")
        if tid:
            topic_counts[tid] = topic_counts.get(tid, 0) + 1
            if "chapter_id" in mcq:
                topic_chapters[tid] = mcq.get("chapter_id")

    for topic_id, count in topic_counts.items():
        chapter_id = topic_chapters.get(topic_id)

        existing = conn.execute("""
            SELECT id, times_sampled FROM topic_sampling_log
            WHERE student_id = ? AND topic_id = ?
        """, (student_id, topic_id)).fetchone()

        if existing:
            conn.execute("""
                UPDATE topic_sampling_log
                SET times_sampled = times_sampled + ?,
                    session_id = ?,
                    session_type = ?,
                    chapter_id = COALESCE(?, chapter_id)
                WHERE id = ?
            """, (count, session_id, session_type, chapter_id, existing["id"]))
        else:
            conn.execute("""
                INSERT INTO topic_sampling_log
                    (session_id, student_id, session_type, chapter_id, topic_id, times_sampled)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, student_id, session_type, chapter_id, topic_id, count))

    conn.commit()


def get_topic_stats(conn: sqlite3.Connection, student_id: str) -> dict:
    """
    Returns dict of {topic_id: {accuracy, times_sampled, last_seen}}.
    Accuracy from student_answer_log; times_sampled from topic_sampling_log.
    """
    topic_ids = _get_all_topic_ids(conn)

    # Accuracy per topic
    accuracy_rows = conn.execute("""
        SELECT
            q.topic_id,
            SUM(CASE WHEN sal.is_correct = 1 THEN 1 ELSE 0 END) as correct,
            SUM(CASE WHEN sal.is_correct = 0 THEN 1 ELSE 0 END) as wrong,
            MAX(sal.answered_at) as last_seen
        FROM student_answer_log sal
        JOIN question_bank q ON q.id = sal.mcq_id
        WHERE sal.student_id = ?
        GROUP BY q.topic_id
    """, (student_id,)).fetchall()

    accuracy_map = {}
    for r in accuracy_rows:
        total = r["correct"] + r["wrong"]
        accuracy_map[r["topic_id"]] = {
            "accuracy": r["correct"] / total if total > 0 else 0.0,
            "last_seen": r["last_seen"],
        }

    # Times sampled per topic
    sampled_rows = conn.execute("""
        SELECT topic_id, SUM(times_sampled) as times_sampled
        FROM topic_sampling_log
        WHERE student_id = ?
        GROUP BY topic_id
    """, (student_id,)).fetchall()

    sampled_map = {r["topic_id"]: r["times_sampled"] for r in sampled_rows}

    result = {}
    for topic_id in topic_ids:
        stats = accuracy_map.get(topic_id, {"accuracy": None, "last_seen": None})
        result[topic_id] = {
            "accuracy": stats["accuracy"],
            "times_sampled": sampled_map.get(topic_id, 0),
            "last_seen": stats["last_seen"],
        }

    return result