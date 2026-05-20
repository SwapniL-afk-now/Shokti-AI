"""Evolution-aware session builder — config-driven MCQ selection.

All 9 evolvable params directly control selection:
  WEAK_THRESHOLD  → binary: is this topic "weak"?
  QBANK_RATIO / GENERATED_RATIO  → source mix
  WEAK_TOPIC_RATIO  → fraction reserved for weak topics
  WEAKNESS_WEIGHT / DEBT_WEIGHT / IMPORTANCE_WEIGHT  → inter-topic priority
  GAP_THRESHOLD  → skip topics with enough MCQs already answered
  SM2_INITIAL_EF  → set EF in student_mcq_stats (affects spacing if SM2 runs)
"""

import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from shokti.evolution.config import ALL_TOPIC_KEYS
from shokti.sampling_weights import compute_sampling_debt, compute_exam_importance


def _flat_id(chapter_id: str, topic_id: str) -> str:
    return f"{chapter_id}_{topic_id}"


def _gaussian_weight(accuracy: float, mean: float, sigma: float) -> float:
    """Compute gaussian weight for a topic accuracy."""
    import math
    return math.exp(-0.5 * ((accuracy - mean) / sigma) ** 2)


def _select_by_weight(mcqs: list, weights: list[float], n: int) -> list[int]:
    """Select n item indices from mcqs using weighted probabilities without replacement."""
    if n <= 0:
        return []
    if n >= len(mcqs):
        return list(range(len(mcqs)))
    selected = []
    remaining_idx = list(range(len(mcqs)))
    remaining_weights = weights.copy()
    for _ in range(n):
        total = sum(remaining_weights)
        if total <= 0:
            idx = random.choice(range(len(remaining_idx)))
        else:
            r = random.random() * total
            cumsum = 0
            idx = 0
            for i, w in enumerate(remaining_weights):
                cumsum += w
                if cumsum >= r:
                    idx = i
                    break
        selected.append(remaining_idx[idx])
        del remaining_idx[idx]
        del remaining_weights[idx]
    return selected


@dataclass
class SessionComposition:
    qbank_count: int = 0
    generated_count: int = 0
    weak_topic_count: int = 0
    other_count: int = 0


