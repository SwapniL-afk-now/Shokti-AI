"""Pydantic schemas for all request/response models."""
from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator
from datetime import datetime
from typing import Annotated


# ── Auth ──────────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class StudentResponse(BaseModel):
    id: str
    email: str
    name: str | None
    created_at: datetime
    last_active_at: datetime | None = None

    class Config:
        from_attributes = True


# ── Catalog ───────────────────────────────────────────────────────────────────


class SubjectResponse(BaseModel):
    id: str
    name: str
    display_name: str | None
    language: str
    gemini_store_name: str | None
    gemini_store_display_name: str | None
    sort_order: int = 0

    class Config:
        from_attributes = True


class BookResponse(BaseModel):
    id: str
    subject_id: str
    title: str
    source_file: str | None
    chapter_count: int | None
    sort_order: int = 0

    class Config:
        from_attributes = True


class ChapterBasicResponse(BaseModel):
    chapter_id: str
    chapter_name: str
    book_page_range: str | None
    source_file: str | None
    mcq_count: int = 0

    class Config:
        from_attributes = True


class ChapterDetailResponse(ChapterBasicResponse):
    topics: list["TopicResponse"] = []


class TopicResponse(BaseModel):
    topic_id: str
    topic_name: str
    book_page_range: str | None
    mcq_count: int = 0
    is_gap: bool = False

    class Config:
        from_attributes = True


# ── MCQ ───────────────────────────────────────────────────────────────────────


class MCQOptions(BaseModel):
    A: str
    B: str
    C: str
    D: str


class MCQCorrectAnswer(BaseModel):
    option: str
    text: str


class MCQListItem(BaseModel):
    id: int
    chapter_id: str
    chapter_name: str
    topic_id: str
    topic_name: str
    difficulty: str | None
    book_page_range: str | None

    class Config:
        from_attributes = True


class MCQDetailResponse(MCQListItem):
    question: str
    options: MCQOptions
    correct_answer: MCQCorrectAnswer
    source_quote: str | None
    pdf_page_number: int | None
    explanation: str | None

    class Config:
        from_attributes = True


# ── Exam ───────────────────────────────────────────────────────────────────────


