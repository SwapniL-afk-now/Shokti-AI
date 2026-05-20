# Shokti Evolution — Plan B

## Overview

Automated config-parameter optimizer for Shokti's adaptive practice system. Uses **hill climbing with elite retention and random restart** — zero API calls, zero cost, runs entirely on your machine.

**Core idea**: For each candidate config (9 params), simulate 5 random students taking 3 exams + 10 adaptive MCQs, measure the improvement Δ = adaptive_accuracy − exam_accuracy, then mutate toward higher Δ. Repeat 100 cycles, deploy the best.

---

## Architecture

```
shokti/evolution/
├── __init__.py          # package marker
├── config.py            # evolution settings + param search spaces + student generator
├── models.py            # dataclasses + evolution_log table
├── simulator.py         # SimulatedStudent (deterministic, no input())
├── benchmark.py         # ShoktiBenchmark.measure(config) → FitnessResult
├── optimizer.py         # ConfigOptimizer (mutation + elite retention + random restart)
├── engine.py            # EvolutionEngine.run() — the main loop
├── apply_config.py      # read/write config.py; snapshot/restore
├── report.py            # print_table, print_summary, print_history
└── run.py               # CLI entrypoints: run, status, history, deploy
```

Plus one modification to an existing file:

```
shokti/core/config.py     # add SM2_INITIAL_EF to MCQConfig
shokti/adaptive_practice.py  # import SM2_INITIAL_EF from config
shokti/spaced_repetition.py  # import SM2_INITIAL_EF from config
```

---

## Data flow per cycle

```
engine.py
  │
  ├─ optimizer.propose(cycle) → candidate_config (dict of 9 params)
  │
  ├─ benchmark.measure(candidate_config) → FitnessResult
  │     │
  │     ├─ snapshot = apply_config.snapshot_config()
  │     ├─ apply_config.apply(candidate)          ← write to config.py
  │     │
  │     ├─ for each of 5 randomly generated students:
  │     │     sim = SimulatedStudent(unique_id, profile, conn)
  │     │     sim.run_exams(["1", "2", "3"])      ← INSERT student_answer_log
  │     │     sim.run_adaptive(count=10)           ← calls build_session() + INSERT
  │     │     measure exam_acc, adaptive_acc       ← SELECT from student_answer_log
  │     │     sim.cleanup()                        ← DELETE all rows for this student
  │     │
  │     ├─ apply_config.restore(snapshot)          ← revert config.py
  │     └─ return FitnessResult
  │
  ├─ optimizer.update(fitness) → accepted: bool
  │
  └─ models.log_cycle(conn, cycle, config, fitness_before, fitness_after, accepted)
       → INSERT INTO evolution_log
```

---

## FILE 1: `shokti/evolution/__init__.py`

```python
"""Evolution optimizer for adaptive practice parameters."""

from shokti.evolution.run import run_evolution, status, history, deploy
```

---

## FILE 2: `shokti/evolution/config.py`

### ParamSpace definitions (the 9 evolvable knobs)

```python
from dataclasses import dataclass, field


@dataclass
class ParamSpace:
    min: float
    max: float
    step: float


# Evolvable parameters mapped by their exact field name in shokti.core.config:
PARAM_SPACES: dict[str, ParamSpace] = {
    # From MCQConfig:
    "WEAK_THRESHOLD":     ParamSpace(0.20, 0.80, 0.05),
    "QBANK_RATIO":        ParamSpace(0.20, 0.70, 0.05),
    "GENERATED_RATIO":    ParamSpace(0.00, 0.50, 0.05),
    "WEAK_TOPIC_RATIO":   ParamSpace(0.00, 0.50, 0.05),
    "GAP_THRESHOLD":      ParamSpace(5,    30,   1),

    # From SamplingConfig:
    "WEAKNESS_WEIGHT":    ParamSpace(0.10, 0.70, 0.05),
    "DEBT_WEIGHT":        ParamSpace(0.10, 0.70, 0.05),
    "IMPORTANCE_WEIGHT":  ParamSpace(0.00, 0.50, 0.05),

    # To be added to MCQConfig:
    "SM2_INITIAL_EF":     ParamSpace(1.30, 3.00, 0.10),
}

# Group by config class for apply_config.py:
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
```

