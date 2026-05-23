"""ExamFeedbackService — Gemini-powered post-exam feedback + related questions."""
import json
import logging
import time
from typing import Annotated, Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from shokti.core.config import GEMINI

logger = logging.getLogger(__name__)


# ── Gemini response schemas ────────────────────────────────────────────────────

class WeakTopicGemini(BaseModel):
    topic_name: str
    chapter_name: str
    accuracy_percentage: float
    focus_recommendations: Annotated[list[str], 3]  # exactly 3


class StrongTopicGemini(BaseModel):
    topic_name: str
    chapter_name: str
    accuracy_percentage: float
    encouragement: str


class ExamFeedbackGeminiResponse(BaseModel):
    overall_summary: str
    weak_topics: Annotated[list[WeakTopicGemini], 5]   # max 5
    strong_topics: Annotated[list[StrongTopicGemini], 5]  # max 5
    personalized_study_recommendations: Annotated[list[str], 5]  # max 5


# ── Service ────────────────────────────────────────────────────────────────────

class ExamFeedbackService:
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key, vertexai=False)
        self._system_instruction = (
            "You are a thoughtful medical exam tutor. Provide constructive, actionable feedback "
            "in Bangla with English technical terms where appropriate. "
            "Focus on helping students improve, not just reporting scores."
        )

    def _build_prompt(
        self,
        total: int,
        correct: int,
        score_pct: float,
        chapter_results: list[dict[str, Any]],
    ) -> str:
        topic_lines = "\n".join(
            f"  - {c['chapter']} > {c['topic']}: {c['correct']}/{c['total']} correct "
            f"({round(c['correct'] / c['total'] * 100) if c['total'] > 0 else 0}% accuracy), "
            f"avg time {c.get('avg_time_seconds', 0):.1f}s, "
            f"lucky guesses {c.get('lucky_guess_count', 0)}, "
            f"confident masters {c.get('confident_master_count', 0)}, "
            f"confident mistakes {c.get('confident_mistake_count', 0)}, "
            f"no-knowledge answers {c.get('no_knowledge_count', 0)}"
            for c in chapter_results
        )
        return f"""\
Student just completed a practice exam.

Results: {correct}/{total} correct ({score_pct:.1f}%)

Per-topic breakdown:
{topic_lines}

Based on this, provide:
1. A qualitative overall_summary (interpret the performance, don't just echo the %)
2. weak_topics: topics with accuracy below 60% with exactly 3 specific focus_recommendations each
3. strong_topics: topics with accuracy 60% or above with encouragement
4. personalized_study_recommendations: exactly 5 concrete study tips

Rules:
- Use timing/confidence signals: fast wrong = confident mistake/misconception risk; slow wrong = no-knowledge gap; fast correct may be lucky guess and needs verification.
- If no weak topics exist, return empty list for weak_topics
- If no strong topics exist, return empty list for strong_topics
- focus_recommendations must be specific, not generic ("read more")
- Return ONLY valid JSON matching the schema"""

    def get_feedback(
        self,
        total: int,
        correct: int,
        score_pct: float,
        chapter_results: list[dict[str, Any]],
    ) -> "ExamFeedback | None":  # noqa: F821
        from shokti.api.schemas import ExamFeedback, WeakTopicFeedback, StrongTopicFeedback

        prompt = self._build_prompt(total, correct, score_pct, chapter_results)

        # Try with File Search first; fall back to plain generation onPermissionDenied
        for attempt in range(2):
            try:
                config = types.GenerateContentConfig(
                    system_instruction=self._system_instruction,
                    tools=([types.Tool(file_search=types.FileSearch(
                        file_search_store_names=[GEMINI.STORE_NAME]
                    ))] if attempt == 0 else None),
                    response_mime_type="application/json",
                    response_json_schema=ExamFeedbackGeminiResponse.model_json_schema(),
                )
                response = self._generate_with_retries(config, prompt)
                if response is None:
                    return None
                parsed = ExamFeedbackGeminiResponse.model_validate_json(response.text)
                return ExamFeedback(
                    overall_summary=parsed.overall_summary,
                    weak_topics=[
                        WeakTopicFeedback(
                            topic_name=w.topic_name,
                            chapter_name=w.chapter_name,
                            accuracy_percentage=w.accuracy_percentage,
                            focus_recommendations=w.focus_recommendations,
                        )
                        for w in parsed.weak_topics
                    ],
                    strong_topics=[
                        StrongTopicFeedback(
                            topic_name=s.topic_name,
                            chapter_name=s.chapter_name,
                            accuracy_percentage=s.accuracy_percentage,
                            encouragement=s.encouragement,
                        )
                        for s in parsed.strong_topics
                    ],
                    personalized_study_recommendations=parsed.personalized_study_recommendations,
                )
            except genai_errors.ClientError:
                # File Search store inaccessible (403 PERMISSION_DENIED) — retry without tools
                if attempt == 0:
                    logger.info("File Search store denied, retrying without tools")
                    continue
                return None
            except Exception as exc:
                logger.warning("ExamFeedbackService failed: %s", exc)
                return None
        return None

    def _generate_with_retries(
        self,
        config: types.GenerateContentConfig,
        prompt: str,
    ) -> types.GenerateContentResponse | None:
        for attempt in range(1, GEMINI.MAX_RETRIES + 1):
            try:
                return self._client.models.generate_content(
                    model=GEMINI.MODEL,
                    contents=prompt,
                    config=config,
                )
            except genai_errors.ClientError:
                # Re-raise ClientError so caller can distinguish it from other failures
                raise
            except genai_errors.ServerError as exc:
                if attempt == GEMINI.MAX_RETRIES:
                    logger.warning("Gemini server error after %d retries: %s", attempt, exc)
                    return None
                delay = attempt * GEMINI.RETRY_DELAY_BASE
                logger.info("Gemini attempt %d failed, retrying in %ds: %s", attempt, delay, exc)
                time.sleep(delay)
            except Exception as exc:
                logger.warning("Gemini generate_content failed: %s", exc)
                return None
