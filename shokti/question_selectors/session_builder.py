"""Adaptive session builder — multi-peaked gaussian question selection."""

import random
import sqlite3
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass

from shokti.core.config import DB_PATH, MCQ
from shokti.question_selectors.weak_topic_tracker import (
    get_topic_stats,
    get_unseen_topics,
)

logger = logging.getLogger(__name__)


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
    fresh_generated_count: int = 0
    unseen_count: int = 0
    other_count: int = 0
    easy_count: int = 0
    medium_count: int = 0
    hard_count: int = 0


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


def _rebalance_by_difficulty(
    selected: list[dict],
    pool: list[dict],
    count: int,
    easy_ratio: float,
    medium_ratio: float,
    hard_ratio: float,
) -> list[dict]:
    """Rebalance selected MCQs toward target difficulty split by swapping with pool."""
    target_easy = int(count * easy_ratio)
    target_medium = int(count * medium_ratio)
    target_hard = count - target_easy - target_medium

    # Classify current selection
    easy_mcqs = [m for m in selected if m.get("difficulty") == "easy"]
    medium_mcqs = [m for m in selected if m.get("difficulty") == "medium"]
    hard_mcqs = [m for m in selected if m.get("difficulty") == "hard"]

    def needs_swap(current_list, target):
        return len(current_list) - target

    excess_easy = len(easy_mcqs) - target_easy
    excess_medium = len(medium_mcqs) - target_medium
    excess_hard = len(hard_mcqs) - target_hard

    # Build lookup for unselected pool by difficulty and topic
    unselected = [m for m in pool if m not in selected]
    unselected_by_diff = {"easy": [], "medium": [], "hard": []}
    for m in unselected:
        diff = m.get("difficulty", "medium")
        if diff in unselected_by_diff:
            unselected_by_diff[diff].append(m)

    def swap(excess_list, excess_count, needed_diff, needed_count):
        if excess_count <= 0 or needed_count <= 0:
            return
        # Swap: replace highest-weight item in excess_list with lowest-weight from needed pool
        # We use random pick since weights already determined at selection time
        to_remove = random.sample(excess_list, min(excess_count, len(excess_list)))
        to_add = random.sample(unselected_by_diff[needed_diff], min(needed_count, len(unselected_by_diff[needed_diff])))
        for m in to_remove:
            selected.remove(m)
        for m in to_add:
            selected.append(m)

    # Swap excess easy for needed hard/medium
    if excess_easy > 0:
        needed_hard = max(0, target_hard - len(hard_mcqs))
        needed_medium = max(0, target_medium - len(medium_mcqs))
        swap(easy_mcqs, excess_easy, "hard", min(excess_easy, needed_hard))
        swap(easy_mcqs, excess_easy, "medium", min(excess_easy, needed_medium))
    # Swap excess hard for needed easy/medium
    if excess_hard > 0:
        needed_easy = max(0, target_easy - len(easy_mcqs))
        needed_medium = max(0, target_medium - len(medium_mcqs))
        swap(hard_mcqs, excess_hard, "easy", min(excess_hard, needed_easy))
        swap(hard_mcqs, excess_hard, "medium", min(excess_hard, needed_medium))
    # Swap excess medium for needed easy/hard
    if excess_medium > 0:
        needed_easy = max(0, target_easy - len(easy_mcqs))
        needed_hard = max(0, target_hard - len(hard_mcqs))
        swap(medium_mcqs, excess_medium, "easy", min(excess_medium, needed_easy))
        swap(medium_mcqs, excess_medium, "hard", min(excess_medium, needed_hard))

    return selected
    if not stats or stats.attempts <= 0:
        return 1.0
    risk_count = (stats.confident_mistake_count * 1.25) + (stats.no_knowledge_count * 1.5)
    risk_rate = risk_count / stats.attempts
    return 1.0 + min(1.5, risk_rate)


def _normalized_practice_ratios() -> dict[str, float]:
    ratios = {
        "qbank": float(MCQ.QBANK_RATIO),
        "generated": float(MCQ.GENERATED_RATIO),
        "weak": float(MCQ.WEAK_TOPIC_RATIO),
        "fresh": float(getattr(MCQ, "FRESH_GENERATED_RATIO", 0.0)),
    }
    total = sum(max(0.0, value) for value in ratios.values())
    if total <= 0:
        logger.warning("Practice ratios sum to zero; using default 40/20/25/15 mix.")
        return {"qbank": 0.40, "generated": 0.20, "weak": 0.25, "fresh": 0.15}
    if abs(total - 1.0) > 0.001:
        logger.warning("Practice ratios sum to %.3f; normalizing instead of failing.", total)
    return {key: max(0.0, value) / total for key, value in ratios.items()}


