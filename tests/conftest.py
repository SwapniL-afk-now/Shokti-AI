import os
import sqlite3
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

@pytest.fixture(scope="session", autouse=True)
def setup_test_db(tmp_path_factory):
    # Create a temporary file for the database
    db_path = tmp_path_factory.mktemp("db") / "test_shokti.db"
    
    # Set the environment variable BEFORE any app modules are loaded
    # However, since config might be imported already, we need to mock it.
    os.environ["DB_PATH_OVERRIDE"] = str(db_path)
    
    # Also directly patch the config module if it was already imported
    import shokti.core.config
    shokti.core.config.DB_PATH = str(db_path)
    import shokti.api.db
    shokti.api.db.DB_PATH = str(db_path)
    shokti.api.db.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
    
    # Patch all other analytical/CLI modules that access DB_PATH
    import shokti.bloom_classifier
    shokti.bloom_classifier.DB_PATH = str(db_path)
    import shokti.distractor_analysis
    shokti.distractor_analysis.DB_PATH = str(db_path)
    import shokti.confusion_map
    shokti.confusion_map.DB_PATH = str(db_path)
    import shokti.evolution.simulator
    shokti.evolution.simulator.DB_PATH = str(db_path)
    
    # Initialize the tables synchronously
    conn = sqlite3.connect(str(db_path))
    
    # Create the tables matching SQLAlchemy and raw SQL
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subjects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            display_name TEXT,
            language TEXT,
            gemini_store_name TEXT,
            gemini_store_display_name TEXT,
            sort_order INTEGER
        );
        CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY,
            subject_id TEXT,
            title TEXT NOT NULL,
            source_file TEXT,
            chapter_count INTEGER,
            sort_order INTEGER,
            FOREIGN KEY(subject_id) REFERENCES subjects(id)
        );
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            created_at TIMESTAMP,
            last_active_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id)
        );
        CREATE TABLE IF NOT EXISTS question_bank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            book_id TEXT,
            chapter_id TEXT,
            chapter_name TEXT,
            book_page_range TEXT,
            source_file TEXT,
            topic_id TEXT,
            topic_name TEXT,
            question TEXT,
            options TEXT,
            correct_answer TEXT,
            source_quote TEXT,
            pdf_page_number INTEGER,
            practice_related_questions TEXT,
            appearance_counter INTEGER DEFAULT 0,
            question_hash TEXT UNIQUE,
            difficulty TEXT DEFAULT 'medium',
            origin TEXT DEFAULT 'question_bank',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
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
        CREATE TABLE IF NOT EXISTS exam_attempts (
            attempt_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            exam_id TEXT NOT NULL,
            exam_title TEXT NOT NULL,
            exam_kind TEXT DEFAULT 'fixed_model_test',
            session_id TEXT NOT NULL,
            total INTEGER DEFAULT 0,
            correct INTEGER DEFAULT 0,
            score_percentage REAL DEFAULT 0,
            time_taken_seconds INTEGER DEFAULT 0,
            answers_json TEXT NOT NULL,
            details_json TEXT NOT NULL,
            topic_breakdown_json TEXT NOT NULL,
            feedback_status TEXT DEFAULT 'pending',
            feedback_source TEXT,
            feedback_error TEXT,
            feedback_json TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            feedback_updated_at TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students(id)
        );
        CREATE INDEX IF NOT EXISTS idx_exam_attempts_student_exam
        ON exam_attempts(student_id, exam_id, submitted_at);
        CREATE TABLE IF NOT EXISTS topic_sampling_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            student_id TEXT,
            session_type TEXT,
            chapter_id TEXT,
            topic_id TEXT,
            times_sampled INTEGER DEFAULT 0,
            session_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Insert some mock MCQs for the exam tests
    conn.executescript("""
        INSERT INTO subjects (id, name, display_name, language, sort_order)
        VALUES ('sub1', 'Biology', 'জীববিজ্ঞান', 'bn', 1);

        INSERT INTO books (id, subject_id, title, sort_order)
        VALUES ('book1', 'sub1', 'Biology 1st Paper', 1);

        INSERT INTO question_bank (id, subject, book_id, chapter_id, chapter_name, topic_id, topic_name, question, options, correct_answer, difficulty, origin)
        VALUES 
        (1, 'Biology', 'book1', '06', 'Chapter 06', 'T1', 'ব্রায়োফাইটা', 'What is Riccia?', '{"A": "Plant", "B": "Animal", "C": "Fungi", "D": "Virus"}', '{"option": "A", "text": "It is a plant"}', 'easy', 'question_bank'),
        (2, 'Biology', 'book1', '06', 'Chapter 06', 'T1', 'ব্রায়োফাইটা', 'What is Pteris?', '{"A": "Plant", "B": "Animal", "C": "Fungi", "D": "Virus"}', '{"option": "A", "text": "It is a fern"}', 'medium', 'question_bank'),
        (3, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia', 'Explain Riccia shape?', '{"A": "Round", "B": "Flat", "C": "Square", "D": "Star"}', '{"option": "B", "text": "It is flat"}', 'hard', 'question_bank'),
        (4, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia', 'Identify the structure of (Archegonium) female organ.', '{"A": "Egg", "B": "Sperm", "C": "Spore", "D": "Seed"}', '{"option": "A", "text": "Egg"}', 'medium', 'question_bank'),
        (5, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia', 'Recall definition of (Antheridium) male organ.', '{"A": "Sperm", "B": "Egg", "C": "Spore", "D": "Seed"}', '{"option": "A", "text": "Sperm"}', 'medium', 'question_bank'),
        (6, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia', 'Compare Archegonium and Antheridium characteristics.', '{"A": "Reproduction", "B": "Growth", "C": "Respiration", "D": "Excretion"}', '{"option": "A", "text": "Reproduction"}', 'hard', 'question_bank'),
        (7, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia', 'What is the function of Archegonium?', '{"A": "Egg production", "B": "Sperm production", "C": "Water absorption", "D": "Food storage"}', '{"option": "A", "text": "Egg production"}', 'easy', 'question_bank'),
        (8, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia', 'What is the function of Antheridium?', '{"A": "Sperm production", "B": "Egg production", "C": "Water absorption", "D": "Food storage"}', '{"option": "A", "text": "Sperm production"}', 'easy', 'question_bank'),
        (9, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia', 'Calculate Riccia growth rate under light.', '{"A": "Fast", "B": "Slow", "C": "None", "D": "Variable"}', '{"option": "A", "text": "Fast"}', 'medium', 'question_bank'),
        (10, 'Biology', 'book1', '06', 'Chapter 06', 'T1', 'টেরিডোফাইটা', 'Calculate Pteris leaf area index.', '{"A": "High", "B": "Low", "C": "Medium", "D": "Zero"}', '{"option": "A", "text": "High"}', 'hard', 'question_bank'),
        (11, 'Biology', 'book1', '06', 'Chapter 06', 'T1', 'টেরিডোফাইটা', 'Explain Archegonium in Pteris.', '{"A": "Structure", "B": "Function", "C": "Form", "D": "None"}', '{"option": "A", "text": "Structure"}', 'medium', 'question_bank'),
        (12, 'Biology', 'book1', '06', 'Chapter 06', 'T1', 'টেরিডোফাইটা', 'Explain Antheridium in Pteris.', '{"A": "Structure", "B": "Function", "C": "Form", "D": "None"}', '{"option": "A", "text": "Structure"}', 'medium', 'question_bank');
    """)
    # Add dummy student
    conn.execute(
        "INSERT INTO students (id, email, password_hash, name) VALUES (?, ?, ?, ?)",
        ("S1", "test@test.com", "dummyhash", "Test Student")
    )
    # Seed wrong answers for S1 in Riccia to trigger weak topics and confusion map
    conn.executescript("""
        INSERT INTO student_answer_log (student_id, mcq_id, is_correct, selected_option, session_type)
        VALUES
        ('S1', 4, 0, 'B', 'adaptive'),
        ('S1', 5, 0, 'B', 'adaptive'),
        ('S1', 6, 0, 'B', 'adaptive'),
        ('S1', 7, 0, 'B', 'adaptive'),
        ('S1', 8, 0, 'B', 'adaptive');
    """)
    conn.commit()
    conn.close()
    
    # Also ensure async engine is pointing to the new DB URL
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    shokti.api.db.engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    shokti.api.db.async_session = async_sessionmaker(shokti.api.db.engine, class_=AsyncSession, expire_on_commit=False)

    yield str(db_path)

@pytest_asyncio.fixture
async def async_client():
    from shokti.api.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