### Evolution algorithm settings

```python
CYCLES_DEFAULT = 100
STUDENTS_PER_CYCLE = 5
ELITE_SIZE = 5
STAGNATION_LIMIT = 15
RESTART_INTERVAL = 30
MUTATION_PARAM_MIN = 1
MUTATION_PARAM_MAX = 2

# Random student generation
ALL_TOPIC_NAMES = [
    "ব্রায়োফাইটা", "Riccia", "টেরিডোফাইটা", "Pteris",
    "টিস্যু ও ভাজক টিস্যু", "স্থায়ী টিস্যু", "আবরণী টিস্যু",
    "গ্রাউন্ড টিস্যুতন্ত্র", "ভাস্কুলার বান্ডল", "উদ্ভিদের মূল ও কাণ্ডের অন্তর্গঠন",
]

BASE_RANGE = (0.20, 0.85)       # overall ability range [min, max]
VARIANCE_RANGE = (0.05, 0.40)   # per-topic deviation from base
CLAMP_RANGE = (0.05, 0.98)      # min/max per-topic accuracy
```

### Student generator function

```python
import random
import math


def generate_student(rng: random.Random, idx: int) -> dict:
    """Return a random student profile dict.

    rng  — deterministic Random instance (seeded per cycle)
    idx  — index within the cycle batch, for unique naming

    Algorithm:
        base = uniform(BASE_RANGE[0], BASE_RANGE[1])
        variance = uniform(VARIANCE_RANGE[0], VARIANCE_RANGE[1])
        seed = rng.randint(0, 10**9)

        topic_acc = {}
        for topic in ALL_TOPIC_NAMES:
            noise = rng.gauss(0, variance)
            acc = base + noise
            acc = max(CLAMP_RANGE[0], min(CLAMP_RANGE[1], acc))
            acc = round(acc, 2)
            topic_acc[topic] = acc

        return {
            "name": f"rand_{seed}_{idx}",
            "seed": seed,
            "topic_accuracy": topic_acc,
        }
"""
```

This generates every archetype automatically:

| base ≈ | variance ≈ | Topics | Archetype |
|--------|-----------|--------|-----------|
| 0.30 | 0.10 | uniformly weak | weak student |
| 0.75 | 0.10 | uniformly strong | strong student |
| 0.50 | 0.35 | mix of weak/strong | spiky student |
| 0.80 | 0.05 | all strong, few weak | near-ceiling student |
| 0.40 | 0.30 | highly variable | realistic student |

---

## FILE 3: `shokti/evolution/models.py`

### Dataclasses

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class FitnessResult:
    config: dict                       # {param_name: value}
    fitness: float                     # mean(rel_deltas) across STUDENTS_PER_CYCLE
    fitness_std: float                 # std(rel_deltas)
    deltas: list[float]               # per-student Δ_rel values (relative improvement)
    exam_accuracies: list[float]     # per-student mean exam accuracy
    adaptive_accuracies: list[float] # per-student adaptive accuracy
    raw_deltas: list[float]           # per-student raw Δ = a_acc − e_acc
    student_ids: list[str]            # per-student IDs
    duration: float                   # wall-clock seconds
    timestamp: str                    # ISO datetime


@dataclass
class CycleResult:
    cycle: int
    config: dict
    fitness_before: float
    fitness_after: float
    delta: float
    accepted: bool
    duration: float
    timestamp: str
    n_students: int
