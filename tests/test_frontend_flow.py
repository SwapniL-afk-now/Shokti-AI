import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

async def test_frontend_user_flow(async_client: AsyncClient):
    """
    Simulates a student's full user experience step-by-step
    exactly as implemented in static/app.js.
    """
    
    # 1. Signup / Register new account
    reg_res = await async_client.post("/api/auth/register", json={
        "email": "student_flow@shokti.com",
        "password": "mypassword123",
        "name": "Flow Student"
    })
    assert reg_res.status_code == 200
    reg_data = reg_res.json()
    assert "access_token" in reg_data
    assert "refresh_token" in reg_data
    token = reg_data["access_token"]
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Get user profile details
    me_res = await async_client.get("/api/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["name"] == "Flow Student"
    
    # 3. Retrieve Subject / Catalog filters
    subj_res = await async_client.get("/api/subjects", headers=headers)
    assert subj_res.status_code == 200
    subjects = subj_res.json()
    assert len(subjects) > 0
    subject_id = subjects[0]["id"]
    
    # Get books for the first subject
    books_res = await async_client.get(f"/api/books?subject_id={subject_id}", headers=headers)
    assert books_res.status_code == 200
    books = books_res.json()
    assert len(books) > 0
    book_id = books[0]["id"]
    
    # Get chapters for that book
    ch_res = await async_client.get(f"/api/chapters?book_id={book_id}", headers=headers)
    assert ch_res.status_code == 200
    chapters = ch_res.json()
    assert len(chapters) > 0
    chapter_id = chapters[0]["chapter_id"]
    chapter_name = chapters[0]["chapter_name"]
    
    # Get topics for that chapter
    topics_res = await async_client.get(f"/api/topics?chapter_id={chapter_id}", headers=headers)
    assert topics_res.status_code == 200
    topics = topics_res.json()
    assert len(topics) > 0
    
    # 4. Launch custom practice session
    session_res = await async_client.post(
        "/api/practice/session",
        headers=headers,
        json={
            "mode": "adaptive",
            "count": 5,
            "topic_name": None,
            "chapter_name": chapter_name
        }
    )
    assert session_res.status_code == 200
    session_data = session_res.json()
    assert "session_id" in session_data
    session_id = session_data["session_id"]
    mcqs = session_data["mcqs"]
    assert len(mcqs) > 0
    
    # 5. Answer all MCQs in the practice session
    for mcq_item in mcqs:
        mcq_id = mcq_item["id"]
        
        # Get full MCQ details (question, options, etc.)
        detail_res = await async_client.get(f"/api/mcqs/{mcq_id}", headers=headers)
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert "options" in detail
        assert "A" in detail["options"]
        
        # Submit answer to session
        answer_res = await async_client.post(
            f"/api/practice/sessions/{session_id}/answer",
            headers=headers,
            json={
                "mcq_id": mcq_id,
                "selected_option": "A",
                "time_spent_seconds": 4
            }
        )
        assert answer_res.status_code == 200
        ans_data = answer_res.json()
        assert "is_correct" in ans_data
        assert "correct_option" in ans_data
        
    # 6. Retrieve analytics & heatmaps
    stats_res = await async_client.get("/api/student/stats", headers=headers)
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["total_answered"] == len(mcqs)
    
    topic_stats_res = await async_client.get("/api/student/topic-stats", headers=headers)
    assert topic_stats_res.status_code == 200
    topic_stats = topic_stats_res.json()
    assert len(topic_stats) > 0
    
    # Verify timing confidence profile API
    conf_res = await async_client.get("/api/student/confidence-profile", headers=headers)
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert "lucky_guess" in conf_data
    assert "confident_master" in conf_data
    assert "confident_mistake" in conf_data
    assert "no_knowledge" in conf_data
    
    # Verify semantic confusion clusters API
    confusion_res = await async_client.get("/api/student/confusion-clusters", headers=headers)
    assert confusion_res.status_code == 200
    confusion_data = confusion_res.json()
    assert isinstance(confusion_data, list)
    
    # Verify student timeline API
    timeline_res = await async_client.get("/api/student/timeline", headers=headers)
    assert timeline_res.status_code == 200
    timeline_data = timeline_res.json()
    assert isinstance(timeline_data, list)
    
    # 7. Start and submit a timed exam
    exams_res = await async_client.get("/api/exams", headers=headers)
    assert exams_res.status_code == 200
    exams = exams_res.json()
    assert len(exams) > 0
    
    # Assert initially the exam is not marked as completed
    assert exams[0]["is_completed"] is False
    exam_id = exams[0]["exam_id"]
    
    # Start the exam
    exam_start_res = await async_client.post(f"/api/exams/{exam_id}/start", headers=headers)
    assert exam_start_res.status_code == 200
    exam_start_data = exam_start_res.json()
    exam_mcqs = exam_start_data["mcqs"]
    assert len(exam_mcqs) > 0
    assert "session_id" in exam_start_data
    
    # Submit exam answers
    exam_answers = [{"mcq_id": q["id"], "selected_option": "B"} for q in exam_mcqs]
    submit_res = await async_client.post(
        f"/api/exams/{exam_id}/submit",
        headers=headers,
        json={
            "session_id": exam_start_data["session_id"],
            "time_taken_seconds": 42,
            "answers": exam_answers,
        }
    )
    assert submit_res.status_code == 200
    submit_data = submit_res.json()
    assert submit_data["attempt_id"]
    assert "score_percentage" in submit_data
    assert "correct" in submit_data
    assert submit_data["total"] == len(exam_mcqs)
    assert submit_data["feedback"] is None

    # 8. Verify exam status updates to is_completed == True
    post_exams_res = await async_client.get("/api/exams", headers=headers)
    assert post_exams_res.status_code == 200
    post_exams = post_exams_res.json()
    matching_exam = next(ex for ex in post_exams if ex["exam_id"] == exam_id)
    assert matching_exam["is_completed"] is True
    assert matching_exam["attempt_count"] == 1
    assert matching_exam["latest_attempt_id"] == submit_data["attempt_id"]

    attempts_res = await async_client.get(f"/api/exams/{exam_id}/attempts", headers=headers)
    assert attempts_res.status_code == 200
    assert attempts_res.json()[0]["attempt_id"] == submit_data["attempt_id"]

    # 9. Verify enhanced academic statistics populate profile/dashboard correctly
    stats_res2 = await async_client.get("/api/student/stats", headers=headers)
    assert stats_res2.status_code == 200
    stats2 = stats_res2.json()
    assert stats2["exams_taken"] == 1
    assert stats2["avg_time_seconds"] >= 0.0
    assert "strongest_topic" in stats2
    assert "weakest_topic" in stats2


async def test_practice_session_submits_like_exam_attempt(async_client: AsyncClient):
    reg_res = await async_client.post("/api/auth/register", json={
        "email": "practice_exam_flow@shokti.com",
        "password": "mypassword123",
        "name": "Practice Exam Student",
    })
    assert reg_res.status_code == 200
    headers = {"Authorization": f"Bearer {reg_res.json()['access_token']}"}

    session_res = await async_client.post(
        "/api/practice/session",
        headers=headers,
        json={"mode": "adaptive", "count": 5, "topic_name": None, "chapter_name": None},
    )
    assert session_res.status_code == 200
    session_data = session_res.json()
    mcqs = session_data["mcqs"]
    assert mcqs

    answers = []
    for item in mcqs:
        detail_res = await async_client.get(f"/api/mcqs/{item['id']}", headers=headers)
        assert detail_res.status_code == 200
        detail = detail_res.json()
        wrong_option = next(opt for opt in ["A", "B", "C", "D"] if opt != detail["correct_answer"]["option"])
        answers.append({"mcq_id": item["id"], "selected_option": wrong_option})

    submit_res = await async_client.post(
        f"/api/practice/sessions/{session_data['session_id']}/submit",
        headers=headers,
        json={
            "session_id": session_data["session_id"],
            "time_taken_seconds": 42,
            "answers": answers,
        },
    )
    assert submit_res.status_code == 200
    result = submit_res.json()
    assert result["attempt_id"]
    assert result["exam_id"].startswith("practice-")
    assert result["exam_title"] == "Practice Exam"
    assert result["time_taken_seconds"] == 42
    assert result["total"] == len(answers)
    assert result["feedback_status"] == "pending"
    assert result["feedback"] is None
    assert all("is_correct" in detail for detail in result["details"])
    assert any(detail["practice_related_questions"] for detail in result["details"] if not detail["is_correct"])

    saved_res = await async_client.get(f"/api/exams/attempts/{result['attempt_id']}", headers=headers)
    assert saved_res.status_code == 200
    saved = saved_res.json()
    assert saved["exam_id"] == result["exam_id"]
    assert saved["details"] == result["details"]
