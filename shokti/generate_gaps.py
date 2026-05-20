"""
Entry point for generate_gaps.
Delegates to generators module.
"""

import sys
from pathlib import Path

# Allow running as: python generate_gaps.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shokti.generators.gap_filler import main

if __name__ == "__main__":
    main()