```

### evolution_log table

```sql
CREATE TABLE IF NOT EXISTS evolution_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle            INTEGER NOT NULL,
    config_snapshot  TEXT NOT NULL,
    fitness_before   REAL,
    fitness_after    REAL,
    delta            REAL,
    accepted         BOOLEAN NOT NULL DEFAULT 0,
    duration_seconds REAL,
    n_students       INTEGER DEFAULT 5,
    timestamp        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Functions

```python
def create_evolution_table(conn):
    """CREATE TABLE IF NOT EXISTS evolution_log."""

def log_cycle(conn, result: CycleResult):
    """INSERT INTO evolution_log."""

def get_best_config(conn) -> dict | None:
    """Return config_snapshot as dict from row with MAX(fitness_after)."""

def get_best_fitness(conn) -> float:
    """SELECT MAX(fitness_after). Returns -inf if no rows."""

def get_last_config(conn) -> dict | None:
    """Config from most recent cycle."""

def get_last_fitness(conn) -> float | None:
    """Fitness from most recent cycle."""

def get_accepted_ratio(conn) -> float:
    """SUM(accepted)*1.0/COUNT(*)."""

def get_total_cycles(conn) -> int:
    """COUNT(*)."""

def get_recent_cycles(conn, limit=20) -> list[CycleResult]:
    """SELECT ... ORDER BY id DESC LIMIT ?."""
```

---

## FILE 4: `shokti/evolution/simulator.py`

### Internal helpers

```python
def _get_correct_option(mcq: dict) -> str:
    """Extract correct option letter from MCQ dict.
    Handles both dict and JSON string correct_answer fields.
    """
    ca = mcq.get("correct_answer", {})
    if isinstance(ca, str):
        import json
        ca = json.loads(ca)
    if isinstance(ca, dict):
        return ca.get("option", "A")
    return "A"


def _wrong_option(correct: str) -> str:
    """Deterministic wrong option. Rotates: A→B, B→C, C→D, D→A."""
    return {"A": "B", "B": "C", "C": "D", "D": "A"}[correct]
```

### SimulatedStudent class

```python
class SimulatedStudent:
    """Deterministic fake student. No input() calls."""

    def __init__(self, student_id: str, profile: dict,
                 conn: sqlite3.Connection):
        """
        student_id  — unique string (e.g., "evo_cycle3_2")
        profile     — dict: {name, seed, topic_accuracy: {topic_name: prob}}
        conn        — sqlite3 Row connection to question_bank.db
        """
        assert isinstance(student_id, str) and student_id
        assert "seed" in profile
        assert "topic_accuracy" in profile
        self.student_id = student_id
        self.profile = profile
        self.conn = conn

    def _decide_answer(self, mcq_id: int, topic_name: str,
                       correct_option: str) -> tuple[str, bool]:
        """Deterministically pick answer for one MCQ.

        Algorithm:
            1. p = profile.topic_accuracy.get(topic_name, 0.5)
            2. h = (self.profile["seed"] * 1000003 + mcq_id * 7) % 10000
            3. is_correct = (h / 10000) < p
            4. If correct: return (correct_option, True)
            5. If wrong: return (wrong_option(correct_option), False)

        Guarantees: same seed + same mcq_id → same answer always.
        """
        ...

    def run_exams(self, exam_ids: list[str]) -> dict[str, dict]:
        """Take all 3 fixed exams.

        For each exam_id:
            1. Load exam JSON from shokti.exams.exam_config
            2. For each mcq_id:
                 a. Query topic_name FROM question_bank
                 b. Query correct_answer FROM question_bank
                 c. Decide answer via _decide_answer()
                 d. INSERT INTO student_answer_log
            3. For each topic in exam:
                 a. Query topic_id FROM question_bank WHERE topic_name=?
                 b. INSERT INTO topic_sampling_log
            4. conn.commit()

        Returns: {exam_id: {"correct": N, "total": N, "accuracy": float}}
        """

    def run_adaptive_session(self, count=10, mode="adaptive") -> dict:
        """Run adaptive practice using the REAL session_builder.

        Algorithm:
            1. session_id = uuid4()
            2. mcqs, _ = build_session(self.conn, self.student_id,
                                        count=count, mode=mode)
               (reads CURRENT config.py values)
            3. For each mcq:
                 a. correct_option = _get_correct_option(mcq)
                 b. selected, is_correct = _decide_answer(...)
                 c. INSERT INTO student_answer_log
                 d. upsert_stats(self.conn, self.student_id,
                                 mcq["id"], is_correct)
            4. log_session_sampling(...)
            5. conn.commit()
            6. Return {"correct": N, "total": N, "accuracy": float}
        """

    def measure_exam_accuracy(self) -> float:
        """SELECT AVG from student_answer_log WHERE session_type LIKE 'exam%'."""

    def measure_adaptive_accuracy(self) -> float:
        """SELECT AVG from student_answer_log WHERE session_type='adaptive_adaptive'."""

    def cleanup(self):
        """DELETE FROM student_answer_log, student_mcq_stats,
        topic_sampling_log WHERE student_id=?.
        """
```

