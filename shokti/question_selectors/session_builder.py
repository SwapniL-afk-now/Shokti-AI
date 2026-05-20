"""Adaptive session builder — multi-peaked gaussian question selection."""

import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from shokti.core.config import DB_PATH, MCQ
from shokti.question_selectors.weak_topic_tracker import (
    get_topic_stats,
    get_weak_topics,
    get_unseen_topics,
)


def _filter_mcqs(mcqs: list, topic: str | None, chapter: str | None) -> list:
    """Filter MCQ list by topic name or chapter name (case-insensitive)."""
    if not topic and not chapter:
        return mcqs
    filtered = []
    for m in mcqs:
        if topic and m.get('topic_name', '').lower() != topic.lower():
            continue
        if chapter and m.get('chapter_name', '').lower() != chapter.lower():
            continue
        filtered.append(m)
    return filtered


@dataclass
class SessionComposition:
    qbank_count: int = 0
    generated_count: int = 0
    weak_topic_count: int = 0
    unseen_count: int = 0
    other_count: int = 0


def _gaussian_weight(accuracy: float, mean: float, sigma: float) -> float:
    """Compute gaussian weight for a topic accuracy."""
    import math
    return math.exp(-0.5 * ((accuracy - mean) / sigma) ** 2)


def _select_by_weight(mcqs: list, weights: list[float], n: int) -> list:
    """Select n distinct items from mcqs using weighted probabilities without replacement.

    Uses the Ellen-Mann algorithm (inverted CDF walk): O(n log n) with numpy,
    or O(n + m log m) with plain Python cumsum. m = len(mcqs), n = selections.
    """
    import numpy as np

    m = len(mcqs)
    if n >= m:
        return list(range(m))

    w = np.array(weights, dtype=float)
    selected = []

    for _ in range(n):
        total = w.sum()
        if total <= 0:
            # All weights zero — fall back to uniform over remaining
            remaining = [i for i in range(m) if i not in selected]
            if remaining:
                chosen = random.choice(remaining)
                selected.append(chosen)
            break

        # Walk cumsum once to find the selected index
        cumsum = np.cumsum(w)
        r = random.random() * total
        idx = int(np.searchsorted(cumsum, r))
        if idx >= m:
            idx = m - 1

        selected.append(idx)
        w[idx] = 0.0  # remove from pool without replacement

    return selected


def build_session(
    conn: sqlite3.Connection,
    student_id: str,
    count: int = 30,
    mode: str = "adaptive",
    topic: str | None = None,
    chapter: str | None = None,
) -> tuple[list[dict], SessionComposition]:
    """
    Build an adaptive question session for a student.

    Modes:
    - 'adaptive': multi-peaked gaussian selection
    - 'weakness': focus on weak topics only
    - 'coverage': focus on unseen topics only

    Returns: (list of MCQ dicts, composition breakdown)
    """
    if mode == "weakness":
        return _build_weakness_session(conn, student_id, count, topic=topic, chapter=chapter)
    elif mode == "coverage":
        return _build_coverage_session(conn, student_id, count, topic=topic, chapter=chapter)
    elif mode == "adaptive":
        return _build_adaptive_session(conn, student_id, count, topic=topic, chapter=chapter)
    else:
        return _build_random_session(conn, count, topic=topic, chapter=chapter)