class ExamListItem(BaseModel):
    exam_id: str
    title: str
    mcq_count: int
    duration_minutes: int
    is_completed: bool = False
    attempt_count: int = 0
    latest_attempt_id: str | None = None
    latest_score_percentage: float | None = None

    @field_validator("exam_id")
    @classmethod
    def exam_id_must_be_numeric(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("exam_id must be a numeric string")
        return v

    class Config:
        from_attributes = True


class ExamDetailResponse(ExamListItem):
    description: str | None
    instructions: str | None


class ExamStartResponse(BaseModel):
    session_id: str
    exam_id: str
    mcqs: list[MCQListItem]
    duration_minutes: int


class ExamAnswerSubmission(BaseModel):
    mcq_id: int
    selected_option: str


class ExamSubmitRequest(BaseModel):
    session_id: str | None = None
    time_taken_seconds: int = Field(default=0, ge=0)
    answers: list[ExamAnswerSubmission]


class ExamSubmissionResponse(BaseModel):
    total: int
    correct: int
    score_percentage: float
    details: list["ExamAnswerDetail"]


class ExamAnswerDetail(BaseModel):
    mcq_id: int
    selected_option: str | None
    correct_option: str
    is_correct: bool


# ── Practice ───────────────────────────────────────────────────────────────────


class PracticeSessionCreate(BaseModel):
    topic_name: str | None = None
    chapter_name: str | None = None
    book_id: str | None = None
    mode: str = Field(default="adaptive", pattern=r"^(adaptive|review|exam|weakness|coverage)$")
    count: int = Field(default=30, ge=1)


class PracticeSessionResponse(BaseModel):
    session_id: str
    mode: str
    count: int
    mcqs: list[MCQListItem]


class AnswerSubmission(BaseModel):
    mcq_id: int
    selected_option: str
    time_spent_seconds: int | None = Field(default=None, ge=0)


class AnswerResponse(BaseModel):
    mcq_id: int
    is_correct: bool
    correct_option: str
    explanation: str | None


# ── Student Stats ─────────────────────────────────────────────────────────────


class StudentStatsResponse(BaseModel):
    total_answered: int
    correct_count: int
    accuracy: float
    current_streak: int
    total_time_seconds: int = 0
    avg_time_seconds: float = 0.0
    exams_taken: int = 0
    strongest_topic: str | None = None
    weakest_topic: str | None = None


class ConfidenceProfileResponse(BaseModel):
    lucky_guess: int
    confident_master: int
    confident_mistake: int
    no_knowledge: int



class TopicHistoryEntry(BaseModel):
    answered_at: datetime
    is_correct: bool
    difficulty: str | None


class TopicHistoryResponse(BaseModel):
    topic_id: str
    topic_name: str
    entries: list[TopicHistoryEntry]


class WeakTopicEntry(BaseModel):
    topic_id: str
    topic_name: str
    chapter_id: str
    chapter_name: str
    accuracy: float
    attempt_count: int


# ── Generation ────────────────────────────────────────────────────────────────


class GenerateTopicRequest(BaseModel):
    topic_name: str
    chapter_id: str
    chapter_name: str
    book_page_range: str
    source_file: str = Field(min_length=1, max_length=255)
    count: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def validate_source_file(self) -> "GenerateTopicRequest":
        sf = self.source_file
        if "\x00" in sf:
            raise ValueError("source_file must not contain null bytes")
        if ".." in sf or sf.startswith("/") or sf.startswith("\\"):
            raise ValueError("source_file must not contain path traversal characters")
        return self


class GenerateJobResponse(BaseModel):
    job_id: str
    status: str  # pending | running | done | error
    message: str


# ── Exam Feedback ───────────────────────────────────────────────────────────────

class WeakTopicFeedback(BaseModel):
    topic_name: str
    chapter_name: str
    accuracy_percentage: float
    focus_recommendations: list[str]


class StrongTopicFeedback(BaseModel):
    topic_name: str
    chapter_name: str
    accuracy_percentage: float
    encouragement: str


class ExamFeedback(BaseModel):
    overall_summary: str
    weak_topics: list[WeakTopicFeedback]
    strong_topics: list[StrongTopicFeedback]
    personalized_study_recommendations: list[str]


class ExamAnswerDetailWithPractice(BaseModel):
    mcq_id: int
    selected_option: str | None
    correct_option: str
    is_correct: bool
    practice_related_questions: list[str] = []


class ExamSubmissionResponseWithFeedback(BaseModel):
    attempt_id: str | None = None
    exam_id: str | None = None
    exam_title: str | None = None
    session_id: str | None = None
    time_taken_seconds: int = 0
    total: int
    correct: int
    score_percentage: float
    details: list[ExamAnswerDetailWithPractice]
    topic_breakdown: list[dict] = []
    feedback_status: str = "pending"
    feedback: ExamFeedback | None = None


class ExamAttemptListItem(BaseModel):
    attempt_id: str
    exam_id: str
    exam_title: str
    total: int
    correct: int
    score_percentage: float
    time_taken_seconds: int
    feedback_status: str
    submitted_at: datetime


class ExamAttemptDetail(ExamSubmissionResponseWithFeedback):
    submitted_at: datetime | None = None
    feedback_source: str | None = None
    feedback_error: str | None = None


class ExamFeedbackStatusResponse(BaseModel):
    attempt_id: str
    feedback_status: str
    feedback: ExamFeedback | None = None
    feedback_source: str | None = None
    feedback_error: str | None = None


# ── Re-export ─────────────────────────────────────────────────────────────────
SubjectResponse.model_rebuild()
ChapterDetailResponse.model_rebuild()
TopicResponse.model_rebuild()
ExamSubmissionResponse.model_rebuild()
ExamAnswerDetail.model_rebuild()
ExamSubmissionResponseWithFeedback.model_rebuild()
ExamAttemptDetail.model_rebuild()