### Determinism guarantee

`_decide_answer()` uses `profile["seed"]` and `mcq_id` via a hash formula — NOT `random.random()`. Same seed + same mcq_id → same answer across all configs. If a different config causes `build_session()` to select different MCQs, those MCQs are answered deterministically.

---

## FILE 5: `shokti/evolution/benchmark.py`

```python
class ShoktiBenchmark:
    """Measures one candidate config's fitness.

    Lifecycle per call to measure():
        1. snapshot config.py (as text for rollback)
        2. apply candidate config to config.py
        3. generate STUDENTS_PER_CYCLE random students
        4. for each student:
             simulated exams → simulated adaptive → measure → cleanup
        5. restore config.py from snapshot (try/finally)
        6. return FitnessResult
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Open new connection with row_factory=sqlite3.Row."""

    def measure(self, config: dict) -> FitnessResult:
        """Full measurement pipeline.

        Algorithm:
            1. snapshot = apply_config.snapshot_config()
            2. applied = apply_config.apply(config)
               If not applied: raise RuntimeError
            3. t0 = time.time()
            4. profiles = [
                   generate_student(rng, i)
                   for i in range(STUDENTS_PER_CYCLE)
                   for rng in [random.Random(cycle_seed * 1000 + i)]
               ]
            5. For each profile:
                 conn = self._get_conn()
                 sid = f"evo_{int(t0)}_{i}"
                 sim = SimulatedStudent(sid, profile, conn)
                 sim.run_exams(["1", "2", "3"])
                 sim.run_adaptive_session(count=10)
                 e_acc = sim.measure_exam_accuracy()
                 a_acc = sim.measure_adaptive_accuracy()
                 d_raw = a_acc - e_acc
                 # Relative improvement: fraction of remaining gap closed
                 # Δ_rel = (a_acc − e_acc) / (1 − e_acc)
                 # Clamp e_acc < 1 to avoid div/0; if e_acc==1 (perfect exam), Δ_rel = 0
                 d_rel = d_raw / (1.0 - e_acc) if e_acc < 1.0 else 0.0
                 deltas.append(d_rel)
                 raw_deltas.append(d_raw)
                 exam_accuracies.append(e_acc)
                 adaptive_accuracies.append(a_acc)
                 sim.cleanup()
                 conn.close()
            6. t1 = time.time()
            7. fitness = mean(deltas), fitness_std = stdev(deltas)
            8. Return FitnessResult(...)

        Finally (always):
            9. apply_config.restore(snapshot)
        """
```

### Error handling

| Exception | Where | Action |
|---|---|---|
| FileNotFoundError | config.py not found | Raise immediately |
| ValueError | field not found in config.py | Caught, skip cycle, log error |
| SyntaxError | config.py after apply | Caught, restore snapshot, skip cycle |
| sqlite3.OperationalError | DB query | Raise immediately (bug) |
| Exception | anywhere | finally restores config.py, re-raises |

