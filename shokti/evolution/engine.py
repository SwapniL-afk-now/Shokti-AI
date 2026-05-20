"""Evolution engine — main loop."""

import time
from pathlib import Path
import sqlite3

from shokti.core.config import DB_PATH
from shokti.evolution.config import CYCLES_DEFAULT, STUDENTS_PER_CYCLE
from shokti.evolution.models import (
    FitnessResult,
    CycleResult,
    create_evolution_table,
    log_cycle,
    get_best_config,
    get_best_fitness,
)
from shokti.evolution.benchmark import ShoktiBenchmark
from shokti.evolution.optimizer import ConfigOptimizer
from shokti.evolution.apply_config import read_current_config, apply


class EvolutionEngine:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def run(self, cycles: int = CYCLES_DEFAULT) -> dict:
        conn = self._get_conn()
        create_evolution_table(conn)

        benchmark = ShoktiBenchmark(self.db_path)
        optimizer = ConfigOptimizer()

        current_config = read_current_config()
        best_from_log = get_best_config(conn)
        best_fitness = get_best_fitness(conn)

        if best_from_log:
            optimizer.initialize(best_from_log, best_fitness)
        else:
            optimizer.initialize(current_config, float("-inf"))

        t_start = time.time()
        cycle_results = []

        for cycle in range(1, cycles + 1):
            candidate = optimizer.propose(cycle)
            result = benchmark.measure(candidate, cycle_seed=cycle)

            if result is None:
                continue

            old_best = optimizer.best_fitness
            accepted = optimizer.update(result)

            cycle_res = CycleResult(
                cycle=cycle,
                config=candidate,
                fitness_before=old_best,
                fitness_after=result.fitness,
                delta=result.fitness - old_best,
                accepted=accepted,
                duration=result.duration,
                timestamp=result.timestamp,
                n_students=STUDENTS_PER_CYCLE,
            )
            log_cycle(conn, cycle_res)
            cycle_results.append(cycle_res)

            if cycle % 10 == 0 or cycle == 1:
                delta = result.fitness - optimizer.best_fitness
                sign = "+" if delta >= 0 else ""
                print(f"  {cycle:3d}/{cycles}  fitness={result.fitness:+.4f}  "
                      f"std={result.fitness_std:.4f}  "
                      f"best={optimizer.best_fitness:.4f}  "
                      f"d={sign}{delta:.4f}  "
                      f"elite={len(optimizer.elite_configs)}  "
                      f"{'✓' if accepted else '✗'}")

        apply(optimizer.best_config)

        t_total = time.time() - t_start

        conn.close()

        return {
            "cycles": cycles,
            "best_config": optimizer.best_config,
            "best_fitness": optimizer.best_fitness,
            "total_time": t_total,
            "results": cycle_results,
        }
