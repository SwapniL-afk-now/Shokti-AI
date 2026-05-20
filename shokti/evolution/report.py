"""Reporting utilities for evolution results."""

import sqlite3
from shokti.core.config import DB_PATH


def _fmt_cfg(cfg: dict) -> str:
    return (f"WT={cfg.get('WEAK_THRESHOLD', 0):.2f} "
            f"QR={cfg.get('QBANK_RATIO', 0):.2f} "
            f"GR={cfg.get('GENERATED_RATIO', 0):.2f} "
            f"Wr={cfg.get('WEAK_TOPIC_RATIO', 0):.2f} "
            f"Wk={cfg.get('WEAKNESS_WEIGHT', 0):.2f} "
            f"Dbt={cfg.get('DEBT_WEIGHT', 0):.2f} "
            f"Imp={cfg.get('IMPORTANCE_WEIGHT', 0):.2f} "
            f"GT={cfg.get('GAP_THRESHOLD', 0)} "
            f"EF={cfg.get('SM2_INITIAL_EF', 0):.2f}")


def print_full_table(conn: sqlite3.Connection) -> None:
    from shokti.evolution.models import get_recent_cycles
    rows = get_recent_cycles(conn, limit=100)
    if not rows:
        print("No evolution cycles recorded.")
        return
    print(f"\n{'='*90}")
    print(f"  EVOLUTION LOG")
    print(f"{'='*90}")
    print(f"  {'Cyc':>3}  {'Fitness':>9}  {'Accepted':>9}  {'Config Summary'}")
    print(f"  {'-'*3}  {'-'*9}  {'-'*9}  {'-'*60}")
    for r in reversed(rows):
        icon = "✓" if r.accepted else "✗"
        print(f"  {r.cycle:3d}  {r.fitness_after:>+9.4f}  {icon:>9}  {_fmt_cfg(r.config)[:60]}")
    print(f"{'='*90}\n")


def print_status(conn: sqlite3.Connection) -> None:
    from shokti.evolution.models import get_best_config, get_best_fitness, get_total_cycles, get_accepted_ratio
    cfg = get_best_config(conn)
    best_fit = get_best_fitness(conn)
    total = get_total_cycles(conn)
    ratio = get_accepted_ratio(conn)

    print(f"\n{'='*60}")
    print(f"  EVOLUTION STATUS")
    print(f"{'='*60}")
    print(f"  Total cycles:     {total}")
    print(f"  Accepted ratio:   {ratio:.1%}")
    print(f"  Best fitness:    {best_fit:+.4f}")
    if cfg:
        print(f"\n  Best config:")
        for k, v in sorted(cfg.items()):
            print(f"    {k:<20} = {v}")
    print(f"{'='*60}\n")


def print_history(conn: sqlite3.Connection, limit: int = 20) -> None:
    from shokti.evolution.models import get_recent_cycles
    rows = get_recent_cycles(conn, limit=limit)
    if not rows:
        print("No evolution history.")
        return
    print(f"\n{'='*90}")
    print(f"  RECENT EVOLUTION CYCLES (last {limit})")
    print(f"{'='*90}")
    print(f"  {'Cyc':>3}  {'FitBef':>8}  {'FitAft':>8}  {'dFit':>8}  {'Acc':>4}  {'dt(s)':>6}  Config")
    print(f"  {'-'*3}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*4}  {'-'*6}  {'-'*40}")
    for r in rows:
        dsign = "+" if r.delta >= 0 else ""
        icon = "✓" if r.accepted else "✗"
        print(f"  {r.cycle:3d}  {r.fitness_before:>+8.4f}  {r.fitness_after:>+8.4f}  "
              f"{dsign}{r.delta:>7.4f}  {icon:>4}  {r.duration:>6.1f}  "
              f"{_fmt_cfg(r.config)[:40]}")
    print(f"{'='*90}\n")