---

## FILE 6: `shokti/evolution/optimizer.py`

### Internal state machine

```
INITIALIZED = False
BEST_CONFIG = None        → dict
BEST_FITNESS = -inf       → float
ELITE = []                → list of (config_dict, fitness), sorted desc
STAGNATION = 0            → cycles without improvement
RESTART_COUNTER = 0       → cycles since last full restart
RNG = Random(seed)
```

### Methods

```python
class ConfigOptimizer:
    """Adaptive Random Mutation with Elite Retention."""

    def __init__(self, seed: int = 0):
        ...

    def initialize(self, current_config: dict,
                   current_fitness: float = -float("inf")):
        """Seed optimizer with current config. Must be called before propose()."""

    def propose(self, cycle: int) -> dict:
        """Generate the next candidate config.

        cycle is 1-indexed.

        Algorithm:
            if cycle == 1: return current config (baseline measurement)

            if stagnation >= STAGNATION_LIMIT or restart_counter >= RESTART_INTERVAL:
                return FULL RANDOM CONFIG (all params randomized in [min,max])

            # Normal mutation:
            1. Parent: 70% from elite[0], 30% from random elite member
            2. Pick 1–2 random params
            3. Each: ±step, clamp to [min,max], quantize to step
            4. Return mutated copy

            finally: restart_counter += 1
        """

    def _random_config(self) -> dict:
        """Full random config within all param bounds. Resets restart_counter."""

    def update(self, fitness: FitnessResult) -> bool:
        """Process measurement result. Returns True if fitness improved.

        Effects:
            - If improved: update best_config/fitness, reset stagnation
            - If not improved: increment stagnation
            - Always: insert into elite set, trim to top ELITE_SIZE
        """

    @property
    def best_config(self) -> dict: ...
    @property
    def best_fitness(self) -> float: ...
    @property
    def stagnation(self) -> int: ...
    @property
    def elite_configs(self) -> list[tuple[dict, float]]: ...
```

### Why the algorithm works

1. **Elite retention** — top 5 configs kept even after bad cycles. Never lose progress.
2. **Random restart** — if stuck for 15 cycles or 30 cycles elapsed, jump to random location. Best config preserved in elite.
3. **70/30 parent selection** — mostly mutate best, occasionally explore other elite members.
4. **1–2 params per mutation** — small steps, stable convergence. Avoids random-walk behavior.
5. **Pseudo-random students each cycle** — each cycle tests 5 new random students. A config that generalizes across many random cohorts stays in elite; a config that got lucky drops out.
6. **Thompson sampling** — high-variance configs (uncertain) are given exploration bonus. Prevents early convergence to a locally optimal config.

### Thompson sampling for exploration bonus

Instead of always proposing from `elite[0]`, sample a config:

```python
def _sample_config(self) -> dict:
    """Thompson sampling: sample from posterior of fitness."""
    import math
    # Track (mean, variance) for each evaluated config
    # Sample from Normal(mean, std + exploration_bonus)
    # Exploration bonus = max(0, (target_fitness - mean) * 0.5)
    samples = []
    for cfg, fit in self._evaluated:
        std = self._fit_std.get(cfg_key(cfg), 0.01)
        bonus = max(0, (self._best_fitness - fit) * 0.3)
        sample = random.gauss(fit, std + bonus)
        samples.append((cfg, sample))
    samples.sort(key=lambda x: x[1], reverse=True)
    return samples[0][0]
```

With small elite sets (<10), add a random candidate to the sample pool to avoid degenerate selection.

### Selection strategy (hybrid)

```python
def propose(self, cycle: int) -> dict:
    # Cycle 1: baseline (never reject)
    if cycle == 1:
        return self._current_config.copy()

    r = random.random()
    if r < 0.5:
        # 50%: exploit — Thompson sample from elite
        return self._sample_config()
    elif r < 0.8:
        # 30%: mutate from elite
        return self._mutate_from_elite()
    else:
        # 20%: random restart (fresh explore)
        return self._random_config()
```