def _ratio_targets(count: int, ratios: dict[str, float]) -> dict[str, int]:
    raw = {key: count * ratio for key, ratio in ratios.items()}
    targets = {key: int(value) for key, value in raw.items()}
    remaining = count - sum(targets.values())
    for key, _ in sorted(raw.items(), key=lambda item: item[1] - int(item[1]), reverse=True):
        if remaining <= 0:
            break
        targets[key] += 1
        remaining -= 1
    return targets


def _where_for_filters(origin: str | None = None, topic: str | None = None, chapter: str | None = None,
                       exclude_ids: set[int] | None = None) -> tuple[str, list]:
    clauses = []
    params = []
    if origin:
        clauses.append("origin = ?")
        params.append(origin)
    if topic:
        clauses.append("topic_name = ? COLLATE NOCASE")
        params.append(topic)
    if chapter:
        clauses.append("chapter_name = ? COLLATE NOCASE")
        params.append(chapter)
    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        clauses.append(f"id NOT IN ({placeholders})")
        params.extend(exclude_ids)
    return (" AND ".join(clauses) if clauses else "1=1"), params


def _fetch_mcqs(conn: sqlite3.Connection, origin: str | None = None, topic: str | None = None,
                chapter: str | None = None, exclude_ids: set[int] | None = None,
                limit: int | None = None, random_order: bool = False) -> list[dict]:
    where_clause, params = _where_for_filters(origin, topic, chapter, exclude_ids)
    order = " ORDER BY RANDOM()" if random_order else ""
    limit_clause = " LIMIT ?" if limit is not None else ""
    if limit is not None:
        params.append(limit)
    rows = conn.execute(f"SELECT * FROM question_bank WHERE {where_clause}{order}{limit_clause}", params).fetchall()
    return [dict(r) for r in rows]


def _topic_inventory_row(conn: sqlite3.Connection, topic: str | None, chapter: str | None,
                         topic_key: tuple[str, str] | None = None) -> dict | None:
    if topic_key:
        row = conn.execute("""
            SELECT subject, book_id, chapter_id, chapter_name, book_page_range, source_file,
                   topic_id, topic_name, COUNT(*) AS mcq_count
            FROM question_bank
            WHERE chapter_id = ? AND topic_id = ?
            GROUP BY chapter_id, topic_id
            LIMIT 1
        """, topic_key).fetchone()
    else:
        where_clause, params = _where_for_filters(topic=topic, chapter=chapter)
        row = conn.execute(f"""
            SELECT subject, book_id, chapter_id, chapter_name, book_page_range, source_file,
                   topic_id, topic_name, COUNT(*) AS mcq_count
            FROM question_bank
            WHERE {where_clause}
            GROUP BY chapter_id, topic_id
            ORDER BY COUNT(*) ASC, topic_name
            LIMIT 1
        """, params).fetchone()
    return dict(row) if row else None


def _select_fresh_topic(
    conn: sqlite3.Connection,
    student_id: str,
    mode: str,
    topic: str | None,
    chapter: str | None,
    topic_stats_list,
    unseen_topic_keys: set[tuple[str, str]],
) -> tuple[dict | None, str]:
    explicit = _topic_inventory_row(conn, topic, chapter) if (topic or chapter) else None
    if explicit:
        return explicit, "explicit filter"

    if mode == "weakness" and topic_stats_list:
        ranked = sorted(
            topic_stats_list,
            key=lambda s: ((1.0 - s.accuracy) * _confidence_risk_multiplier(s), s.attempts),
            reverse=True,
        )
        row = _topic_inventory_row(conn, None, None, ranked[0].topic_key)
        if row:
            return row, "weak topic/confidence risk"

    if mode == "coverage" and unseen_topic_keys:
        row = _topic_inventory_row(conn, None, None, sorted(unseen_topic_keys)[0])
        if row:
            return row, "coverage gap"

    candidates = []
    for stats in topic_stats_list:
        candidates.append((
            (1.0 - stats.accuracy) * _confidence_risk_multiplier(stats),
            "weak topic/confidence risk",
            stats.topic_key,
        ))
    for key in unseen_topic_keys:
        candidates.append((1.1, "under-sampled topic", key))
    for _, reason, key in sorted(candidates, reverse=True):
        row = _topic_inventory_row(conn, None, None, key)
        if row:
            return row, reason

    row = _topic_inventory_row(conn, None, None)
    return row, "under-covered DB inventory"


