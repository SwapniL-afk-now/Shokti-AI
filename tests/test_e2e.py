import pytest
from httpx import AsyncClient
import sqlite3
import datetime
import json
from unittest.mock import patch

# Import CLI modules for testing
from shokti.core.config import MCQ
from shokti.question_selectors.session_builder import build_session, _build_weakness_session
from shokti.generators.gap_filler import build_generation_prompt
from shokti.sampling_weights import get_combined_weights
from shokti.exams.exam_runner import run_exam
from shokti.spaced_repetition import update_review_date, get_due_mcqs
from shokti.bloom_classifier import classify_mcq, get_topic_bloom_profile
from shokti.distractor_analysis import classify_distractor, analyze_distractors
from shokti.confusion_map import detect_confusion_clusters, extract_key_terms
from shokti.evolution.simulator import SimulatedStudent
from shokti.evolution.optimizer import ConfigOptimizer
from shokti.evolution.models import FitnessResult

pytestmark = pytest.mark.asyncio


# ==========================================
# 1. CORE AUTHENTICATION FLOW
# ==========================================

async def test_auth_register_happy_path(async_client: AsyncClient):
    res = await async_client.post("/api/auth/register", json={
        "email": "user1@test.com",
        "password": "securepassword123",
        "name": "User One"
    })
    assert res.status_code == 200
    assert "access_token" in res.json()
    assert "refresh_token" in res.json()

async def test_auth_register_duplicate(async_client: AsyncClient):
    # Register first
    await async_client.post("/api/auth/register", json={
        "email": "dup@test.com",
        "password": "securepassword123",
        "name": "Duplicate"
    })
    # Register again with same email
    res = await async_client.post("/api/auth/register", json={
        "email": "dup@test.com",
        "password": "securepassword123",
        "name": "Duplicate Two"
    })
    assert res.status_code == 409
    assert "already registered" in res.json()["detail"].lower()

async def test_auth_login_happy_path(async_client: AsyncClient):
    # Register first
    await async_client.post("/api/auth/register", json={
        "email": "login1@test.com",
        "password": "correctpassword",
        "name": "Login User"
    })
    # Login
    res = await async_client.post("/api/auth/login", json={
        "email": "login1@test.com",
        "password": "correctpassword"
    })
    assert res.status_code == 200
    assert "access_token" in res.json()
    assert "refresh_token" in res.json()

async def test_auth_login_bad_credentials(async_client: AsyncClient):
    # Wrong password
    res = await async_client.post("/api/auth/login", json={
        "email": "login1@test.com",
        "password": "wrongpassword"
    })
    assert res.status_code == 401
    assert "invalid credentials" in res.json()["detail"].lower()

async def test_auth_refresh(async_client: AsyncClient):
    reg = await async_client.post("/api/auth/register", json={
        "email": "refresh@test.com",
        "password": "password123",
        "name": "Refresh User"
    })
    refresh_token = reg.json()["refresh_token"]
    res = await async_client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert res.status_code == 200
    assert "access_token" in res.json()

async def test_auth_me_endpoint(async_client: AsyncClient):
    # Without token
    res = await async_client.get("/api/auth/me")
    assert res.status_code == 401
    
    # With token
    reg = await async_client.post("/api/auth/register", json={
        "email": "me@test.com",
        "password": "password123",
        "name": "Me User"
    })
    token = reg.json()["access_token"]
    res = await async_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["email"] == "me@test.com"


# ==========================================
# 2. CATALOG METADATA NAVIGATION
# ==========================================

async def test_catalog_subjects(async_client: AsyncClient):
    res = await async_client.get("/api/subjects")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
    assert len(res.json()) >= 1
    assert res.json()[0]["id"] == "sub1"

async def test_catalog_books(async_client: AsyncClient):
    res = await async_client.get("/api/books")
    assert res.status_code == 200
    assert len(res.json()) >= 1
    assert res.json()[0]["id"] == "book1"

async def test_catalog_chapters(async_client: AsyncClient):
    res = await async_client.get("/api/chapters")
    assert res.status_code == 200
    assert len(res.json()) >= 1
    assert res.json()[0]["chapter_id"] == "06"