**Effect**: Exploitation-biased but with systematic exploration. The 50% Thompson sample pulls toward high-confidence good configs; the 20% random restarts prevent lock-in to local optima.

---

## FILE 7: `shokti/evolution/engine.py`

```python
class EvolutionEngine:
    """Main evolution loop."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def run(self, cycles: int = CYCLES_DEFAULT) -> dict:
        """Run full evolution.

        Algorithm:
            1. Open DB, create_evolution_table()
            2. Initialize benchmark and optimizer
            3. current_config = apply_config.read_current_config()
            4. best_from_log = get_best_config(conn)
               If best_from_log:
                   optimize.initialize(best_from_log, get_best_fitness(conn))
               Else:
                   optimize.initialize(current_config, -inf)

            5. For cycle in range(1, cycles + 1):

               a. candidate = optimizer.propose(cycle)
               b. fitness = benchmark.measure(candidate)
               c. accepted = optimizer.update(fitness)
               d. Log cycle to evolution_log
               e. If cycle % 10 == 0: print progress

            6. After loop: deploy best config to config.py
            7. Print summary table
            8. Return summary dict
        """
```

### Console output during run

```
Shokti Evolution v1.0
────────────────────────────────────────────────────────────
Cycle   Fitness    σ(Δ)    Δ vs best    Elite   Accepted
──────  ─────────  ──────  ───────────  ──────  ────────
  1/100   +0.0000   —        (baseline)     1    —  start
 10/100   +0.0234   0.015    +0.0234        2    ✓
 20/100   +0.0312   0.018    +0.0078        3    ✓
 30/100   +0.0298   0.020    -0.0014        3    ✗
 40/100   +0.0381   0.012    +0.0083        4    ✓
 50/100   +0.0423   0.016    +0.0042        5    ✓  ★BEST
 70/100   +0.0350   0.022    -0.0073        5    ✗
 80/100   [RESTART — stagnation]
 90/100   +0.0441   0.014    +0.0091        5    ✓
100/100   +0.0452   0.011    +0.0011        5    ✓
────────────────────────────────────────────────────────────
Best config (fitness=+0.0452):
  WEAK_THRESHOLD=0.45   QBANK_RATIO=0.55   GENERATED_RATIO=0.15
  WEAK_TOPIC_RATIO=0.25 WEAKNESS_WEIGHT=0.50  DEBT_WEIGHT=0.30
  IMPORTANCE_WEIGHT=0.20  GAP_THRESHOLD=12  SM2_INITIAL_EF=2.7
Deployed to shokti/core/config.py
```

---

## FILE 8: `shokti/evolution/apply_config.py`

```python
CONFIG_FILE_PATH = Path(__file__).resolve().parents[2] / "shokti/core/config.py"


def read_current_config() -> dict:
    """Import shokti.core.config and read all 9 evolvable parameters.

    Uses importlib.reload() to force fresh import (avoid cache).
    Returns dict like {"WEAK_THRESHOLD": 0.5, "QBANK_RATIO": 0.5, ...}.
    """


def snapshot_config() -> str:
    """Return full text of config.py for rollback."""


def restore(snapshot: str) -> bool:
    """Overwrite config.py with snapshot text."""


def apply(config: dict) -> bool:
    """Modify config.py in-place using regex replacement.

    For each (key, value) in config:
        - Search for: rf"^    {re.escape(key)}: .*$"
        - Replace with: f"    {key}: {repr(value)}"
        - If not found: raise ValueError

    Steps:
        1. Read file as text
        2. For each param: regex search + replace
        3. Verify syntax with py_compile BEFORE writing
        4. Write text to config.py
        5. Clear import cache for shokti.core.config
        6. Return True
    """


def deploy() -> bool:
    """Apply best config from evolution_log to config.py."""
```