def _example_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "chapter": row.get("chapter_name"),
        "topic": row.get("topic_name"),
        "question": row.get("question"),
        "options": row.get("options"),
        "correct_answer": row.get("correct_answer"),
        "difficulty": row.get("difficulty"),
    }


def _practice_generation_context(
    conn: sqlite3.Connection,
    student_id: str,
    mode: str,
    topic: str | None,
    chapter: str | None,
    target_topic: dict,
    target_reason: str,
    target_count: int,
    ratios: dict[str, float],
    topic_stats_list,
) -> dict:
    avg_rows = conn.execute("""
        SELECT qb.chapter_id, qb.topic_id, AVG(sal.time_spent_seconds) AS avg_time_seconds
        FROM student_answer_log sal
        JOIN question_bank qb ON qb.id = sal.mcq_id
        WHERE sal.student_id = ? AND sal.time_spent_seconds IS NOT NULL
        GROUP BY qb.chapter_id, qb.topic_id
    """, (student_id,)).fetchall()
    avg_time_by_topic = {
        (row["chapter_id"], row["topic_id"]): row["avg_time_seconds"]
        for row in avg_rows
    }
    topic_stats = []
    for stats in topic_stats_list[:20]:
        topic_stats.append({
            "chapter_id": stats.topic_key[0],
            "topic_id": stats.topic_id,
            "topic_name": stats.topic_name,
            "accuracy": stats.accuracy,
            "attempts": stats.attempts,
            "wrong_count": stats.wrong_count,
            "avg_time_seconds": avg_time_by_topic.get(stats.topic_key),
            "confident_mistakes": stats.confident_mistake_count,
            "no_knowledge_count": stats.no_knowledge_count,
        })

    qbank_examples = [
        _example_row(r) for r in _fetch_mcqs(
            conn,
            origin="question_bank",
            topic=target_topic.get("topic_name"),
            chapter=target_topic.get("chapter_name"),
            limit=5,
            random_order=True,
        )
    ]
    generated_examples = [
        _example_row(r) for r in _fetch_mcqs(
            conn,
            origin="generated",
            topic=target_topic.get("topic_name"),
            chapter=target_topic.get("chapter_name"),
            limit=5,
            random_order=True,
        )
    ]
    weak_rows = conn.execute("""
        SELECT qb.id, qb.chapter_name, qb.topic_name, qb.question, qb.options, qb.correct_answer,
               qb.difficulty, sal.selected_option, sal.time_spent_seconds, sal.confidence_rating
        FROM student_answer_log sal
        JOIN question_bank qb ON qb.id = sal.mcq_id
        WHERE sal.student_id = ?
          AND sal.is_correct = 0
          AND qb.chapter_id = ?
          AND qb.topic_id = ?
        ORDER BY sal.answered_at DESC
        LIMIT 5
    """, (student_id, target_topic.get("chapter_id"), target_topic.get("topic_id"))).fetchall()
    weak_examples = []
    for row in weak_rows:
        item = _example_row(dict(row))
        item.update({
            "selected_option": row["selected_option"],
            "time_spent_seconds": row["time_spent_seconds"],
            "confidence_rating": row["confidence_rating"],
        })
        weak_examples.append(item)

    coverage_rows = conn.execute("""
        SELECT chapter_id, chapter_name, topic_id, topic_name, COUNT(*) AS mcq_count
        FROM question_bank
        GROUP BY chapter_id, topic_id
        ORDER BY mcq_count ASC, topic_name
        LIMIT 5
    """).fetchall()

    return {
        "target_session_mode": mode,
        "target_bucket": "fresh_generated",
        "selected_filters": {"topic": topic, "chapter": chapter},
        "target_topic_reason": target_reason,
        "requested_mix_percentages": {key: round(value, 4) for key, value in ratios.items()},
        "target_count": target_count,
        "desired_difficulty_split": {
            "easy": MCQ.DIFFICULTY_EASY_RATIO,
            "medium": MCQ.DIFFICULTY_MEDIUM_RATIO,
            "hard": MCQ.DIFFICULTY_HARD_RATIO,
        },
        "target_topic": {
            "chapter_id": target_topic.get("chapter_id"),
            "chapter_name": target_topic.get("chapter_name"),
            "topic_id": target_topic.get("topic_id"),
            "topic_name": target_topic.get("topic_name"),
        },
        "student_topic_stats": topic_stats,
        "nearby_qbank_examples": qbank_examples,
        "nearby_stored_generated_examples": generated_examples,
        "weak_confidence_risk_examples": weak_examples,
        "coverage_gap_context": [dict(r) for r in coverage_rows],
        "file_search_instructions": [
            "use retrieved textbook/source content as ground truth",
            "do not duplicate provided examples",
            "match style and difficulty distribution",
            "diagnose the target weakness or coverage gap",
            "return valid JSON only",
        ],
    }