async def test_catalog_topics(async_client: AsyncClient):
    res = await async_client.get("/api/topics")
    assert res.status_code == 200
    assert len(res.json()) >= 2


# ==========================================
# 3. MCQ CATALOG ENGINE
# ==========================================

async def test_mcqs_filtering(async_client: AsyncClient):
    res = await async_client.get("/api/mcqs?difficulty=hard")
    assert res.status_code == 200
    items = res.json()
    assert all(item["difficulty"] == "hard" for item in items)

async def test_mcq_detail_decodes(async_client: AsyncClient):
    res = await async_client.get("/api/mcqs/1")
    assert res.status_code == 200
    data = res.json()
    assert data["options"]["A"] == "Plant"
    assert data["correct_answer"]["option"] == "A"


# ==========================================
# 4. INTERACTIVE EXAM SERVICE
# ==========================================

async def test_exams_flow(async_client: AsyncClient):
    reg = await async_client.post("/api/auth/register", json={
        "email": "examflow@test.com",
        "password": "password123",
        "name": "Exam Flow User"
    })
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    
    # List Exams
    res = await async_client.get("/api/exams")
    assert res.status_code == 200
    assert len(res.json()) > 0
    exam_id = res.json()[0]["exam_id"]
    
    # Start Exam
    res = await async_client.post(f"/api/exams/{exam_id}/start", headers=headers)
    assert res.status_code == 200
    
    # Submit Exam
    res = await async_client.post(f"/api/exams/{exam_id}/submit", headers=headers, json=[
        {"mcq_id": 1, "selected_option": "A"},
        {"mcq_id": 2, "selected_option": "B"} # Wrong option
    ])
    assert res.status_code == 200
    assert res.json()["total"] == 2
    assert res.json()["correct"] == 1


# ==========================================
# 5. PRACTICE ROUTER & SESSION BUILDER
# ==========================================

async def test_practice_modes(async_client: AsyncClient):
    reg = await async_client.post("/api/auth/register", json={
        "email": "practiceflow@test.com",
        "password": "password123",
        "name": "Practice User"
    })
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    
    for mode in ["adaptive", "exam"]:
        res = await async_client.post("/api/practice/session", headers=headers, json={
            "mode": mode,
            "count": 5
        })
        assert res.status_code == 200
        assert "session_id" in res.json()


# ==========================================
# 6. SPACED REPETITION SCHEDULER (SM-2)
# ==========================================

def test_spaced_repetition_intervals():
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    
    # Initial state
    update_review_date(conn, "S1", 1, is_correct=True, quality=5)
    cursor = conn.cursor()
    cursor.execute("SELECT easiness_factor, interval_days FROM student_mcq_stats WHERE student_id='S1' AND mcq_id=1")
    ef, interval = cursor.fetchone()
    assert float(ef) > MCQ.SM2_INITIAL_EF
    assert int(interval) >= 1
    
    # Repeat correct to escalate interval
    update_review_date(conn, "S1", 1, is_correct=True, quality=5)
    cursor.execute("SELECT interval_days FROM student_mcq_stats WHERE student_id='S1' AND mcq_id=1")
    interval_2 = cursor.fetchone()[0]
    assert interval_2 >= interval
    
    # Negative test: blackout reset
    update_review_date(conn, "S1", 1, is_correct=False, quality=1)
    cursor.execute("SELECT easiness_factor, interval_days FROM student_mcq_stats WHERE student_id='S1' AND mcq_id=1")
    new_ef, new_interval = cursor.fetchone()
    assert new_interval == 1
    assert float(new_ef) < float(ef)
    conn.close()

def test_spaced_repetition_due_mcqs():
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    # Set next_review_at in the past
    cursor = conn.cursor()
    past_date = (datetime.datetime.now() - datetime.timedelta(days=5)).isoformat()
    cursor.execute("""
        UPDATE student_mcq_stats 
        SET next_review_at = ? 
        WHERE student_id='S1' AND mcq_id=1
    """, (past_date,))
    conn.commit()
    
    due = get_due_mcqs(conn, "S1")
    assert len(due) >= 1
    assert due[0]["mcq_id"] == 1
    conn.close()