def _build_adaptive_session(
    conn: sqlite3.Connection,
    student_id: str,
    count: int,
    topic: str | None = None,
    chapter: str | None = None,
) -> tuple[list[dict], SessionComposition]:
    """Multi-peaked gaussian selection across qbank, generated, weak, and unseen.

    Optimized: single SQL fetch per origin, topic filter pushed to SQL,
    topic_stats computed once and reused for weak + unseen derivation.
    """
    target_qbank = int(count * MCQ.QBANK_RATIO)
    target_generated = int(count * MCQ.GENERATED_RATIO)
    target_weak = int(count * MCQ.WEAK_TOPIC_RATIO)
    target_unseen = count - target_qbank - target_generated - target_weak

    # Compute topic stats once — derive weak + seen from the same result
    topic_stats_list = get_topic_stats(conn, student_id)
    topic_stats = {t.topic_key: t for t in topic_stats_list}
    weak_topic_keys = {t.topic_key for t in topic_stats_list if t.accuracy < MCQ.WEAK_THRESHOLD}
    seen_topic_keys = {t.topic_key for t in topic_stats_list}
    unseen_topic_keys = set(get_unseen_topics(conn, student_id))

    # Build SQL filter for topic/chapter — push to SQLite B-tree, not Python
    def _sql_filter(where_clause, params):
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM question_bank WHERE {where_clause}", params
        ).fetchall()]

    if topic and chapter:
        # Both specified — match MCQs where topic AND chapter both match
        qbank_mcqs = _sql_filter(
            "origin = 'question_bank' AND topic_name = ? COLLATE NOCASE AND chapter_name = ? COLLATE NOCASE",
            [topic, chapter],
        )
        generated_mcqs = _sql_filter(
            "origin = 'generated' AND topic_name = ? COLLATE NOCASE AND chapter_name = ? COLLATE NOCASE",
            [topic, chapter],
        )
    elif topic:
        qbank_mcqs = _sql_filter(
            "origin = 'question_bank' AND topic_name = ? COLLATE NOCASE",
            [topic],
        )
        generated_mcqs = _sql_filter(
            "origin = 'generated' AND topic_name = ? COLLATE NOCASE",
            [topic],
        )
    elif chapter:
        qbank_mcqs = _sql_filter(
            "origin = 'question_bank' AND chapter_name = ? COLLATE NOCASE",
            [chapter],
        )
        generated_mcqs = _sql_filter(
            "origin = 'generated' AND chapter_name = ? COLLATE NOCASE",
            [chapter],
        )
    else:
        qbank_mcqs = _sql_filter("origin = 'question_bank'", [])
        generated_mcqs = _sql_filter("origin = 'generated'", [])

    all_mcqs = qbank_mcqs + generated_mcqs

    # Assign gaussian weights based on topic accuracy — single pass over combined list
    combined = qbank_mcqs + generated_mcqs
    weak_weights = []
    medium_weights = []
    for m in combined:
        key = (m['chapter_id'], m['topic_id'])
        stats = topic_stats.get(key)
        acc = stats.accuracy if stats else 0.0
        weak_weights.append(_gaussian_weight(acc, 0.1, 0.15))
        medium_weights.append(_gaussian_weight(acc, 0.5, 0.2))

    selected = []
    comp = SessionComposition()

    # 1. Pull from qbank — moderate weight toward weak topics
    qbank_weights = [
        1.0 if (m['chapter_id'], m['topic_id']) in weak_topic_keys else 0.4
        for m in qbank_mcqs
    ]
    qbank_selected_idx = _select_by_weight(qbank_mcqs, qbank_weights, min(target_qbank, len(qbank_mcqs)))
    for i in qbank_selected_idx:
        selected.append(qbank_mcqs[i])
        comp.qbank_count += 1

    # 2. Pull from generated — moderate weight toward weak topics
    generated_weights = [
        1.5 if (m['chapter_id'], m['topic_id']) in weak_topic_keys else 0.5
        for m in generated_mcqs
    ]
    gen_selected_idx = _select_by_weight(
        generated_mcqs, generated_weights, min(target_generated, len(generated_mcqs)),
    )
    for i in gen_selected_idx:
        selected.append(generated_mcqs[i])
        comp.generated_count += 1

    # 3. Pull from weak topics (any origin) — highest weight
    weak_mcqs = [m for m in all_mcqs
                 if (m['chapter_id'], m['topic_id']) in weak_topic_keys and m not in selected]
    weak_weights_list = [2.0 if (m['chapter_id'], m['topic_id']) in weak_topic_keys else 0.3
                   for m in weak_mcqs]
    for idx_m, m in enumerate(weak_mcqs):
        key = (m['chapter_id'], m['topic_id'])
        stats = topic_stats.get(key)
        if stats and stats.accuracy > 0:
            weak_weights_list[idx_m] *= (1.0 - stats.accuracy) * 2

    weak_needed = min(target_weak, len(weak_mcqs))
    if weak_mcqs:
        weak_selected_idx = _select_by_weight(weak_mcqs, weak_weights_list, weak_needed)
        for i in weak_selected_idx:
            selected.append(weak_mcqs[i])
            comp.weak_topic_count += 1

    # 4. Fill rest from unseen + medium topics
    remaining = [m for m in all_mcqs if m not in selected]
    unseen_mcqs = [m for m in remaining
                   if (m['chapter_id'], m['topic_id']) in unseen_topic_keys]
    other_mcqs = [m for m in remaining
                  if (m['chapter_id'], m['topic_id']) not in unseen_topic_keys]

    remaining_count = count - len(selected)
    if unseen_mcqs and target_unseen > 0:
        unseen_needed = min(target_unseen, len(unseen_mcqs))
        unseen_selected_idx = _select_by_weight(unseen_mcqs, [1.0] * len(unseen_mcqs), unseen_needed)
        for i in unseen_selected_idx:
            selected.append(unseen_mcqs[i])
            comp.unseen_count += 1
        remaining_count = count - len(selected)

    if remaining_count > 0 and other_mcqs:
        other_needed = min(remaining_count, len(other_mcqs))
        other_selected_idx = _select_by_weight(other_mcqs, [0.5] * len(other_mcqs), other_needed)
        for i in other_selected_idx:
            selected.append(other_mcqs[i])
            comp.other_count += 1

    # Shuffle to avoid predictable ordering
    random.shuffle(selected)

    return selected[:count], comp