def _generate_fresh_for_session(
    conn: sqlite3.Connection,
    student_id: str,
    mode: str,
    topic: str | None,
    chapter: str | None,
    count: int,
    ratios: dict[str, float],
    topic_stats_list,
    unseen_topic_keys: set[tuple[str, str]],
) -> list[dict]:
    if count <= 0 or not getattr(MCQ, "ENABLE_FRESH_GENERATION_IN_PRACTICE", True):
        return []

    target_topic, reason = _select_fresh_topic(
        conn, student_id, mode, topic, chapter, topic_stats_list, unseen_topic_keys
    )
    if not target_topic:
        return []

    context = _practice_generation_context(
        conn, student_id, mode, topic, chapter, target_topic, reason, count, ratios, topic_stats_list
    )
    max_id_row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM question_bank").fetchone()
    max_existing_id = max_id_row[0] if max_id_row else 0

    def _worker() -> int:
        from shokti.generators.gap_filler import generate_fresh_mcqs, setup_generator

        worker_conn = sqlite3.connect(DB_PATH)
        worker_conn.row_factory = sqlite3.Row
        try:
            client, store_name, gen_config, cite_config = setup_generator()
            return generate_fresh_mcqs(
                topic_name=target_topic["topic_name"],
                chapter_id=target_topic["chapter_id"],
                chapter_name=target_topic["chapter_name"],
                book_page_range=target_topic.get("book_page_range") or "",
                source_file=target_topic.get("source_file") or "",
                count=count,
                conn=worker_conn,
                client=client,
                store_name=store_name,
                gen_config=gen_config,
                cite_config=cite_config,
                practice_context=context,
                allow_existing_fallback=False,
            )
        finally:
            worker_conn.close()

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_worker)
        inserted = future.result(timeout=getattr(MCQ, "FRESH_GENERATION_MAX_WAIT_SECONDS", 20))
        executor.shutdown(wait=False, cancel_futures=True)
    except TimeoutError:
        executor.shutdown(wait=False, cancel_futures=True)
        logger.warning("Fresh MCQ generation timed out for practice session.")
        return []
    except Exception as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        logger.warning("Fresh MCQ generation failed for practice session: %s", exc)
        return []

    if inserted <= 0:
        return []

    where_clause, params = _where_for_filters(
        origin="generated",
        topic=target_topic.get("topic_name"),
        chapter=target_topic.get("chapter_name"),
    )
    where_clause = f"{where_clause} AND id > ?"
    params.append(max_existing_id)
    params.append(count)
    return [
        dict(r) for r in conn.execute(
            f"SELECT * FROM question_bank WHERE {where_clause} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    ]


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
    ratios = _normalized_practice_ratios()
    targets = _ratio_targets(count, ratios)
    target_qbank = targets["qbank"]
    target_generated = targets["generated"]
    target_weak = targets["weak"]
    target_fresh = targets["fresh"]
    target_unseen = max(0, count - target_qbank - target_generated - target_weak - target_fresh)

    # Compute topic stats once — derive weak + seen from the same result
    topic_stats_list = get_topic_stats(conn, student_id)
    topic_stats = {t.topic_key: t for t in topic_stats_list}
    weak_topic_keys = {t.topic_key for t in topic_stats_list if t.accuracy < MCQ.WEAK_THRESHOLD}
    unseen_topic_keys = set(get_unseen_topics(conn, student_id))
    fresh_mcqs = _generate_fresh_for_session(
        conn,
        student_id,
        "adaptive",
        topic,
        chapter,
        target_fresh,
        ratios,
        topic_stats_list,
        unseen_topic_keys,
    )
    fresh_ids = {m["id"] for m in fresh_mcqs}

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

    generated_mcqs = [m for m in generated_mcqs if m["id"] not in fresh_ids]

    all_mcqs = qbank_mcqs + generated_mcqs

    selected = []
    comp = SessionComposition()

    # 0. Fresh Gemini-generated MCQs are practice-only and stored before selection.
    if fresh_mcqs:
        for mcq in fresh_mcqs[:target_fresh]:
            selected.append(mcq)
            comp.fresh_generated_count += 1
    if comp.fresh_generated_count < target_fresh:
        target_generated += target_fresh - comp.fresh_generated_count

    # 1. Pull from qbank — moderate weight toward weak topics
    qbank_weights = [
        (1.0 if (m['chapter_id'], m['topic_id']) in weak_topic_keys else 0.4)
        * _confidence_risk_multiplier(topic_stats.get((m['chapter_id'], m['topic_id'])))
        for m in qbank_mcqs
    ]
    qbank_selected_idx = _select_by_weight(qbank_mcqs, qbank_weights, min(target_qbank, len(qbank_mcqs)))
    for i in qbank_selected_idx:
        selected.append(qbank_mcqs[i])
        comp.qbank_count += 1

    # 2. Pull from generated — moderate weight toward weak topics
    generated_weights = [
        (1.5 if (m['chapter_id'], m['topic_id']) in weak_topic_keys else 0.5)
        * _confidence_risk_multiplier(topic_stats.get((m['chapter_id'], m['topic_id'])))
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
        weak_weights_list[idx_m] *= _confidence_risk_multiplier(stats)

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

    # Rebalance toward target difficulty split
    selected = _rebalance_by_difficulty(
        selected, all_mcqs, count,
        MCQ.DIFFICULTY_EASY_RATIO,
        MCQ.DIFFICULTY_MEDIUM_RATIO,
        MCQ.DIFFICULTY_HARD_RATIO,
    )

    # Shuffle to avoid predictable ordering
    random.shuffle(selected)

    # Count difficulty distribution for composition
    for m in selected:
        diff = m.get("difficulty", "medium")
        if diff == "easy":
            comp.easy_count += 1
        elif diff == "medium":
            comp.medium_count += 1
        else:
            comp.hard_count += 1

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
    ratios = _normalized_practice_ratios()
    fresh_target = _ratio_targets(count, ratios)["fresh"]
    unseen_topic_keys = set(get_unseen_topics(conn, student_id))
    fresh_mcqs = _generate_fresh_for_session(
        conn, student_id, "weakness", topic, chapter, fresh_target, ratios, topic_stats_list, unseen_topic_keys
    )
    fresh_ids = {m["id"] for m in fresh_mcqs}
    if not weak_keys:
        random_mcqs, comp = _build_random_session(conn, max(0, count - len(fresh_mcqs)), topic=topic, chapter=chapter)
        selected = fresh_mcqs[:fresh_target] + [m for m in random_mcqs if m["id"] not in fresh_ids]
        comp.fresh_generated_count = min(fresh_target, len(fresh_mcqs))
        return selected[:count], comp

    weak_clause = " OR ".join([f"(chapter_id = ? AND topic_id = ?)" for _ in weak_keys])
    if not weak_clause:
        return _build_random_session(conn, count, topic=topic, chapter=chapter)
    params = [p for pair in weak_keys for p in pair]
    mcqs = [dict(r) for r in conn.execute(
        f"SELECT * FROM question_bank WHERE {weak_clause}", params
    ).fetchall()]
    if topic or chapter:
        mcqs = _filter_mcqs(mcqs, topic, chapter)
    mcqs = [m for m in mcqs if m["id"] not in fresh_ids]

    topic_stats = {t.topic_key: t for t in topic_stats_list}
    weighted = []
    for m in mcqs:
        stats = topic_stats.get((m['chapter_id'], m['topic_id']))
        weighted.append((random.random() / _confidence_risk_multiplier(stats), m))
    weighted.sort(key=lambda item: item[0])
    mcqs = [m for _, m in weighted]
    selected = fresh_mcqs[:fresh_target] + mcqs[:max(0, count - min(fresh_target, len(fresh_mcqs)))]
    selected = _rebalance_by_difficulty(
        selected, selected, count,
        MCQ.DIFFICULTY_EASY_RATIO,
        MCQ.DIFFICULTY_MEDIUM_RATIO,
        MCQ.DIFFICULTY_HARD_RATIO,
    )
    comp = SessionComposition(
        weak_topic_count=min(max(0, count - min(fresh_target, len(fresh_mcqs))), len(mcqs)),
        fresh_generated_count=min(fresh_target, len(fresh_mcqs)),
    )
    for m in selected:
        diff = m.get("difficulty", "medium")
        if diff == "easy":
            comp.easy_count += 1
        elif diff == "medium":
            comp.medium_count += 1
        else:
            comp.hard_count += 1
    return selected[:count], comp


def _build_coverage_session(
    conn: sqlite3.Connection,
    student_id: str,
    count: int,
    topic: str | None = None,
    chapter: str | None = None,
) -> tuple[list[dict], SessionComposition]:
    """Focus on unseen topics."""
    topic_stats_list = get_topic_stats(conn, student_id)
    ratios = _normalized_practice_ratios()
    fresh_target = _ratio_targets(count, ratios)["fresh"]
    unseen_keys = get_unseen_topics(conn, student_id)
    fresh_mcqs = _generate_fresh_for_session(
        conn, student_id, "coverage", topic, chapter, fresh_target, ratios, topic_stats_list, set(unseen_keys)
    )
    fresh_ids = {m["id"] for m in fresh_mcqs}
    if not unseen_keys:
        random_mcqs, comp = _build_random_session(conn, max(0, count - len(fresh_mcqs)), topic=topic, chapter=chapter)
        selected = fresh_mcqs[:fresh_target] + [m for m in random_mcqs if m["id"] not in fresh_ids]
        comp.fresh_generated_count = min(fresh_target, len(fresh_mcqs))
        return selected[:count], comp

    unseen_clause = " OR ".join([f"(chapter_id = ? AND topic_id = ?)" for _ in unseen_keys])
    params = [p for pair in unseen_keys for p in pair]
    mcqs = [dict(r) for r in conn.execute(
        f"SELECT * FROM question_bank WHERE {unseen_clause}", params
    ).fetchall()]
    if topic or chapter:
        mcqs = _filter_mcqs(mcqs, topic, chapter)
    mcqs = [m for m in mcqs if m["id"] not in fresh_ids]

    random.shuffle(mcqs)
    selected = fresh_mcqs[:fresh_target] + mcqs[:max(0, count - min(fresh_target, len(fresh_mcqs)))]
    selected = _rebalance_by_difficulty(
        selected, selected, count,
        MCQ.DIFFICULTY_EASY_RATIO,
        MCQ.DIFFICULTY_MEDIUM_RATIO,
        MCQ.DIFFICULTY_HARD_RATIO,
    )
    comp = SessionComposition(
        unseen_count=min(max(0, count - min(fresh_target, len(fresh_mcqs))), len(mcqs)),
        fresh_generated_count=min(fresh_target, len(fresh_mcqs)),
    )
    for m in selected:
        diff = m.get("difficulty", "medium")
        if diff == "easy":
            comp.easy_count += 1
        elif diff == "medium":
            comp.medium_count += 1
        else:
            comp.hard_count += 1
    return selected[:count], comp


def _build_random_session(
    conn: sqlite3.Connection,
    count: int,
    topic: str | None = None,
    chapter: str | None = None,
) -> tuple[list[dict], SessionComposition]:
    """Fallback: pure random selection (optionally filtered by topic/chapter)."""
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
    elif chapter:
        mcqs = [dict(r) for r in conn.execute(
            "SELECT * FROM question_bank WHERE chapter_name = ? COLLATE NOCASE",
            (chapter,),
        ).fetchall()]
    else:
        mcqs = [
            dict(r) for r in conn.execute(
                "SELECT * FROM question_bank ORDER BY RANDOM() LIMIT ?",
                (count,),
            ).fetchall()
        ]
    random.shuffle(mcqs)
    mcqs = _rebalance_by_difficulty(
        mcqs, mcqs, count,
        MCQ.DIFFICULTY_EASY_RATIO,
        MCQ.DIFFICULTY_MEDIUM_RATIO,
        MCQ.DIFFICULTY_HARD_RATIO,
    )
    qbank = sum(1 for m in mcqs if m['origin'] == 'question_bank')
    generated = sum(1 for m in mcqs if m['origin'] == 'generated')
    easy = sum(1 for m in mcqs if m.get('difficulty') == 'easy')
    medium = sum(1 for m in mcqs if m.get('difficulty') == 'medium')
    hard = sum(1 for m in mcqs if m.get('difficulty') == 'hard')
    comp = SessionComposition(
        qbank_count=qbank,
        generated_count=generated,
        other_count=len(mcqs) - qbank - generated,
        easy_count=easy,
        medium_count=medium,
        hard_count=hard,
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