# ==========================================
# 7. BLOOM'S TAXONOMY CLASSIFIER
# ==========================================

def test_bloom_classification_lexical():
    assert classify_mcq("What is the definition of Riccia?") == "Remember"
    assert classify_mcq("Explain the structure of Riccia.") == "Understand"
    assert classify_mcq("Calculate the length of the sporophyte.") == "Apply"

def test_bloom_topic_profile():
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    profile = get_topic_bloom_profile(conn)
    assert "Riccia" in profile
    assert "ব্রায়োফাইটা" in profile
    conn.close()


# ==========================================
# 8. DISTRACTOR ENTROPY ANALYSIS
# ==========================================

def test_distractor_classification():
    assert classify_distractor("all living things", "A", "What is Riccia?") == "too_broad"
    assert classify_distractor("only spores", "A", "What is Riccia?") == "too_narrow"
    assert classify_distractor("archegonium", "antheridium", "What is Riccia?") == "concept_confused"

def test_distractor_entropy_profile():
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    guide = analyze_distractors(conn)
    # Since we seeded wrong selections for S1 in Riccia
    assert "Riccia" in guide
    assert guide["Riccia"]["wrong_answer_count"] == 5
    assert guide["Riccia"]["dominant_distractor_type"] is not None
    conn.close()


# ==========================================
# 9. SEMANTIC CONFUSION MAP
# ==========================================

def test_semantic_confusion_clustering():
    # Detect confusion using biological terms seeded in conftest ("Archegonium" and "Antheridium")
    # For student S1 who has 5 wrong answers in Riccia
    clusters = detect_confusion_clusters("S1", min_wrong=5)
    assert len(clusters) >= 1
    # Check if confusion mapped to expected biological terms
    assert "Archegonium" in clusters[0]["explanation"] or "Antheridium" in clusters[0]["explanation"]


