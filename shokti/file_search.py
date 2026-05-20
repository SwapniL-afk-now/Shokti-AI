"""
Entry point for file_search.
Delegates to infrastructure module.
"""

import sys
from pathlib import Path

# Allow running as: python file_search.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shokti.infrastructure.file_uploader import main

if __name__ == "__main__":
    main()