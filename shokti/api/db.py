"""Async SQLAlchemy engine + session factory."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from shokti.core.config import DB_PATH

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Raw-SQL tables (student_answer_log, student_mcq_stats) not in Base — create manually
LIVENESS_TABLES = """
CREATE TABLE IF NOT EXISTS student_answer_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT,
    mcq_id INTEGER,
    is_correct BOOLEAN,
    confidence_rating INTEGER,
    answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    time_spent_seconds INTEGER,
    session_type TEXT DEFAULT 'diagnostic',
    session_id TEXT,
    selected_option TEXT DEFAULT '',
    FOREIGN KEY (mcq_id) REFERENCES question_bank(id)
);
CREATE INDEX IF NOT EXISTS idx_student_answer ON student_answer_log(student_id, mcq_id);
CREATE TABLE IF NOT EXISTS student_mcq_stats (
    student_id TEXT,
    mcq_id INTEGER,
    correct_count INTEGER DEFAULT 0,
    wrong_count INTEGER DEFAULT 0,
    easiness_factor REAL DEFAULT 1.5,
    interval_days INTEGER DEFAULT 0,
    last_seen_at TIMESTAMP,
    next_review_at TIMESTAMP,
    last_reviewed_at TIMESTAMP,
    PRIMARY KEY (student_id, mcq_id),
    FOREIGN KEY (mcq_id) REFERENCES question_bank(id)
);
"""


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session