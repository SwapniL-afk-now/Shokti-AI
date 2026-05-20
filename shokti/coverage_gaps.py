"""
Entry point for coverage_gaps.
Delegates to analytics module.
"""

import sys
from pathlib import Path

# Allow running as: python coverage_gaps.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shokti.analytics.gap_analyzer import main

if __name__ == "__main__":
    main()