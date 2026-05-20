"""Benchmark: measures one candidate config's fitness via learning growth."""

import time
import random
from pathlib import Path
from typing import Optional
import sqlite3

from shokti.core.config import DB_PATH
from shokti.evolution.config import (
    STUDENTS_PER_CYCLE,
    DAYS_PER_CYCLE,
    generate_student,
    validate_config,
)
from shokti.evolution.models import FitnessResult
from shokti.evolution.simulator import SimulatedStudent
from shokti.evolution.apply_config import snapshot_config, apply, restore


class ShoktiBenchmark:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def measure(self, config: dict, cycle_seed: int = 0) -> Optional[FitnessResult]:
        error = validate_config(config)
        if error:
            return None

        snapshot = snapshot_config()
        try:
            applied = apply(config)
            if not applied:
                return None

            t0 = time.time()
            all_growths = []
            student_ids = []

            for i in range(STUDENTS_PER_CYCLE):
                rng = random.Random(cycle_seed * 1000 + i)
                profile = generate_student(rng, i)

                conn = self._get_conn()
                sid = f"evo_{int(t0)}_{i}"
                sim = SimulatedStudent(sid, profile, conn)

                # Day 0: 3 preset exams establish baseline
                sim.run_exams(["1", "2", "3"])

                # Day 1..DAYS_PER_CYCLE: checkin + adaptive exams
                for day in range(1, DAYS_PER_CYCLE + 1):
                    sim.run_day(day, config)

                growth = sim.growth
                all_growths.append(growth)
                student_ids.append(sid)

                sim.cleanup()
                conn.close()

            t1 = time.time()
            duration = t1 - t0

            import statistics
            fitness = statistics.mean(all_growths) if all_growths else 0.0
            fitness_std = statistics.stdev(all_growths) if len(all_growths) > 1 else 0.0

            return FitnessResult(
                config=config,
                fitness=fitness,
                fitness_std=fitness_std,
                student_growths=all_growths,
                student_ids=student_ids,
                duration=duration,
                timestamp=__import__("datetime").datetime.now().isoformat(),
            )

        finally:
            restore(snapshot)