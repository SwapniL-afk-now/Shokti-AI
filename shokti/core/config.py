"""
Core configuration module for Shokti MCQ System.
All hardcoded paths, API settings, and business logic values are defined here.

Usage:
    from shokti.core.config import ROOT_DIR, GEMINI, MCQ
"""

from dataclasses import dataclass, field
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DB_DIR = ROOT_DIR / "db"
import os
DB_PATH = os.getenv("DB_PATH_OVERRIDE", DB_DIR / "question_bank.db")
ENV_FILE = ROOT_DIR / ".env"
BOOKS_DIR = ROOT_DIR / "books"
OUTPUT_DIR = ROOT_DIR


@dataclass
class GeminiConfig:
    MODEL: str = "gemini-3.1-flash-lite"
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    STORE_DISPLAY_NAME: str = "biology-hasan-sir"
    STORE_NAME: str = "fileSearchStores/biologyhasansir-mzdu36ya24fr"
    MEDICAL_STORE_DISPLAY_NAME: str = "free_medical_qbank"
    MEDICAL_STORE_NAME: str = "fileSearchStores/freemedicalqbank-lnxjz27ui0b7"
    MAX_RETRIES: int = 3
    RETRY_DELAY_BASE: int = 30
    ENABLE_CONTEXT_CACHE: bool = True


@dataclass
class MCQConfig:
    GAP_THRESHOLD: int = 15
    MCQS_PER_GAP: int = 3
    WEAK_THRESHOLD: float = 0.50
    QBANK_RATIO: float = 0.40
    GENERATED_RATIO: float = 0.20
    WEAK_TOPIC_RATIO: float = 0.25
    FRESH_GENERATED_RATIO: float = 0.15
    ENABLE_FRESH_GENERATION_IN_PRACTICE: bool = True
    FRESH_GENERATION_MAX_WAIT_SECONDS: int = 20
    ENABLE_GENERATION_ON_SELECTION: bool = True
    MIN_GENERATED_ON_SELECTION: int = 10
    DIFFICULTY_EASY_RATIO: float = 0.3
    DIFFICULTY_MEDIUM_RATIO: float = 0.5
    DIFFICULTY_HARD_RATIO: float = 0.2
    SM2_INITIAL_EF: float = 1.5
    OUTPUT_LANGUAGE: str = "Bangla (bn), with Biology terms in English brackets"
    CHAPTER_JOBS: list = field(default_factory=lambda: [
        {
            "chapter": "Chapter 06",
            "topic": "ব্রায়োফাইটা ও টেরিডোফাইটা (Bryophyta and Pteridophyta)",
            "book_page_range": "198-208",
            "source_file": "chapter_06_bryophyta_and_pteridophyta_pages_198-208.pdf",
            "number_of_mcqs": 15,
        },
        {
            "chapter": "Chapter 08",
            "topic": "টিস্যু ও টিস্যুতন্ত্র (Tissue and Tissue System)",
            "book_page_range": "235-254",
            "source_file": "chapter_08_tissue_and_tissue_system_pages_235-254.pdf",
            "number_of_mcqs": 15,
        },
    ])


@dataclass
class DBConfig:
    TABLE_QUESTION_BANK: str = "question_bank"
    TABLE_STUDENT_ANSWER_LOG: str = "student_answer_log"
    TABLE_STUDENT_MCQ_STATS: str = "student_mcq_stats"


@dataclass
class SamplingConfig:
    WEAKNESS_WEIGHT: float = 0.40
    DEBT_WEIGHT: float = 0.35
    IMPORTANCE_WEIGHT: float = 0.25
    DEBT_PEER_WINDOW_DAYS: int = 30


@dataclass
class ExamConfig:
    EXAM_IDS: list = field(default_factory=lambda: ["1", "2", "3"])
    DEFAULT_EXAM_COUNT: int = 30
    DEFAULT_EXAM_DURATION: int = 30


DB = DBConfig()
GEMINI = GeminiConfig()
MCQ = MCQConfig()
SAMPLING = SamplingConfig()
EXAM = ExamConfig()
