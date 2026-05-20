"""CLI entrypoints for evolution."""

import argparse
from shokti.evolution.engine import EvolutionEngine
from shokti.evolution.report import print_status, print_history, print_full_table
from shokti.evolution.models import create_evolution_table
from shokti.evolution.apply_config import deploy as apply_deploy
from shokti.core.config import DB_PATH
import sqlite3


def run_evolution(cycles: int | None = None) -> dict:
    cycles = cycles or 100
    print(f"\n{'='*60}")
    print(f"  Shokti Evolution v1.0")
    print(f"{'='*60}")
    engine = EvolutionEngine(DB_PATH)
    result = engine.run(cycles=cycles)
    print(f"\n{'='*60}")
    print(f"  EVOLUTION COMPLETE")
    print(f"{'='*60}")
    print(f"  Cycles:           {result['cycles']}")
    print(f"  Best fitness:     {result['best_fitness']:+.4f}")
    print(f"  Total time:       {result['total_time']:.1f}s")
    print(f"\n  Best config deployed to shokti/core/config.py")
    print(f"{'='*60}\n")
    return result


def status() -> None:
    conn = sqlite3.connect(DB_PATH)
    print_status(conn)
    conn.close()


def history(limit: int = 20) -> None:
    conn = sqlite3.connect(DB_PATH)
    print_history(conn, limit=limit)
    conn.close()


def deploy() -> None:
    ok = apply_deploy()
    if ok:
        print("Best config deployed to shokti/core/config.py")
    else:
        print("No best config found in evolution_log.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Shokti Evolution Optimizer")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Run evolution").add_argument(
        "--cycles", type=int, default=None)
    sub.add_parser("status", help="Show current status")
    sub.add_parser("history", help="Show recent cycles").add_argument(
        "--limit", type=int, default=20)
    sub.add_parser("deploy", help="Deploy best config")
    args = parser.parse_args()

    if args.command == "run":
        run_evolution(cycles=args.cycles if hasattr(args, "cycles") else None)
    elif args.command == "status":
        status()
    elif args.command == "history":
        history(limit=args.limit if hasattr(args, "limit") else 20)
    elif args.command == "deploy":
        deploy()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()