def test_confidence_risk_prioritizes_weakness_selection(monkeypatch):
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        INSERT OR IGNORE INTO students (id, email, password_hash, name)
        VALUES ('RiskS', 'risk@test.com', 'hash', 'Risk Student');
        INSERT INTO student_answer_log
            (student_id, mcq_id, is_correct, time_spent_seconds, confidence_rating, session_type)
        VALUES
            ('RiskS', 1, 0, 5, NULL, 'exam1'),
            ('RiskS', 3, 0, 5, 3, 'exam1');
    """)
    conn.commit()

    monkeypatch.setattr("shokti.question_selectors.session_builder.random.random", lambda: 0.5)
    selected, _ = _build_weakness_session(conn, "RiskS", count=1)
    conn.close()

    assert selected[0]["topic_id"] == "T2"


def test_practice_mix_config_defaults():
    assert MCQ.QBANK_RATIO == 0.40
    assert MCQ.GENERATED_RATIO == 0.20
    assert MCQ.WEAK_TOPIC_RATIO == 0.25
    assert MCQ.FRESH_GENERATED_RATIO == 0.15
    assert MCQ.ENABLE_FRESH_GENERATION_IN_PRACTICE is True


def test_adaptive_practice_uses_configured_mix(monkeypatch):
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    conn.row_factory = sqlite3.Row

    for i in range(13, 33):
        origin = "generated" if i < 21 else "question_bank"
        conn.execute("""
            INSERT INTO question_bank
                (id, subject, book_id, chapter_id, chapter_name, topic_id, topic_name,
                 question, options, correct_answer, difficulty, origin)
            VALUES (?, 'Biology', 'book1', '07', 'Chapter 07', 'T3', 'জিমনোস্পার্ম',
                    ?, '{"A":"A","B":"B","C":"C","D":"D"}',
                    '{"option":"A","text":"A"}', 'medium', ?)
        """, (i, f"Seeded question {i}", origin))
    for i in range(33, 61):
        conn.execute("""
            INSERT INTO question_bank
                (id, subject, book_id, chapter_id, chapter_name, topic_id, topic_name,
                 question, options, correct_answer, difficulty, origin)
            VALUES (?, 'Biology', 'book1', '06', 'Chapter 06', 'T2', 'Riccia',
                    ?, '{"A":"A","B":"B","C":"C","D":"D"}',
                    '{"option":"A","text":"A"}', 'medium', 'question_bank')
        """, (i, f"Weak seeded question {i}"))
    conn.commit()

    fresh = [dict(r) for r in conn.execute(
        "SELECT * FROM question_bank WHERE origin='generated' ORDER BY id DESC LIMIT 3"
    ).fetchall()]
    monkeypatch.setattr(
        "shokti.question_selectors.session_builder._generate_fresh_for_session",
        lambda *args, **kwargs: fresh,
    )

    selected, comp = build_session(conn, "S1", count=20, mode="adaptive")
    conn.close()

    assert len(selected) == 20
    assert comp.qbank_count == 8
    assert comp.generated_count == 4
    assert comp.weak_topic_count == 5
    assert comp.fresh_generated_count == 3


def test_adaptive_practice_falls_back_when_fresh_generation_fails(monkeypatch):
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(
        "shokti.question_selectors.session_builder._generate_fresh_for_session",
        lambda *args, **kwargs: [],
    )

    selected, comp = build_session(conn, "S1", count=8, mode="adaptive")
    conn.close()

    assert len(selected) == 8
    assert comp.fresh_generated_count == 0


def test_fresh_generation_prompt_includes_practice_context():
    topic_data = {
        "topic_name": "Riccia",
        "chapter_id": "06",
        "chapter_name": "Chapter 06",
        "book_page_range": "76-85",
        "source_file": "biology_1st.pdf",
    }
    context = {
        "target_session_mode": "adaptive",
        "target_bucket": "fresh_generated",
        "selected_filters": {"topic": "Riccia", "chapter": "Chapter 06"},
        "target_topic_reason": "confident mistake",
        "requested_mix_percentages": {"qbank": 0.4, "generated": 0.2, "weak": 0.25, "fresh": 0.15},
        "student_topic_stats": [{"topic_name": "Riccia", "accuracy": 0.2, "confident_mistakes": 2}],
        "nearby_qbank_examples": [{"question": "Qbank example"}],
        "nearby_stored_generated_examples": [{"question": "Generated example"}],
        "weak_confidence_risk_examples": [{"question": "Missed example"}],
        "coverage_gap_context": [{"topic_name": "Pteris", "mcq_count": 1}],
    }

    prompt = build_generation_prompt(topic_data, [{"question": "Style example"}], count=3, practice_context=context)

    assert "practice_session_context" in prompt
    assert "fresh_generated" in prompt
    assert "requested_mix_percentages" in prompt
    assert "nearby_qbank_examples" in prompt
    assert "weak_confidence_risk_examples" in prompt
    assert "Use Gemini File Search" in prompt
    assert "Do not duplicate" in prompt


def test_generated_mcq_schema_requires_question_and_options():
    from shokti.generators.gap_filler import GeneratedMCQResponse

    payload = {
        "topic": "Riccia",
        "number_of_mcqs": 1,
        "mcqs": [{
            "id": 1,
            "chapter": "Chapter 06",
            "topic": "Riccia",
            "source_file": "biology_1st.pdf",
            "book_page_range": "76-85",
            "question": "Which structure produces sperm in Riccia?",
            "options": {"A": "Antheridium", "B": "Archegonium", "C": "Capsule", "D": "Rhizoid"},
            "correct_answer": {"option": "A", "text": "Antheridium"},
            "explanation": "Antheridium is the male reproductive organ.",
            "difficulty": "medium",
        }],
    }

    parsed = GeneratedMCQResponse.model_validate(payload)
    mcq = parsed.mcqs[0]

    assert mcq.question
    assert mcq.options.A
    assert mcq.options.B
    assert mcq.options.C
    assert mcq.options.D
    assert mcq.correct_answer.option == "A"


def test_fresh_generated_mcqs_are_stored_as_generated_origin():
    import shokti.core.config
    from shokti.generators.gap_filler import insert_mcqs

    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    conn.row_factory = sqlite3.Row
    topic_data = {
        "subject": "Biology",
        "book_id": "book1",
        "chapter_id": "06",
        "chapter_name": "Chapter 06",
        "book_page_range": "76-85",
        "source_file": "biology_1st.pdf",
        "topic_id": "T2",
        "topic_name": "Riccia",
    }
    mcqs = [{
        "question": "Fresh generated persistence test question",
        "options": {"A": "Antheridium", "B": "Archegonium", "C": "Capsule", "D": "Rhizoid"},
        "correct_answer": {"option": "A", "text": "Antheridium"},
        "source_quote": "Antheridium is the male reproductive organ.",
        "pdf_page_number": 77,
        "practice_related_questions": ["Related Riccia question"],
        "difficulty": "medium",
    }]

    inserted = insert_mcqs(conn, mcqs, topic_data)
    row = conn.execute(
        "SELECT origin, options, correct_answer FROM question_bank WHERE question = ?",
        ("Fresh generated persistence test question",),
    ).fetchone()
    conn.close()

    assert inserted == 1
    assert row["origin"] == "generated"
    assert json.loads(row["options"])["A"] == "Antheridium"
    assert json.loads(row["correct_answer"])["option"] == "A"


# ==========================================
# 10. AI SIMULATED STUDENT & OPTIMIZERS
# ==========================================

def test_simulated_student_learning_curve():
    import shokti.core.config
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    conn.row_factory = sqlite3.Row
    profile = {
        "seed": 42,
        "topic_accuracy": {
            ("06", "T1"): 0.5,
            ("06", "T2"): 0.5,
        }
    }
    student = SimulatedStudent("Sim1", profile, conn)
    # Check initial growth
    assert student.growth == 0.0
    
    # Run a checkin
    acc = student.run_checkin(day=1)
    assert 0.0 <= acc <= 1.0
    conn.close()

def test_config_optimizer_proposals():
    optimizer = ConfigOptimizer(seed=42)
    initial_config = {
        "WEAK_THRESHOLD": 0.50,
        "QBANK_RATIO": 0.50,
        "GENERATED_RATIO": 0.20,
        "WEAK_TOPIC_RATIO": 0.30,
        "GAP_THRESHOLD": 15,
        "WEAKNESS_WEIGHT": 0.40,
        "DEBT_WEIGHT": 0.30,
        "IMPORTANCE_WEIGHT": 0.30,
        "SM2_INITIAL_EF": 2.50,
    }
    optimizer.initialize(initial_config, fitness=0.5)
    
    # Propose new config
    proposed = optimizer.propose(cycle=2)
    assert isinstance(proposed, dict)
    assert "GAP_THRESHOLD" in proposed
    
    # Update with a simulated fitness result
    result = FitnessResult(
        config=proposed,
        fitness=0.6,
        fitness_std=0.02,
        student_growths=[0.6],
        student_ids=["S1"],
        duration=1.5,
        timestamp="2026-05-20T00:00:00Z"
    )
    improved = optimizer.update(result)
    assert improved == True
    assert optimizer.best_fitness == 0.6


# ==========================================
# 11. LLM GENERATION ROUTER
# ==========================================

async def test_generation_router_flow(async_client: AsyncClient):
    # Register/Login
    reg = await async_client.post("/api/auth/register", json={
        "email": "gen@test.com",
        "password": "password123",
        "name": "Gen User"
    })
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    
    # Mock the long generation engine
    with patch("shokti.generators.gap_filler.generate_fresh_mcqs", return_value=5), \
         patch("shokti.generators.gap_filler.setup_generator", return_value=(None, "store", {}, {})):
        # Trigger Generation
        res = await async_client.post("/api/generate/topic", headers=headers, json={
            "topic_name": "Riccia",
            "chapter_id": "06",
            "chapter_name": "Chapter 06",
            "book_page_range": "76-85",
            "source_file": "biology_1st.pdf",
            "count": 5
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        assert res.json()["status"] == "pending"
        
        # Wait a brief moment to allow background tasks thread to run or check status
        res = await async_client.get(f"/api/generate/status/{job_id}", headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] in ["pending", "running", "done"]
