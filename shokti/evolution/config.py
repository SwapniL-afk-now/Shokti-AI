"""Evolution settings, param search spaces, and student generator."""

from dataclasses import dataclass
import random


@dataclass
class ParamSpace:
    min: float
    max: float
    step: float


PARAM_SPACES: dict[str, ParamSpace] = {
    "WEAK_THRESHOLD":    ParamSpace(0.20, 0.80, 0.05),
    "QBANK_RATIO":       ParamSpace(0.20, 0.70, 0.05),
    "GENERATED_RATIO":   ParamSpace(0.00, 0.50, 0.05),
    "WEAK_TOPIC_RATIO":  ParamSpace(0.00, 0.50, 0.05),
    "GAP_THRESHOLD":      ParamSpace(5,   30,   1),
    "WEAKNESS_WEIGHT":    ParamSpace(0.10, 0.70, 0.05),
    "DEBT_WEIGHT":        ParamSpace(0.10, 0.70, 0.05),
    "IMPORTANCE_WEIGHT": ParamSpace(0.00, 0.50, 0.05),
    "SM2_INITIAL_EF":     ParamSpace(1.30, 3.00, 0.10),
}

PARAM_CLASS_MAP: dict[str, str] = {
    "WEAK_THRESHOLD":    "MCQ",
    "QBANK_RATIO":       "MCQ",
    "GENERATED_RATIO":   "MCQ",
    "WEAK_TOPIC_RATIO":  "MCQ",
    "GAP_THRESHOLD":     "MCQ",
    "WEAKNESS_WEIGHT":   "SAMPLING",
    "DEBT_WEIGHT":       "SAMPLING",
    "IMPORTANCE_WEIGHT": "SAMPLING",
    "SM2_INITIAL_EF":    "MCQ",
}

CYCLES_DEFAULT = 100
STUDENTS_PER_CYCLE = 5
ELITE_SIZE = 5
STAGNATION_LIMIT = 15
RESTART_INTERVAL = 30
MUTATION_PARAM_MIN = 1
MUTATION_PARAM_MAX = 2
DAYS_PER_CYCLE = 15
EXAMS_PER_DAY = 3
MCQS_PER_EXAM = 15
CHECKIN_COUNT = 10

ALL_TOPIC_NAMES = [
    "ব্রায়োফাইটা", "Riccia", "টেরিডোফাইটা", "Pteris",
    "টিস্যু ও ভাজক টিস্যু", "স্থায়ী টিস্যু", "আবরণী টিস্যু",
    "গ্রাউন্ড টিস্যুতন্ত্র", "ভাস্কুলার বান্ডল", "উদ্ভিদের মূল ও কাণ্ডের অন্তর্গঠন",
]

ALL_TOPIC_KEYS = [
    ("06", "1"), ("06", "2"), ("06", "3"), ("06", "4"),
    ("08", "1"), ("08", "2"), ("08", "3"), ("08", "4"), ("08", "5"), ("08", "6"),
]

BASE_RANGE = (0.40, 0.85)
VARIANCE_RANGE = (0.05, 0.30)
CLAMP_RANGE = (0.20, 0.98)


def generate_student(rng: random.Random, idx: int) -> dict:
    base = rng.uniform(BASE_RANGE[0], BASE_RANGE[1])
    variance = rng.uniform(VARIANCE_RANGE[0], VARIANCE_RANGE[1])
    seed = rng.randint(0, 10**9)

    topic_acc = {}
    for key in ALL_TOPIC_KEYS:
        noise = rng.gauss(0, variance)
        acc = base + noise
        acc = max(CLAMP_RANGE[0], min(CLAMP_RANGE[1], acc))
        acc = round(acc, 2)
        topic_acc[key] = acc

    return {
        "name": f"rand_{seed}_{idx}",
        "seed": seed,
        "topic_accuracy": topic_acc,
    }


def validate_config(config: dict) -> str | None:
    total_ratio = (
        config.get("QBANK_RATIO", 0) +
        config.get("GENERATED_RATIO", 0) +
        config.get("WEAK_TOPIC_RATIO", 0)
    )
    if total_ratio > 1.0:
        return f"ratio sum {total_ratio:.2f} > 1.0"

    weight_sum = (
        config.get("WEAKNESS_WEIGHT", 0) +
        config.get("DEBT_WEIGHT", 0) +
        config.get("IMPORTANCE_WEIGHT", 0)
    )
    if abs(weight_sum - 1.0) > 0.02:
        return f"weight sum {weight_sum:.2f} != 1.0"

    ef = config.get("SM2_INITIAL_EF", 2.5)
    if not (1.3 <= ef <= 3.0):
        return f"SM2_INITIAL_EF {ef} outside [1.3, 3.0]"

    wt = config.get("WEAK_THRESHOLD", 0.5)
    if not (0 < wt < 1):
        return f"WEAK_THRESHOLD {wt} not in (0, 1)"

    return None