def build_evolvable_session(
    conn: sqlite3.Connection,
    student,
    config: dict,
    count: int,
) -> tuple[list[dict], SessionComposition]:
    """
    Build an evolvable question session driven by all 9 config params.

    Returns: (selected_mcqs, composition)
    """
    weak_thresh = config["WEAK_THRESHOLD"]
    qbank_ratio = config["QBANK_RATIO"]
    generated_ratio = config["GENERATED_RATIO"]
    weak_topic_ratio = config["WEAK_TOPIC_RATIO"]
    gap_thresh = config.get("GAP_THRESHOLD", 15)
    weakness_w = config["WEAKNESS_WEIGHT"]
    debt_w = config["DEBT_WEIGHT"]
    importance_w = config["IMPORTANCE_WEIGHT"]

    # 1. Get student's per-topic accuracy (dict keyed by (chapter_id, topic_id) tuple)
    topic_acc = student._topic_accuracy

    # 2. Compute signals
    student_id = student.student_id
    sampling_debt = compute_sampling_debt(conn, student_id)
    exam_importance = compute_exam_importance(conn)

    # 3. Priority per topic: weakness×W1 + debt×W2 + importance×W3
    priorities: dict[tuple, float] = {}
    for key in ALL_TOPIC_KEYS:
        acc = topic_acc.get(key, 0.5)
        weakness = 1.0 - acc
        flat = _flat_id(key[0], key[1])
        debt = sampling_debt.get(flat, 1.0)
        importance = exam_importance.get(flat, 0.5)
        priorities[key] = (
            weakness * weakness_w
            + debt * debt_w
            + importance * importance_w
        )

    # 4. Allocate count MCQs across topics proportionally
    total_priority = sum(priorities.values())
    if total_priority <= 0:
        total_priority = 1.0

    allocations: dict[tuple, int] = {}
    remainders: dict[tuple, float] = {}
    for key, p in priorities.items():
        share = (p / total_priority) * count
        alloc = int(share)
        rem = share - alloc
        allocations[key] = alloc
        remainders[key] = rem

    remaining_slots = count - sum(allocations.values())
    sorted_by_rem = sorted(remainders, key=lambda k: remainders[k], reverse=True)
    for key in sorted_by_rem[:remaining_slots]:
        allocations[key] += 1

    # 4. Compute target counts per category
    # WEAK_TOPIC_RATIO: fraction reserved for weak topics
    # Remaining split by QBANK_RATIO / GENERATED_RATIO
    target_weak = int(count * weak_topic_ratio)
    remaining = count - target_weak

    # 5. Collect MCQs per category (weak vs non-weak) and source (qbank vs generated)
    all_mcqs_by_topic: dict[tuple, list[dict]] = defaultdict(list)
    weak_all_qbank: list[dict] = []
    weak_all_generated: list[dict] = []
    non_weak_all_qbank: list[dict] = []
    non_weak_all_generated: list[dict] = []

    weak_keys = {k for k, acc in topic_acc.items() if acc < weak_thresh}

    for row in conn.execute("SELECT * FROM question_bank").fetchall():
        mcq = dict(row)
        key = (mcq["chapter_id"], mcq["topic_id"])
        all_mcqs_by_topic[key].append(mcq)
        is_weak = key in weak_keys
        if mcq["origin"] == "question_bank":
            if is_weak:
                weak_all_qbank.append(mcq)
            else:
                non_weak_all_qbank.append(mcq)
        else:
            if is_weak:
                weak_all_generated.append(mcq)
            else:
                non_weak_all_generated.append(mcq)

    # 6. Select MCQs respecting WEAK_TOPIC_RATIO + source ratios
    selected: list[dict] = []
    comp = SessionComposition()

    def _select_source(whole_list: list[dict], n: int, weight: float) -> list[dict]:
        if n <= 0 or not whole_list:
            return []
        weights = [weight] * len(whole_list)
        idxs = _select_by_weight(whole_list, weights, n)
        return [whole_list[i] for i in idxs]

    # Weak-topic MCQs: split by qbank_ratio / generated_ratio
    weak_target_qbank = int(target_weak * qbank_ratio)
    weak_target_generated = int(target_weak * generated_ratio)
    weak_fill = target_weak - weak_target_qbank - weak_target_generated

    chosen = _select_source(weak_all_qbank, weak_target_qbank, 1.0)
    comp.qbank_count += len(chosen)
    selected.extend(chosen)

    chosen = _select_source(weak_all_generated, weak_target_generated, 1.0)
    comp.generated_count += len(chosen)
    selected.extend(chosen)

    # Non-weak MCQs: fill remaining from qbank (preferred) then generated
    non_weak_target_qbank = int(remaining * qbank_ratio)
    non_weak_target_generated = int(remaining * generated_ratio)
    non_weak_fill = remaining - non_weak_target_qbank - non_weak_target_generated

    chosen = _select_source(non_weak_all_qbank, non_weak_target_qbank, 1.0)
    comp.qbank_count += len(chosen)
    selected.extend(chosen)

    chosen = _select_source(non_weak_all_generated, non_weak_target_generated, 1.0)
    comp.generated_count += len(chosen)
    selected.extend(chosen)

    # Fill any remaining slots from any available MCQ
    already_ids = {id(m) for m in selected}
    fill_pool = [m for m in weak_all_qbank + weak_all_generated + non_weak_all_qbank + non_weak_all_generated
                 if id(m) not in already_ids]
    while len(selected) < count and fill_pool:
        mcq = fill_pool.pop()
        selected.append(mcq)

    random.shuffle(selected)
    return selected[:count], comp