---

## FILE 9: `shokti/evolution/report.py`

```python
def print_progress_line(result: CycleResult) -> None:
    """Print a single line during run."""

def print_full_table(conn) -> None:
    """Print full evolution_log as formatted table."""

def print_status(conn) -> None:
    """Print latest cycle + best config."""

def print_history(conn, limit=20) -> None:
    """Print last N cycles."""
```

---

## FILE 10: `shokti/evolution/run.py`

```python
def run_evolution(cycles: int | None = None) -> dict:
    """Create engine and run."""

def status() -> None:
    """Print current status from evolution_log."""

def history(limit: int = 20) -> None:
    """Print recent evolution cycles."""

def deploy() -> None:
    """Apply best config from evolution_log to config.py."""


def main() -> None:
    """CLI entry point.

    Usage:
        python -m shokti.evolution run [--cycles 100]
        python -m shokti.evolution status
        python -m shokti.evolution history [--limit 20]
        python -m shokti.evolution deploy
    """
    parser = argparse.ArgumentParser(description="Shokti Evolution Optimizer")
    sub = parser.add_subparsers(dest="command")
    # run, status, history, deploy subcommands
    args = parser.parse_args()
    args.func(args)
```

---

## Changes to existing files

### `shokti/core/config.py`

Add one field to `MCQConfig`:

```python
@dataclass
class MCQConfig:
    ...
    GAP_THRESHOLD: int = 15

    # SM-2 initial Easiness Factor
    SM2_INITIAL_EF: float = 2.5
```

### `shokti/adaptive_practice.py`

In `upsert_stats()`, replace hardcoded `2.5` with `MCQ.SM2_INITIAL_EF`:

```python
from shokti.core.config import MCQ

# Lines 55, 65:
# VALUES (?, ?, 1, 0, CURRENT_TIMESTAMP, ?, ?, MCQ.SM2_INITIAL_EF, 1)
```

### `shokti/spaced_repetition.py`

In `update_review_date()`, replace hardcoded `2.5` with `MCQ.SM2_INITIAL_EF`:

```python
from shokti.core.config import MCQ

# Line 28:
# ef = MCQ.SM2_INITIAL_EF
```

---

## Pre-existing bug: topic_id collision — FIXED

`session_builder.py` previously used `topic_id` as dict key, but `topic_id` is not globally unique:

| topic_id | topic_name (Ch06) | topic_name (Ch08) |
|----------|-------------------|-------------------|
| "1" | ব্রায়োফাইটা | টিস্যু ও ভাজক টিস্যু |
| "2" | Riccia | স্থায়ী টিস্যু |
| "3" | টেরিডোফাইটা | আবরণী টিস্যু |
| "4" | Pteris | গ্রাউন্ড টিস্যুতন্ত্র |

**Fix applied**: all weight lookups now use `(chapter_id, topic_id)` as a composite key. `TopicStats.topic_key` is `(str, str)`. `session_builder` and `weak_topic_tracker` both group by `(qb.chapter_id, qb.topic_id)` in all SQL. The simulator uses `mcq_id` (globally unique) for answer decisions — unaffected.

**Impact on evolution**: the optimizer now searches over a clean, noise-free weight signal. Configs that performed well *despite* the collision noise may be superseded by better configs that were previously masked.

---

## Config validation (pre-measurement guard)

Before passing a candidate config to `benchmark.measure()`, validate all constraints. Invalid configs are rejected (return `None`, skip the cycle) rather than risking a crash or silent bad data.

