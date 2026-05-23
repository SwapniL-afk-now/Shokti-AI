import pytest
from httpx import AsyncClient
import sqlite3
import json
import asyncio
from unittest.mock import patch
import shokti.core.config
from shokti.api.schemas import ExamFeedback, WeakTopicFeedback, StrongTopicFeedback

pytestmark = pytest.mark.asyncio


async def wait_for_feedback(async_client: AsyncClient, attempt_id: str, headers: dict, retries: int = 20):
    for _ in range(retries):
        feedback_res = await async_client.get(
            f"/api/exams/attempts/{attempt_id}/feedback",
            headers=headers,
        )
        assert feedback_res.status_code == 200
        data = feedback_res.json()
        if data["feedback_status"] == "ready":
            return data
        await asyncio.sleep(0.05)
    return data


async def test_gemini_feedback_and_related_questions_flow(async_client: AsyncClient):
    """
    E2E integration test:
    1. Seed related questions in SQLite test database.
    2. Mock Gemini AI feedback generation.
    3. Register, start, and submit an exam.
    4. Assert scoring returns immediately, then stored feedback is reviewable.
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
        assert [item["id"] for item in start_res.json()["mcqs"][:8]] == [1, 2, 3, 4, 5, 6, 7, 8]
        
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
        assert data["attempt_id"]
        assert data["feedback_status"] == "pending"
        assert data["feedback"] is None
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
        
        # C. Gemini feedback is stored for the saved attempt and can be reviewed later.
        feedback_data = await wait_for_feedback(async_client, data["attempt_id"], headers)
        assert feedback_data["feedback_status"] == "ready"
        feedback = feedback_data["feedback"]
        assert feedback is not None
        assert feedback["overall_summary"] == "Excellent job overall, but pay attention to cell structure details."
        assert len(feedback["weak_topics"]) == 1
        assert feedback["weak_topics"][0]["topic_name"] == "Cell Biology"
        assert len(feedback["strong_topics"]) == 1
        assert feedback["strong_topics"][0]["topic_name"] == "Fungi"
        assert len(feedback["personalized_study_recommendations"]) == 2

        attempts_res = await async_client.get(f"/api/exams/{exam_id}/attempts", headers=headers)
        assert attempts_res.status_code == 200
        attempts = attempts_res.json()
        assert attempts[0]["attempt_id"] == data["attempt_id"]

        attempt_detail_res = await async_client.get(f"/api/exams/attempts/{data['attempt_id']}", headers=headers)
        assert attempt_detail_res.status_code == 200
        attempt_detail = attempt_detail_res.json()
        assert attempt_detail["feedback"]["overall_summary"] == mock_feedback.overall_summary
        assert attempt_detail["details"][0]["practice_related_questions"] == prq_data


async def test_exam_submission_returns_local_feedback_when_ai_is_unavailable(async_client: AsyncClient):
    """
    Regression coverage for the student-facing submit flow:
    even when the AI feedback service cannot run, the saved attempt must still
    receive usable fallback analysis for the right-side results panel.
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
    assert data["feedback"] is None

    feedback_data = await wait_for_feedback(async_client, data["attempt_id"], headers)
    assert feedback_data["feedback_status"] == "ready"
    assert feedback_data["feedback_source"] == "local_fallback"
    assert feedback_data["feedback"]["overall_summary"]
    assert feedback_data["feedback"]["weak_topics"]
    assert feedback_data["feedback"]["personalized_study_recommendations"]


async def test_wrong_answer_gets_related_practice_fallback_when_not_seeded(async_client: AsyncClient):
    reg_res = await async_client.post("/api/auth/register", json={
        "email": "related_fallback_student@shokti.com",
        "password": "password123",
        "name": "Related Fallback Student"
    })
    assert reg_res.status_code == 200
    headers = {"Authorization": f"Bearer {reg_res.json()['access_token']}"}

    exams_res = await async_client.get("/api/exams", headers=headers)
    exam_id = exams_res.json()[0]["exam_id"]

    conn = sqlite3.connect(shokti.core.config.DB_PATH)
    conn.execute("UPDATE question_bank SET practice_related_questions = ? WHERE id = ?", ("[]", 1))
    conn.commit()
    conn.close()

    with patch("shokti.api.routers.exams._load_gemini_api_key", side_effect=RuntimeError("no key")):
        submit_res = await async_client.post(
            f"/api/exams/{exam_id}/submit",
            headers=headers,
            json=[{"mcq_id": 1, "selected_option": "B"}],
        )

    assert submit_res.status_code == 200
    detail = submit_res.json()["details"][0]
    assert detail["is_correct"] is False
    assert detail["practice_related_questions"]
    assert any("Pteris" in item or "Riccia" in item for item in detail["practice_related_questions"])


async def test_multiple_attempts_are_saved_newest_first(async_client: AsyncClient):
    reg_res = await async_client.post("/api/auth/register", json={
        "email": "attempt_history_student@shokti.com",
        "password": "password123",
        "name": "Attempt History Student"
    })
    assert reg_res.status_code == 200
    headers = {"Authorization": f"Bearer {reg_res.json()['access_token']}"}

    exams_res = await async_client.get("/api/exams", headers=headers)
    exam_id = exams_res.json()[0]["exam_id"]

    payload_1 = {"session_id": "session-one", "time_taken_seconds": 11, "answers": [{"mcq_id": 1, "selected_option": "B"}]}
    payload_2 = {"session_id": "session-two", "time_taken_seconds": 7, "answers": [{"mcq_id": 1, "selected_option": "A"}]}

    with patch("shokti.api.routers.exams._load_gemini_api_key", side_effect=RuntimeError("no key")):
        first = await async_client.post(f"/api/exams/{exam_id}/submit", headers=headers, json=payload_1)
        second = await async_client.post(f"/api/exams/{exam_id}/submit", headers=headers, json=payload_2)

    assert first.status_code == 200
    assert second.status_code == 200
    attempts_res = await async_client.get(f"/api/exams/{exam_id}/attempts", headers=headers)
    assert attempts_res.status_code == 200
    attempts = attempts_res.json()
    assert [item["attempt_id"] for item in attempts[:2]] == [
        second.json()["attempt_id"],
        first.json()["attempt_id"],
    ]
    assert attempts[0]["time_taken_seconds"] == 7


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
