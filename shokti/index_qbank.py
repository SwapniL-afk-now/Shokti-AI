"""
Entry point for index_qbank.
Delegates to ingest module.
"""

import sys
from pathlib import Path

# Allow running as: python index_qbank.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shokti.ingest.json_importer import main

if __name__ == "__main__":
    main()