```python
def validate_config(config: dict) -> str | None:
    """Return None if valid, or an error string explaining the first violation."""
    errors = []

    # Ratio sanity
    total_ratio = (
        config.get("QBANK_RATIO", 0) +
        config.get("GENERATED_RATIO", 0) +
        config.get("WEAK_TOPIC_RATIO", 0)
    )
    if total_ratio > 1.0:
        errors.append(f"ratio sum {total_ratio:.2f} > 1.0")

    # Sampling weights must sum to 1.0 (±0.02 tolerance)
    weight_sum = (
        config.get("WEAKNESS_WEIGHT", 0) +
        config.get("DEBT_WEIGHT", 0) +
        config.get("IMPORTANCE_WEIGHT", 0)
    )
    if abs(weight_sum - 1.0) > 0.02:
        errors.append(f"weight sum {weight_sum:.2f} ≠ 1.0")

    # SM2_INITIAL_EF range
    ef = config.get("SM2_INITIAL_EF", 2.5)
    if not (1.3 <= ef <= 3.0):
        errors.append(f"SM2_INITIAL_EF {ef} outside [1.3, 3.0]")

    # WEAK_THRESHOLD in (0, 1)
    wt = config.get("WEAK_THRESHOLD", 0.5)
    if not (0 < wt < 1):
        errors.append(f"WEAK_THRESHOLD {wt} not in (0, 1)")

    if errors:
        return f"Config rejected: {', '.join(errors)}"
    return None
```

**When called**: In `optimizer.propose()`, before returning the candidate config. If invalid, draw a new random config instead (do not count as a cycle).

| Scenario | Behavior |
|---|---|
| No evolution_log table | `create_evolution_table()` creates it (idempotent) |
| First run (no prior log) | optimizer initialized with current config, best_fitness=-inf |
| Config.py syntax error after apply | `py_compile` detects before write; ValueError raised, cycle skipped |
| `build_session()` returns 0 MCQs | adaptive accuracy = 0.0, delta = -exam_accuracy |
| Simulated student answers 0 exam questions | exam accuracy = 0.0 |
| KeyboardInterrupt mid-cycle | finally block restores config.py |
| KeyboardInterrupt between cycles | Stopped gracefully. `deploy` still works (uses best in log) |
| Consecutive same config proposed | Possible (1/9 mutation collision). Fine — remeasurement handles noise |
| fitness_std = 0.0 | Possible with 5 identical deltas. Correct behavior |
| Multiple runs accumulate in log | `get_best_config()` queries MAX across all rows. History preserved |

---

## Estimated run time

| Step | Time |
|---|---|
| Apply config + snapshot | 0.02s |
| Generate 5 random students | 0.001s |
| Per student: 3 exams (90 MCQs) + 10 adaptive | ~0.7s |
| Per student: cleanup | 0.01s |
| Per cycle (5 students) | ~3.5s |
| **100 cycles** | **~6 minutes** |

---

## Verification

```bash
python -m py_compile shokti/evolution/*.py
python -m shokti.evolution run --cycles 3
python -m shokti.evolution status
python -m shokti.evolution history --limit 5
python -m shokti.evolution deploy
python -c "from shokti.core.config import MCQ; print(MCQ.WEAK_THRESHOLD)"
```

## Fitness = Relative Improvement (Δ_rel), not raw Δ

```
Δ_raw  = adaptive_accuracy − exam_accuracy
Δ_rel = Δ_raw / (1 − exam_accuracy)      ← clamped to 0 if exam_accuracy = 1
```

**Why Δ_rel**: Raw Δ rewards configs where weak students start low (20%→40% = +20pp beats 70%→80% = +10pp). Δ_rel normalizes by the remaining ceiling — both cases close 20% of the remaining gap and score equally. A config that helps a strong student from 85%→92% (+7pp, Δ_rel=47%) scores higher than one that takes a weak student from 30%→45% (+15pp, Δ_rel=21%).

**Clamp at ceiling**: If `exam_accuracy == 1.0`, `Δ_rel = 0` (no room to improve). If `exam_accuracy >= 0.99`, clamp to `Δ_rel = 0` to avoid division instability.

**Reporting**: `FitnessResult.raw_deltas` stores the raw Δ for diagnostics. `FitnessResult.deltas` stores Δ_rel (the optimized metric).
