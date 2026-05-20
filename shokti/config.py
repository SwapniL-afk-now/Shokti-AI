"""
Central configuration file for Shokti MCQ System.
Re-exports from core.config for backward compatibility.

Usage:
    from shokti.config import GEMINI, MCQ  # backward compatible
    from shokti.core.config import GEMINI, MCQ  # recommended
"""

from shokti.core.config import (
    ROOT_DIR,
    DB_DIR,
    DB_PATH,
    ENV_FILE,
    BOOKS_DIR,
    OUTPUT_DIR,
    GEMINI,
    MCQ,
    DB,
)

__all__ = [
    "ROOT_DIR",
    "DB_DIR", 
    "DB_PATH",
    "ENV_FILE",
    "BOOKS_DIR",
    "OUTPUT_DIR",
    "GEMINI",
    "MCQ",
    "DB",
]