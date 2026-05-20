"""
Entry point for bank_stats.
Delegates to analytics module.
"""

import sys
from pathlib import Path

# Allow running as: python bank_stats.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shokti.analytics.bank_stats import main

if __name__ == "__main__":
    main()