def _build_weakness_session(
    conn: sqlite3.Connection,
    student_id: str,
    count: int,
    topic: str | None = None,
    chapter: str | None = None,
) -> tuple[list[dict], SessionComposition]:
    """Focus on student's weak topics."""
    topic_stats_list = get_topic_stats(conn, student_id)
    weak_keys = {t.topic_key for t in topic_stats_list if t.accuracy < MCQ.WEAK_THRESHOLD}
    if not weak_keys:
        return _build_random_session(conn, count, topic=topic, chapter=chapter)

    weak_clause = " OR ".join([f"(chapter_id = ? AND topic_id = ?)" for _ in weak_keys])
    if not weak_clause:
        return _build_random_session(conn, count, topic=topic, chapter=chapter)
    params = [p for pair in weak_keys for p in pair]
    mcqs = [dict(r) for r in conn.execute(
        f"SELECT * FROM question_bank WHERE {weak_clause}", params
    ).fetchall()]
    if topic or chapter:
        mcqs = _filter_mcqs(mcqs, topic, chapter)

    random.shuffle(mcqs)
    comp = SessionComposition(weak_topic_count=min(count, len(mcqs)))
    return mcqs[:count], comp


def _build_coverage_session(
    conn: sqlite3.Connection,
    student_id: str,
    count: int,
    topic: str | None = None,
    chapter: str | None = None,
) -> tuple[list[dict], SessionComposition]:
    """Focus on unseen topics."""
    unseen_keys = get_unseen_topics(conn, student_id)
    if not unseen_keys:
        return _build_random_session(conn, count, topic=topic, chapter=chapter)

    unseen_clause = " OR ".join([f"(chapter_id = ? AND topic_id = ?)" for _ in unseen_keys])
    params = [p for pair in unseen_keys for p in pair]
    mcqs = [dict(r) for r in conn.execute(
        f"SELECT * FROM question_bank WHERE {unseen_clause}", params
    ).fetchall()]
    if topic or chapter:
        mcqs = _filter_mcqs(mcqs, topic, chapter)

    random.shuffle(mcqs)
    comp = SessionComposition(unseen_count=min(count, len(mcqs)))
    return mcqs[:count], comp


def _build_random_session(
    conn: sqlite3.Connection,
    count: int,
    topic: str | None = None,
    chapter: str | None = None,
) -> tuple[list[dict], SessionComposition]:
    """Fallback: pure random selection (optionally filtered by topic/chapter)."""
    if topic or chapter:
        if topic and chapter:
            mcqs = [dict(r) for r in conn.execute(
                "SELECT * FROM question_bank WHERE topic_name = ? COLLATE NOCASE AND chapter_name = ? COLLATE NOCASE",
                (topic, chapter),
            ).fetchall()]
        elif topic:
            mcqs = [dict(r) for r in conn.execute(
                "SELECT * FROM question_bank WHERE topic_name = ? COLLATE NOCASE",
                (topic,),
            ).fetchall()]
        else:
            mcqs = [dict(r) for r in conn.execute(
                "SELECT * FROM question_bank WHERE chapter_name = ? COLLATE NOCASE",
                (chapter,),
            ).fetchall()]
        random.shuffle(mcqs)
    else:
        mcqs = [
            dict(r) for r in conn.execute(
                "SELECT * FROM question_bank ORDER BY RANDOM() LIMIT ?",
                (count,),
            ).fetchall()
        ]
    # Count origins
    qbank = sum(1 for m in mcqs if m['origin'] == 'question_bank')
    generated = sum(1 for m in mcqs if m['origin'] == 'generated')
    comp = SessionComposition(
        qbank_count=qbank,
        generated_count=generated,
        other_count=len(mcqs) - qbank - generated,
    )
    return mcqs[:count], comp


def get_topic_list(conn: sqlite3.Connection) -> list[dict]:
    """Return all topics with MCQ counts for topic selection UI."""
    rows = conn.execute("""
        SELECT topic_id, topic_name, chapter_name,
               COUNT(*) as mcq_count,
               COUNT(DISTINCT qb.chapter_id) as chapter_count
        FROM question_bank qb
        GROUP BY topic_id
        ORDER BY chapter_name, topic_name
    """).fetchall()
    return [dict(r) for r in rows]