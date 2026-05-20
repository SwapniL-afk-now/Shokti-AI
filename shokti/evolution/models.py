"""Dataclasses, evolution_log table, and CRUD helpers."""

from dataclasses import dataclass
from typing import Optional
import sqlite3


@dataclass
class FitnessResult:
    config: dict
    fitness: float
    fitness_std: float
    student_growths: list[float]
    student_ids: list[str]
    duration: float
    timestamp: str


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


EVOLUTION_LOG_SCHEMA = """
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
"""


def create_evolution_table(conn: sqlite3.Connection) -> None:
    conn.execute(EVOLUTION_LOG_SCHEMA)
    conn.commit()


def log_cycle(conn: sqlite3.Connection, result: CycleResult) -> None:
    import json
    conn.execute("""
        INSERT INTO evolution_log
          (cycle, config_snapshot, fitness_before, fitness_after, delta,
           accepted, duration_seconds, n_students, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.cycle,
        json.dumps(result.config),
        result.fitness_before,
        result.fitness_after,
        result.delta,
        result.accepted,
        result.duration,
        result.n_students,
        result.timestamp,
    ))
    conn.commit()


def get_best_config(conn: sqlite3.Connection) -> Optional[dict]:
    import json
    row = conn.execute("""
        SELECT config_snapshot FROM evolution_log
        ORDER BY fitness_after DESC LIMIT 1
    """).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def get_best_fitness(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT MAX(fitness_after) FROM evolution_log").fetchone()
    return row[0] if row and row[0] is not None else float("-inf")


def get_last_config(conn: sqlite3.Connection) -> Optional[dict]:
    import json
    row = conn.execute("""
        SELECT config_snapshot FROM evolution_log
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def get_last_fitness(conn: sqlite3.Connection) -> Optional[float]:
    row = conn.execute("""
        SELECT fitness_after FROM evolution_log ORDER BY id DESC LIMIT 1
    """).fetchone()
    return row[0] if row else None


def get_accepted_ratio(conn: sqlite3.Connection) -> float:
    row = conn.execute("""
        SELECT SUM(accepted)*1.0/COUNT(*) FROM evolution_log
    """).fetchone()
    return row[0] if row and row[0] is not None else 0.0


def get_total_cycles(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM evolution_log").fetchone()
    return row[0] if row else 0


def get_recent_cycles(conn: sqlite3.Connection, limit: int = 20) -> list[CycleResult]:
    import json
    rows = conn.execute("""
        SELECT cycle, config_snapshot, fitness_before, fitness_after, delta,
               accepted, duration_seconds, n_students, timestamp
        FROM evolution_log ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    return [
        CycleResult(
            cycle=row[0],
            config=json.loads(row[1]),
            fitness_before=row[2] or 0.0,
            fitness_after=row[3] or 0.0,
            delta=row[4] or 0.0,
            accepted=bool(row[5]),
            duration=row[6] or 0.0,
            timestamp=row[8] or "",
            n_students=row[7] or 0,
        )
        for row in rows
    ]