import pytest
from httpx import AsyncClient
import sqlite3
import json
from unittest.mock import patch
import shokti.core.config
from shokti.api.schemas import ExamFeedback, WeakTopicFeedback, StrongTopicFeedback

pytestmark = pytest.mark.asyncio


async def test_gemini_feedback_and_related_questions_flow(async_client: AsyncClient):
    """
    E2E integration test:
    1. Seed related questions in SQLite test database.
    2. Mock Gemini AI feedback generation.
    3. Register, start, and submit an exam.
    4. Assert feedback and related practice questions are returned in the response.
    """
    
    # 1. Seed practice_related_questions for MCQ ID 1
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    cursor = conn.cursor()
    
    prq_data = ["প্যারেনকাইমা টিস্যুর কোষগুলো জীবিত নাকি মৃত?", "প্যারেনকাইমা টিস্যুর প্রধান কাজ কী?"]
    cursor.execute(
        "UPDATE question_bank SET practice_related_questions = ? WHERE id = ?",
        (json.dumps(prq_data), 1)
    )
    conn.commit()
    conn.close()
    
    # 2. Mock ExamFeedbackService.get_feedback
    mock_feedback = ExamFeedback(
        overall_summary="Excellent job overall, but pay attention to cell structure details.",
        weak_topics=[
            WeakTopicFeedback(
                topic_name="Cell Biology",
                chapter_name="Chapter 06",
                accuracy_percentage=40.0,
                focus_recommendations=["Review parenchymal cell walls", "Practice related MCQ sets"]
            )
        ],
        strong_topics=[
            StrongTopicFeedback(
                topic_name="Fungi",
                chapter_name="Chapter 06",
                accuracy_percentage=80.0,
                encouragement="Great grasp of fungal structures!"
            )
        ],
        personalized_study_recommendations=[
            "Spend 15 mins daily on spaced repetition cards",
            "Read textbook pages 120-125 carefully"
        ]
    )
    
    # We patch ExamFeedbackService.get_feedback
    with patch("shokti.services.exam_feedback_service.ExamFeedbackService.get_feedback", return_value=mock_feedback):
        
        # 3. Register user
        reg_res = await async_client.post("/api/auth/register", json={
            "email": "gemini_student@shokti.com",
            "password": "password123",
            "name": "Gemini Student"
        })
        assert reg_res.status_code == 200
        token = reg_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 4. Start Exam
        exams_res = await async_client.get("/api/exams", headers=headers)
        assert exams_res.status_code == 200
        exam_id = exams_res.json()[0]["exam_id"]
        
        start_res = await async_client.post(f"/api/exams/{exam_id}/start", headers=headers)
        assert start_res.status_code == 200
        
        # 5. Submit Exam answers (specifically answer MCQ 1 incorrectly to trigger related questions)
        # In conftest, correct answer for MCQ 1 is 'A' (text "It is a plant"). We submit 'B' (wrong).
        payload = [
            {"mcq_id": 1, "selected_option": "B"}
        ]
        
        submit_res = await async_client.post(
            f"/api/exams/{exam_id}/submit",
            headers=headers,
            json=payload
        )
        assert submit_res.status_code == 200
        data = submit_res.json()
        
        # 6. Verify assertions
        # A. Score assertions
        assert data["total"] == 1
        assert data["correct"] == 0
        assert data["score_percentage"] == 0.0
        
        # B. Related practice questions assertion
        assert len(data["details"]) == 1
        detail = data["details"][0]
        assert detail["mcq_id"] == 1
        assert detail["selected_option"] == "B"
        assert detail["correct_option"] == "A"
        assert detail["is_correct"] is False
        assert detail["practice_related_questions"] == prq_data
        
        # C. Gemini feedback assertion
        assert "feedback" in data
        feedback = data["feedback"]
        assert feedback is not None
        assert feedback["overall_summary"] == "Excellent job overall, but pay attention to cell structure details."
        assert len(feedback["weak_topics"]) == 1
        assert feedback["weak_topics"][0]["topic_name"] == "Cell Biology"
        assert len(feedback["strong_topics"]) == 1
        assert feedback["strong_topics"][0]["topic_name"] == "Fungi"
        assert len(feedback["personalized_study_recommendations"]) == 2


async def test_exam_submission_returns_local_feedback_when_ai_is_unavailable(async_client: AsyncClient):
    """
    Regression coverage for the student-facing submit flow:
    even when the AI feedback service cannot run, the exam response must still
    include usable analysis for the right-side results drawer.
    """
    reg_res = await async_client.post("/api/auth/register", json={
        "email": "fallback_feedback_student@shokti.com",
        "password": "password123",
        "name": "Fallback Feedback Student"
    })
    assert reg_res.status_code == 200
    headers = {"Authorization": f"Bearer {reg_res.json()['access_token']}"}

    exams_res = await async_client.get("/api/exams", headers=headers)
    assert exams_res.status_code == 200
    exam_id = exams_res.json()[0]["exam_id"]

    payload = [
        {"mcq_id": 1, "selected_option": "B"},
        {"mcq_id": 2, "selected_option": "A"},
    ]

    with patch("shokti.api.routers.exams._load_gemini_api_key", side_effect=RuntimeError("no key")):
        submit_res = await async_client.post(
            f"/api/exams/{exam_id}/submit",
            headers=headers,
            json=payload,
        )

    assert submit_res.status_code == 200
    data = submit_res.json()
    assert data["total"] == 2
    assert data["correct"] == 1
    assert data["details"][0]["is_correct"] is False
    assert data["details"][0]["correct_option"] == "A"
    assert data["feedback"] is not None
    assert data["feedback"]["overall_summary"]
    assert data["feedback"]["weak_topics"]
    assert data["feedback"]["personalized_study_recommendations"]


async def test_confidence_profile_endpoint(async_client: AsyncClient):
    """
    Test the /api/student/confidence-profile endpoint:
    1. Register a student.
    2. Seed answers with different correctness values and response times.
    3. Assert correct mapping of:
       - lucky_guess (correct, <= median)
       - confident_master (correct, > median)
       - confident_mistake (wrong, <= median)
       - no_knowledge (wrong, > median)
    """
    # 1. Register student
    reg_res = await async_client.post("/api/auth/register", json={
        "email": "confidence_student@shokti.com",
        "password": "password123",
        "name": "Confidence Student"
    })
    assert reg_res.status_code == 200
    token = reg_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Retrieve student ID
    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM students WHERE email = ?", ("confidence_student@shokti.com",))
    student_id = cursor.fetchone()[0]

    # 2. Seed student_answer_log
    # Sorted times: [4, 5, 10, 15], median: 10
    # Correct <= 10: 4, 10 -> lucky_guess = 2
    # Correct > 10: None -> confident_master = 0
    # Wrong <= 10: 5 -> confident_mistake = 1
    # Wrong > 10: 15 -> no_knowledge = 1
    cursor.executemany("""
        INSERT INTO student_answer_log (student_id, mcq_id, is_correct, time_spent_seconds)
        VALUES (?, ?, ?, ?)
    """, [
        (student_id, 1, 1, 4),
        (student_id, 2, 1, 10),
        (student_id, 3, 0, 5),
        (student_id, 4, 0, 15)
    ])
    conn.commit()
    conn.close()

    # 3. Call endpoint and assert
    profile_res = await async_client.get("/api/student/confidence-profile", headers=headers)
    assert profile_res.status_code == 200
    profile_data = profile_res.json()
    assert profile_data["lucky_guess"] == 2
    assert profile_data["confident_master"] == 0
    assert profile_data["confident_mistake"] == 1
    assert profile_data["no_knowledge"] == 1
