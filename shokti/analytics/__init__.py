"""Analytics modules."""

from shokti.analytics.bank_stats import main as bank_stats_main
from shokti.analytics.gap_analyzer import main as gap_analyzer_main

__all__ = ["bank_stats_main", "gap_analyzer_main"]