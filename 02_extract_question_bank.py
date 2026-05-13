import os
import re
import json
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF
from tqdm import tqdm
from dotenv import load_dotenv
from google import genai
from google.genai import errors
from google.genai import types


load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

if not API_KEY:
    raise ValueError("Missing GEMINI_API_KEY in .env")

client = genai.Client(api_key=API_KEY)


BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
DEFAULT_API_TIMEOUT_MS = 180_000
RETRYABLE_HTTP_STATUS_CODES = [408, 429, 500, 502, 503, 504]


def load_json(path: Path, default: Any):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_json_loads(text: str) -> Any:
    text = text.strip()

    if text.startswith("```json"):
        text = text.replace("```json", "").replace("```", "").strip()
    elif text.startswith("```"):
        text = text.replace("```", "").strip()

    return json.loads(text)


def normalize_extraction_result(data: Any, page_number: int, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Gemini sometimes returns the requested object shape and sometimes returns
    only the questions array. Normalize both into the page result shape.
    """
    result = {
        "page_number": page_number,
        "questions": [],
    }

    if defaults:
        result.update(defaults)

    if isinstance(data, list):
        result["questions"] = data
        return result

    if not isinstance(data, dict):
        raise TypeError(f"Expected JSON object or list, got {type(data).__name__}")

    result.update(data)

    questions = result.get("questions", [])
    if isinstance(questions, dict):
        result["questions"] = [questions]
    elif not isinstance(questions, list):
        result["questions"] = []

    result["page_number"] = normalize_number(result.get("page_number")) or page_number
    return result


def normalize_number(value: Any) -> Optional[int]:
    if value is None:
        return None

    text = str(value).translate(BANGLA_DIGITS)
    match = re.search(r"\d+", text)
    if not match:
        return None

    return int(match.group())


def open_pdf(path: Path, password: Optional[str] = None) -> fitz.Document:
    doc = fitz.open(str(path))
    if doc.needs_pass:
        if not password:
            raise ValueError(f"{path} is password-protected. Pass --password <password>.")
        if not doc.authenticate(password):
            raise ValueError(f"Wrong password for {path}.")
    return doc


def render_pdf_page_to_png(pdf_path: Path, page_index: int, dpi: int = 220, password: Optional[str] = None) -> bytes:
    doc = open_pdf(pdf_path, password)
    page = doc[page_index]

    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)

    png_bytes = pix.tobytes("png")
    doc.close()

    return png_bytes


def image_part(png_bytes: bytes):
    return types.Part.from_bytes(
        data=png_bytes,
        mime_type="image/png"
    )


def gemini_json_config(timeout_ms: int) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(
                attempts=4,
                initial_delay=2,
                max_delay=30,
                exp_base=2,
                jitter=1,
                http_status_codes=RETRYABLE_HTTP_STATUS_CODES,
            ),
        ),
    )


def extract_questions_from_page(
    png_bytes: bytes,
    year: int,
    exam_name: str,
    page_number: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are extracting MCQ questions from a scanned admission question paper page.

Exam: {exam_name}
Year: {year}
Page number: {page_number}

Task:
Extract EVERY visible MCQ question from this page.

Rules:
- Do not skip any visible question.
- Preserve original language.
- Extract question number if visible.
- Extract stem and options.
- If options are split across lines, merge correctly.
- If a question continues from previous or next page, mark it using continuation fields.
- If answer key is visible, extract answer, otherwise null.
- Do not solve the question yourself.
- Return only valid JSON.

JSON format:
{{
  "year": {year},
  "exam_name": "{exam_name}",
  "page_number": {page_number},
  "questions": [
    {{
      "question_number": 1,
      "raw_question_number": "১",
      "stem": "question text",
      "options": {{
        "A": "option A",
        "B": "option B",
        "C": "option C",
        "D": "option D"
      }},
      "answer": null,
      "subject_guess": "Physics / Chemistry / Biology / English / GK / Unknown",
      "is_continuation_from_previous_page": false,
      "continues_to_next_page": false,
      "raw_text": "full visible text of this question",
      "confidence": 0.0
    }}
  ]
}}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )

    return safe_json_loads(response.text)


def extract_written_questions_from_page(
    png_bytes: bytes,
    year: int,
    exam_name: str,
    page_number: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are extracting written questions from a scanned admission question paper page.

Exam: {exam_name}
Year: {year}
Page number: {page_number}

Task:
Extract EVERY visible written question from this page.

Rules:
- Do not skip any visible question.
- Preserve original language.
- Extract question number if visible.
- Extract the full stem and all sub-questions with their labels (a/b/c or i/ii/iii).
- Extract marks for each question and sub-question if visible.
- Classify written_type: short_answer, long_answer, problem, creative, proof, or other.
- If a question continues from previous or next page, mark it using continuation fields.
- Do not solve the question yourself.
- Return only valid JSON.

JSON format:
{{
  "year": {year},
  "exam_name": "{exam_name}",
  "page_number": {page_number},
  "questions": [
    {{
      "question_number": 1,
      "question_type": "written",
      "written_type": "long_answer",
      "stem": "main question text",
      "marks": 10,
      "sub_questions": [
        {{"label": "a", "text": "sub-question text", "marks": 3}},
        {{"label": "b", "text": "sub-question text", "marks": 3}},
        {{"label": "c", "text": "sub-question text", "marks": 4}}
      ],
      "options": null,
      "answer": null,
      "subject_guess": "Physics / Chemistry / Math / Biology / English / GK / Unknown",
      "is_continuation_from_previous_page": false,
      "continues_to_next_page": false,
      "raw_text": "full visible text of this question",
      "confidence": 0.0
    }}
  ]
}}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )
    return safe_json_loads(response.text)


def extract_mixed_questions_from_page(
    png_bytes: bytes,
    year: int,
    exam_name: str,
    page_number: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are extracting questions from a scanned admission question paper page that contains both MCQ and written questions.

Exam: {exam_name}
Year: {year}
Page number: {page_number}

Task:
Extract EVERY visible question from this page.

Rules:
- Do not skip any visible question.
- Preserve original language.
- For MCQ: set question_type "mcq", fill options A/B/C/D, marks and sub_questions can be null/empty.
- For written: set question_type "written", fill written_type, marks, sub_questions; options is null.
- Written types: short_answer, long_answer, problem, creative, proof, or other.
- If a question continues from previous or next page, mark it using continuation fields.
- If answer key is visible, extract answer, otherwise null.
- Do not solve the question yourself.
- Return only valid JSON.

JSON format:
{{
  "year": {year},
  "exam_name": "{exam_name}",
  "page_number": {page_number},
  "questions": [
    {{
      "question_number": 1,
      "question_type": "mcq",
      "written_type": null,
      "stem": "question text",
      "marks": null,
      "sub_questions": [],
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": null,
      "subject_guess": "Physics / Chemistry / Math / Biology / English / GK / Unknown",
      "is_continuation_from_previous_page": false,
      "continues_to_next_page": false,
      "raw_text": "full visible text of this question",
      "confidence": 0.0
    }}
  ]
}}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )
    return safe_json_loads(response.text)


def audit_page_question_numbers(
    png_bytes: bytes,
    year: int,
    exam_name: str,
    page_number: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are auditing a scanned admission question paper page.

Exam: {exam_name}
Year: {year}
Page number: {page_number}

Task:
Only identify the question numbers visible on this page.

Rules:
- Do not extract full question text.
- Do not solve anything.
- Include every visible question number.
- If a number is partially visible but likely, include it with low confidence.
- Return only valid JSON.

JSON format:
{{
  "page_number": {page_number},
  "visible_question_numbers": [1, 2, 3],
  "visible_question_count": 3,
  "notes": ""
}}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )

    return safe_json_loads(response.text)


def extract_page_with_validation(
    pdf_path: Path,
    page_index: int,
    year: int,
    exam_name: str,
    question_type: str = "mcq",
    max_retries: int = 3,
    timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    page_number = page_index + 1
    png_bytes = render_pdf_page_to_png(pdf_path, page_index, password=password)

    last_result = {
        "year": year,
        "exam_name": exam_name,
        "page_number": page_number,
        "questions": [],
    }
    last_audit = None
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            if question_type == "written":
                result = extract_written_questions_from_page(
                    png_bytes=png_bytes,
                    year=year,
                    exam_name=exam_name,
                    page_number=page_number,
                    timeout_ms=timeout_ms,
                )
            elif question_type == "mixed":
                result = extract_mixed_questions_from_page(
                    png_bytes=png_bytes,
                    year=year,
                    exam_name=exam_name,
                    page_number=page_number,
                    timeout_ms=timeout_ms,
                )
            else:
                result = extract_questions_from_page(
                    png_bytes=png_bytes,
                    year=year,
                    exam_name=exam_name,
                    page_number=page_number,
                    timeout_ms=timeout_ms,
                )
            result = normalize_extraction_result(
                result,
                page_number=page_number,
                defaults={
                    "year": year,
                    "exam_name": exam_name,
                },
            )

            audit = audit_page_question_numbers(
                png_bytes=png_bytes,
                year=year,
                exam_name=exam_name,
                page_number=page_number,
                timeout_ms=timeout_ms,
            )
        except (errors.APIError, TimeoutError, json.JSONDecodeError, TypeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"\nPage {page_number} attempt {attempt} failed: {last_error}")
            continue

        extracted_nums = {
            normalize_number(q.get("question_number"))
            for q in result.get("questions", [])
            if normalize_number(q.get("question_number")) is not None
        }

        audited_nums = {
            normalize_number(n)
            for n in audit.get("visible_question_numbers", [])
            if normalize_number(n) is not None
        }

        last_result = result
        last_audit = audit

        # If audit found numbers, extracted should match audit.
        if audited_nums and extracted_nums == audited_nums:
            result["_audit"] = audit
            result["_validation_status"] = "passed"
            result["_attempts"] = attempt
            return result

        # If no question number visible, accept only if both say no questions.
        if not audited_nums and len(result.get("questions", [])) == 0:
            result["_audit"] = audit
            result["_validation_status"] = "passed_no_questions"
            result["_attempts"] = attempt
            return result

    last_result["_audit"] = last_audit
    last_result["_validation_status"] = "needs_manual_review"
    last_result["_attempts"] = max_retries
    if last_error:
        last_result["_error"] = last_error

    return last_result


def chapter_context_from_result(page_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "subject": page_result.get("subject"),
        "chapter_number": page_result.get("chapter_number"),
        "chapter_name": page_result.get("chapter_name"),
    }


def latest_chapter_context(page_results_by_number: Dict[int, Dict[str, Any]], before_page_number: int) -> Dict[str, Any]:
    context = {
        "subject": None,
        "chapter_number": None,
        "chapter_name": None,
    }

    for page_number in sorted(page_results_by_number):
        if page_number >= before_page_number:
            continue

        page_context = chapter_context_from_result(page_results_by_number[page_number])
        if page_context.get("subject"):
            context["subject"] = page_context["subject"]
        if page_context.get("chapter_number"):
            context["chapter_number"] = page_context["chapter_number"]
        if page_context.get("chapter_name"):
            context["chapter_name"] = page_context["chapter_name"]

    return context


def extract_chapterwise_questions_from_page(
    png_bytes: bytes,
    page_number: int,
    known_context: Dict[str, Any],
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are extracting MCQ questions from a chapterwise medical admission question bank page.

Page number: {page_number}

Known context from previous pages, if this page does not show a new chapter heading:
- Subject: {known_context.get("subject")}
- Chapter number: {known_context.get("chapter_number")}
- Chapter name: {known_context.get("chapter_name")}

Task:
Extract EVERY visible MCQ question from this page.

Rules:
- This is a chapterwise question bank, not one yearwise exam paper.
- If a subject/chapter heading is visible, extract it.
- If no heading is visible, use the known context above.
- Preserve original language.
- Extract the source tag such as [MEDICAL : 21-22] for each question.
- Extract source_exam as MEDICAL and source_year_label as 21-22 when visible.
- Do not invent a sequential question number if no number is printed.
- If options are split across lines, merge correctly.
- If a question continues from previous or next page, mark it using continuation fields.
- If answer key is visible, extract answer, otherwise null.
- Do not solve the question yourself.
- Return only valid JSON.

JSON format:
{{
  "page_number": {page_number},
  "subject": "Physics / Chemistry / Biology / English / GK / Unknown",
  "chapter_number": "1",
  "chapter_name": "chapter title",
  "questions": [
    {{
      "question_number": null,
      "raw_question_number": null,
      "stem": "question text",
      "options": {{
        "A": "option A",
        "B": "option B",
        "C": "option C",
        "D": "option D"
      }},
      "answer": null,
      "source_exam": "MEDICAL",
      "source_year_label": "21-22",
      "subject_guess": "Physics / Chemistry / Biology / English / GK / Unknown",
      "is_continuation_from_previous_page": false,
      "continues_to_next_page": false,
      "raw_text": "full visible text of this question",
      "confidence": 0.0
    }}
  ]
}}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )

    return safe_json_loads(response.text)


def audit_chapterwise_page_question_count(
    png_bytes: bytes,
    page_number: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are auditing a chapterwise medical admission question bank page.

Page number: {page_number}

Task:
Only count the visible MCQ questions on this page.

Rules:
- Do not extract full question text.
- Do not solve anything.
- Count each visible MCQ question once.
- If a question is partially visible, include it and note that it is partial.
- Return only valid JSON.

JSON format:
{{
  "page_number": {page_number},
  "visible_question_count": 3,
  "notes": ""
}}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )

    return safe_json_loads(response.text)


def extract_chapterwise_page_with_validation(
    pdf_path: Path,
    page_index: int,
    known_context: Dict[str, Any],
    max_retries: int = 3,
    timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    page_number = page_index + 1
    png_bytes = render_pdf_page_to_png(pdf_path, page_index, password=password)

    last_result = {
        "page_number": page_number,
        "subject": known_context.get("subject"),
        "chapter_number": known_context.get("chapter_number"),
        "chapter_name": known_context.get("chapter_name"),
        "questions": [],
    }
    last_audit = None
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            result = extract_chapterwise_questions_from_page(
                png_bytes=png_bytes,
                page_number=page_number,
                known_context=known_context,
                timeout_ms=timeout_ms,
            )
            result = normalize_extraction_result(
                result,
                page_number=page_number,
                defaults={
                    "subject": known_context.get("subject"),
                    "chapter_number": known_context.get("chapter_number"),
                    "chapter_name": known_context.get("chapter_name"),
                },
            )

            audit = audit_chapterwise_page_question_count(
                png_bytes=png_bytes,
                page_number=page_number,
                timeout_ms=timeout_ms,
            )
        except (errors.APIError, TimeoutError, json.JSONDecodeError, TypeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"\nPage {page_number} attempt {attempt} failed: {last_error}")
            continue

        last_result = result
        last_audit = audit

        extracted_count = len(result.get("questions", []))
        audited_count = normalize_number(audit.get("visible_question_count"))

        if audited_count is not None and extracted_count == audited_count:
            result["_audit"] = audit
            result["_validation_status"] = "passed"
            result["_attempts"] = attempt
            return result

    last_result["_audit"] = last_audit
    last_result["_validation_status"] = "needs_manual_review"
    last_result["_attempts"] = max_retries
    if last_error:
        last_result["_error"] = last_error

    return last_result


def extract_random_questions_from_page(
    png_bytes: bytes,
    page_number: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are extracting MCQ questions from a medical admission question bank page.

Page number: {page_number}

Task:
Extract EVERY visible MCQ question from this page.

Rules:
- This page may be random/unstructured. Do not assume questions belong to one year or one chapter.
- Preserve original language.
- Extract any visible subject, chapter, topic, source exam, source year/session, or source tag for each question.
- Extract source tags such as [MEDICAL : 21-22] when visible.
- Do not invent a sequential question number if no number is printed.
- If options are split across lines, merge correctly.
- If a question continues from previous or next page, mark it using continuation fields.
- If answer key is visible, extract answer, otherwise null.
- Do not solve the question yourself.
- Return only valid JSON.

JSON format:
{{
  "page_number": {page_number},
  "questions": [
    {{
      "question_number": null,
      "raw_question_number": null,
      "stem": "question text",
      "options": {{
        "A": "option A",
        "B": "option B",
        "C": "option C",
        "D": "option D"
      }},
      "answer": null,
      "source_exam": "MEDICAL",
      "source_year_label": "21-22",
      "source_tag": "[MEDICAL : 21-22]",
      "subject_guess": "Physics / Chemistry / Biology / English / GK / Unknown",
      "chapter_guess": null,
      "topic_guess": null,
      "is_continuation_from_previous_page": false,
      "continues_to_next_page": false,
      "raw_text": "full visible text of this question",
      "confidence": 0.0
    }}
  ]
}}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )

    return safe_json_loads(response.text)


def audit_random_page_question_count(
    png_bytes: bytes,
    page_number: int,
    timeout_ms: int,
) -> Dict[str, Any]:
    prompt = f"""
You are auditing a medical admission question bank page.

Page number: {page_number}

Task:
Only count the visible MCQ questions on this page.

Rules:
- Do not extract full question text.
- Do not solve anything.
- Count each visible MCQ question once.
- If a question is partially visible, include it and note that it is partial.
- Return only valid JSON.

JSON format:
{{
  "page_number": {page_number},
  "visible_question_count": 3,
  "notes": ""
}}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part(png_bytes), prompt],
        config=gemini_json_config(timeout_ms),
    )

    return safe_json_loads(response.text)


def extract_random_page_with_validation(
    pdf_path: Path,
    page_index: int,
    max_retries: int = 3,
    timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    page_number = page_index + 1
    png_bytes = render_pdf_page_to_png(pdf_path, page_index, password=password)

    last_result = {
        "page_number": page_number,
        "questions": [],
    }
    last_audit = None
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            result = extract_random_questions_from_page(
                png_bytes=png_bytes,
                page_number=page_number,
                timeout_ms=timeout_ms,
            )
            result = normalize_extraction_result(
                result,
                page_number=page_number,
            )

            audit = audit_random_page_question_count(
                png_bytes=png_bytes,
                page_number=page_number,
                timeout_ms=timeout_ms,
            )
        except (errors.APIError, TimeoutError, json.JSONDecodeError, TypeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"\nPage {page_number} attempt {attempt} failed: {last_error}")
            continue

        last_result = result
        last_audit = audit

        extracted_count = len(result.get("questions", []))
        audited_count = normalize_number(audit.get("visible_question_count"))

        if audited_count is not None and extracted_count == audited_count:
            result["_audit"] = audit
            result["_validation_status"] = "passed"
            result["_attempts"] = attempt
            return result

    last_result["_audit"] = last_audit
    last_result["_validation_status"] = "needs_manual_review"
    last_result["_attempts"] = max_retries
    if last_error:
        last_result["_error"] = last_error

    return last_result


def checkpoint_file_path(checkpoint_dir: Path, exam_name: str, year: int, pdf_name: str) -> Path:
    safe_exam_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", exam_name).strip("_")
    safe_pdf_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", pdf_name).strip("_")
    return checkpoint_dir / f"{safe_exam_name}_{year}_{safe_pdf_name}.pages.json"


def collection_checkpoint_file_path(checkpoint_dir: Path, collection_name: str, pdf_name: str, mode: str) -> Path:
    safe_collection_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", collection_name).strip("_")
    safe_pdf_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", pdf_name).strip("_")
    safe_mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", mode).strip("_")
    return checkpoint_dir / f"{safe_collection_name}_{safe_pdf_name}.{safe_mode}.pages.json"


def load_checkpoint(path: Path) -> Dict[int, Dict[str, Any]]:
    data = load_json(path, default={"page_results": []})
    return {
        int(page_result["page_number"]): page_result
        for page_result in data.get("page_results", [])
        if page_result.get("page_number") is not None
    }


def save_checkpoint(path: Path, pdf_path: Path, year: int, exam_name: str, page_results: List[Dict[str, Any]]):
    save_json(path, {
        "pdf": str(pdf_path),
        "year": year,
        "exam_name": exam_name,
        "page_results": sorted(page_results, key=lambda x: x.get("page_number", 0)),
    })


def save_collection_checkpoint(
    path: Path,
    pdf_path: Path,
    collection_name: str,
    mode: str,
    page_results: List[Dict[str, Any]],
):
    save_json(path, {
        "pdf": str(pdf_path),
        "collection_name": collection_name,
        "mode": mode,
        "page_results": sorted(page_results, key=lambda x: x.get("page_number", 0)),
    })


def merge_into_question_bank(
    existing_bank: Dict[str, Any],
    year: int,
    exam_name: str,
    pdf_name: str,
    page_results: List[Dict[str, Any]],
):
    existing_bank.setdefault("exams", [])

    # Remove old version of same year/exam/pdf
    existing_bank["exams"] = [
        e for e in existing_bank["exams"]
        if not (
            e.get("year") == year
            and e.get("exam_name") == exam_name
            and e.get("source_pdf") == pdf_name
        )
    ]

    questions = []

    for page_result in page_results:
        page_number = page_result["page_number"]

        for q in page_result.get("questions", []):
            q_num = normalize_number(q.get("question_number"))

            question_id = f"{exam_name}_{year}_q{q_num}" if q_num else f"{exam_name}_{year}_page{page_number}_unknown_{len(questions)+1}"

            questions.append({
                "id": question_id,
                "year": year,
                "exam_name": exam_name,
                "source_pdf": pdf_name,
                "page_number": page_number,
                "question_number": q_num,
                "raw_question_number": q.get("raw_question_number"),
                "question_type": q.get("question_type", "mcq"),
                "written_type": q.get("written_type"),
                "stem": q.get("stem"),
                "options": q.get("options") or {},
                "marks": q.get("marks"),
                "sub_questions": q.get("sub_questions") or [],
                "answer": q.get("answer"),
                "subject_guess": q.get("subject_guess", "Unknown"),
                "is_continuation_from_previous_page": q.get("is_continuation_from_previous_page", False),
                "continues_to_next_page": q.get("continues_to_next_page", False),
                "raw_text": q.get("raw_text"),
                "confidence": q.get("confidence"),
                "mapping": {
                    "mapped": False,
                    "node_ids": [],
                    "notes": ""
                }
            })

    questions.sort(key=lambda x: (x["question_number"] is None, x["question_number"] or 9999))

    exam_record = {
        "year": year,
        "exam_name": exam_name,
        "source_pdf": pdf_name,
        "total_questions": len(questions),
        "questions": questions
    }

    existing_bank["exams"].append(exam_record)


def merge_into_chapterwise_question_bank(
    existing_bank: Dict[str, Any],
    collection_name: str,
    pdf_name: str,
    page_results: List[Dict[str, Any]],
):
    existing_bank.setdefault("collections", [])

    existing_bank["collections"] = [
        collection for collection in existing_bank["collections"]
        if not (
            collection.get("collection_name") == collection_name
            and collection.get("source_pdf") == pdf_name
        )
    ]

    chapters_by_key = {}
    total_questions = 0

    for page_result in page_results:
        page_number = page_result["page_number"]
        subject = page_result.get("subject") or "Unknown"
        chapter_number = str(page_result.get("chapter_number") or "unknown")
        chapter_name = page_result.get("chapter_name") or "Unknown Chapter"
        chapter_key = (subject, chapter_number, chapter_name)

        if chapter_key not in chapters_by_key:
            chapters_by_key[chapter_key] = {
                "subject": subject,
                "chapter_number": chapter_number,
                "chapter_name": chapter_name,
                "total_questions": 0,
                "questions": []
            }

        chapter = chapters_by_key[chapter_key]

        for question_index, q in enumerate(page_result.get("questions", []), start=1):
            total_questions += 1
            chapter["total_questions"] += 1
            question_id = f"{pdf_name}_p{page_number}_q{question_index}"

            chapter["questions"].append({
                "id": question_id,
                "collection_name": collection_name,
                "source_pdf": pdf_name,
                "page_number": page_number,
                "question_index_on_page": question_index,
                "question_number": normalize_number(q.get("question_number")),
                "raw_question_number": q.get("raw_question_number"),
                "question_type": q.get("question_type", "mcq"),
                "written_type": q.get("written_type"),
                "stem": q.get("stem"),
                "options": q.get("options") or {},
                "marks": q.get("marks"),
                "sub_questions": q.get("sub_questions") or [],
                "answer": q.get("answer"),
                "source_exam": q.get("source_exam"),
                "source_year_label": q.get("source_year_label"),
                "subject_guess": q.get("subject_guess", subject),
                "chapter": {
                    "subject": subject,
                    "chapter_number": chapter_number,
                    "chapter_name": chapter_name
                },
                "is_continuation_from_previous_page": q.get("is_continuation_from_previous_page", False),
                "continues_to_next_page": q.get("continues_to_next_page", False),
                "raw_text": q.get("raw_text"),
                "confidence": q.get("confidence"),
                "mapping": {
                    "mapped": False,
                    "node_ids": [],
                    "notes": ""
                }
            })

    collection_record = {
        "collection_name": collection_name,
        "source_pdf": pdf_name,
        "extraction_mode": "chapterwise",
        "total_questions": total_questions,
        "chapters": list(chapters_by_key.values())
    }

    existing_bank["collections"].append(collection_record)


def merge_into_random_question_bank(
    existing_bank: Dict[str, Any],
    collection_name: str,
    pdf_name: str,
    page_results: List[Dict[str, Any]],
):
    existing_bank.setdefault("collections", [])

    existing_bank["collections"] = [
        collection for collection in existing_bank["collections"]
        if not (
            collection.get("collection_name") == collection_name
            and collection.get("source_pdf") == pdf_name
        )
    ]

    questions = []

    for page_result in page_results:
        page_number = page_result["page_number"]

        for question_index, q in enumerate(page_result.get("questions", []), start=1):
            question_id = f"{pdf_name}_p{page_number}_q{question_index}"

            questions.append({
                "id": question_id,
                "collection_name": collection_name,
                "source_pdf": pdf_name,
                "page_number": page_number,
                "question_index_on_page": question_index,
                "question_number": normalize_number(q.get("question_number")),
                "raw_question_number": q.get("raw_question_number"),
                "question_type": q.get("question_type", "mcq"),
                "written_type": q.get("written_type"),
                "stem": q.get("stem"),
                "options": q.get("options") or {},
                "marks": q.get("marks"),
                "sub_questions": q.get("sub_questions") or [],
                "answer": q.get("answer"),
                "source_exam": q.get("source_exam"),
                "source_year_label": q.get("source_year_label"),
                "source_tag": q.get("source_tag"),
                "subject_guess": q.get("subject_guess", "Unknown"),
                "chapter_guess": q.get("chapter_guess"),
                "topic_guess": q.get("topic_guess"),
                "is_continuation_from_previous_page": q.get("is_continuation_from_previous_page", False),
                "continues_to_next_page": q.get("continues_to_next_page", False),
                "raw_text": q.get("raw_text"),
                "confidence": q.get("confidence"),
                "mapping": {
                    "mapped": False,
                    "node_ids": [],
                    "notes": ""
                }
            })

    collection_record = {
        "collection_name": collection_name,
        "source_pdf": pdf_name,
        "extraction_mode": "random",
        "total_questions": len(questions),
        "questions": questions
    }

    existing_bank["collections"].append(collection_record)


def validate_exam_question_count(
    page_results: List[Dict[str, Any]],
    expected_total: Optional[int],
) -> Dict[str, Any]:
    all_numbers = []
    review_pages = []

    for page_result in page_results:
        if page_result.get("_validation_status") == "needs_manual_review":
            review_pages.append({
                "page_number": page_result.get("page_number"),
                "reason": "page_audit_mismatch",
                "audit": page_result.get("_audit")
            })

        for q in page_result.get("questions", []):
            q_num = normalize_number(q.get("question_number"))
            if q_num is not None:
                all_numbers.append(q_num)

    unique_numbers = sorted(set(all_numbers))

    missing_numbers = []
    duplicate_numbers = []

    if expected_total:
        expected_set = set(range(1, expected_total + 1))
        actual_set = set(unique_numbers)

        missing_numbers = sorted(expected_set - actual_set)

        for n in unique_numbers:
            if all_numbers.count(n) > 1:
                duplicate_numbers.append(n)

    passed = True

    if review_pages:
        passed = False

    if expected_total:
        if len(unique_numbers) != expected_total:
            passed = False
        if missing_numbers:
            passed = False
        if duplicate_numbers:
            passed = False

    return {
        "passed": passed,
        "unique_question_count": len(unique_numbers),
        "expected_total": expected_total,
        "missing_numbers": missing_numbers,
        "duplicate_numbers": sorted(set(duplicate_numbers)),
        "review_pages": review_pages
    }


def _question_fingerprint(question: Dict[str, Any]) -> str:
    stem = question.get("stem") or ""
    return re.sub(r"\s+", " ", stem.lower())[:120]


def merge_continuation_questions(page_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge questions split across page boundaries.

    When the last question on a page has continues_to_next_page=True and the
    first question on the next non-empty page has is_continuation_from_previous_page=True,
    the two halves are merged in-place and the continuation question is removed.
    """
    sorted_pages = sorted(page_results, key=lambda p: p.get("page_number", 0))

    for i, page in enumerate(sorted_pages):
        questions = page.get("questions", [])
        if not questions:
            continue

        last_q = questions[-1]
        if not last_q.get("continues_to_next_page"):
            continue

        for j in range(i + 1, len(sorted_pages)):
            next_page = sorted_pages[j]
            next_questions = next_page.get("questions", [])
            if not next_questions:
                continue

            first_next_q = next_questions[0]
            if not first_next_q.get("is_continuation_from_previous_page"):
                break

            if first_next_q.get("stem"):
                last_q["stem"] = (last_q.get("stem") or "") + " " + first_next_q["stem"]

            for key, val in (first_next_q.get("options") or {}).items():
                if not (last_q.get("options") or {}).get(key):
                    last_q.setdefault("options", {})[key] = val

            if not last_q.get("answer") and first_next_q.get("answer"):
                last_q["answer"] = first_next_q["answer"]

            last_q["continues_to_next_page"] = False
            next_page["questions"] = next_questions[1:]
            break

    return sorted_pages


def deduplicate_question_bank(bank: Dict[str, Any]) -> Dict[str, Any]:
    """Remove duplicate questions across the entire bank by stem fingerprint."""
    seen: set = set()

    def dedup_list(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for q in questions:
            fp = _question_fingerprint(q)
            if fp and fp in seen:
                continue
            if fp:
                seen.add(fp)
            result.append(q)
        return result

    for exam in bank.get("exams", []):
        exam["questions"] = dedup_list(exam.get("questions", []))
        exam["total_questions"] = len(exam["questions"])

    for collection in bank.get("collections", []):
        if "chapters" in collection:
            for chapter in collection.get("chapters", []):
                chapter["questions"] = dedup_list(chapter.get("questions", []))
                chapter["total_questions"] = len(chapter["questions"])
        elif "questions" in collection:
            collection["questions"] = dedup_list(collection.get("questions", []))
            collection["total_questions"] = len(collection["questions"])

    return bank


def validate_chapterwise_page_results(page_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    review_pages = []
    total_questions = 0

    for page_result in page_results:
        total_questions += len(page_result.get("questions", []))

        if page_result.get("_validation_status") == "needs_manual_review":
            review_pages.append({
                "page_number": page_result.get("page_number"),
                "reason": "page_count_audit_mismatch",
                "audit": page_result.get("_audit")
            })

    return {
        "passed": len(review_pages) == 0,
        "total_questions": total_questions,
        "review_pages": review_pages
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="Question paper PDF path")
    parser.add_argument(
        "--mode",
        choices=["yearwise", "chapterwise", "random", "exam"],
        default="random",
        help="Question bank organization. exam is kept as an alias for yearwise."
    )
    parser.add_argument("--year", type=int)
    parser.add_argument("--exam-name", default="Admission Exam")
    parser.add_argument("--collection-name", default="Question Bank")
    parser.add_argument(
        "--question-type",
        choices=["mcq", "written", "mixed"],
        default="mcq",
        help="Type of questions in the paper: mcq (default), written, or mixed (MCQ + written). Applies to yearwise mode."
    )
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--out", default="data/question_bank.json")
    parser.add_argument("--review-dir", default="data/extraction_reviews")
    parser.add_argument("--checkpoint-dir", default="data/extraction_checkpoints")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--api-timeout-ms",
        type=int,
        default=DEFAULT_API_TIMEOUT_MS,
        help="Per Gemini request timeout in milliseconds."
    )
    parser.add_argument("--password", default=None, help="Password for encrypted question paper PDFs")

    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    out_path = Path(args.out)
    review_dir = Path(args.review_dir)
    checkpoint_dir = Path(args.checkpoint_dir)

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    effective_mode = "yearwise" if args.mode == "exam" else args.mode

    if effective_mode == "yearwise" and args.year is None:
        raise ValueError("--year is required when --mode yearwise is used")

    doc = open_pdf(pdf_path, args.password)
    total_pages = doc.page_count
    doc.close()

    print(f"Extracting: {pdf_path}")
    print(f"Extraction mode: {effective_mode}")
    print(f"Total pages: {total_pages}")
    print(f"Per-request Gemini timeout: {args.api_timeout_ms} ms")

    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    if effective_mode == "yearwise":
        checkpoint_path = checkpoint_file_path(
            checkpoint_dir=checkpoint_dir,
            exam_name=args.exam_name,
            year=args.year,
            pdf_name=pdf_path.name,
        )
    else:
        checkpoint_path = collection_checkpoint_file_path(
            checkpoint_dir=checkpoint_dir,
            collection_name=args.collection_name,
            pdf_name=pdf_path.name,
            mode=effective_mode,
        )

    checkpoint_results = load_checkpoint(checkpoint_path)
    page_results_by_number = dict(checkpoint_results)

    if checkpoint_results:
        print(f"Loaded checkpoint: {checkpoint_path}")
        print(f"Pages already processed: {len(checkpoint_results)}")

    for page_index in tqdm(range(total_pages), desc="Processing pages"):
        page_number = page_index + 1
        if page_number in page_results_by_number:
            continue

        if effective_mode == "yearwise":
            page_result = extract_page_with_validation(
                pdf_path=pdf_path,
                page_index=page_index,
                year=args.year,
                exam_name=args.exam_name,
                question_type=args.question_type,
                max_retries=args.max_retries,
                timeout_ms=args.api_timeout_ms,
                password=args.password,
            )
        elif effective_mode == "chapterwise":
            page_result = extract_chapterwise_page_with_validation(
                pdf_path=pdf_path,
                page_index=page_index,
                known_context=latest_chapter_context(page_results_by_number, page_number),
                max_retries=args.max_retries,
                timeout_ms=args.api_timeout_ms,
                password=args.password,
            )
        else:
            page_result = extract_random_page_with_validation(
                pdf_path=pdf_path,
                page_index=page_index,
                max_retries=args.max_retries,
                timeout_ms=args.api_timeout_ms,
                password=args.password,
            )

        page_results_by_number[page_number] = page_result
        if effective_mode == "yearwise":
            save_checkpoint(
                path=checkpoint_path,
                pdf_path=pdf_path,
                year=args.year,
                exam_name=args.exam_name,
                page_results=list(page_results_by_number.values()),
            )
        else:
            save_collection_checkpoint(
                path=checkpoint_path,
                pdf_path=pdf_path,
                collection_name=args.collection_name,
                mode=effective_mode,
                page_results=list(page_results_by_number.values()),
            )

    page_results = [
        page_results_by_number[page_number]
        for page_number in sorted(page_results_by_number)
    ]

    page_results = merge_continuation_questions(page_results)

    if effective_mode == "yearwise":
        expected_total = args.expected_total if args.expected_total is not None else 100
        validation = validate_exam_question_count(
            page_results=page_results,
            expected_total=expected_total,
        )
    else:
        validation = validate_chapterwise_page_results(page_results=page_results)

    if not validation["passed"]:
        review_dir.mkdir(parents=True, exist_ok=True)

        review_file = review_dir / f"review_{effective_mode}_{pdf_path.stem}.json"

        review_payload = {
            "pdf": str(pdf_path),
            "mode": effective_mode,
            "validation": validation,
            "page_results": page_results
        }
        if effective_mode == "yearwise":
            review_payload["year"] = args.year
            review_payload["exam_name"] = args.exam_name
        else:
            review_payload["collection_name"] = args.collection_name

        save_json(review_file, review_payload)

        print("\nExtraction validation FAILED.")
        print(f"Review file created: {review_file}")
        print(json.dumps(validation, ensure_ascii=False, indent=2))

        raise RuntimeError(
            "Question extraction is incomplete or uncertain. "
            "Fix/review the review JSON before using analytics."
        )

    if effective_mode == "yearwise":
        question_bank = load_json(out_path, default={
            "version": 1,
            "description": "Medical admission extracted question bank",
            "exams": []
        })

        merge_into_question_bank(
            existing_bank=question_bank,
            year=args.year,
            exam_name=args.exam_name,
            pdf_name=pdf_path.name,
            page_results=page_results,
        )
    elif effective_mode == "chapterwise":
        question_bank = load_json(out_path, default={
            "version": 1,
            "description": "Medical admission chapterwise question bank",
            "collections": []
        })

        merge_into_chapterwise_question_bank(
            existing_bank=question_bank,
            collection_name=args.collection_name,
            pdf_name=pdf_path.name,
            page_results=page_results,
        )
    else:
        question_bank = load_json(out_path, default={
            "version": 1,
            "description": "Medical admission question bank",
            "collections": []
        })

        merge_into_random_question_bank(
            existing_bank=question_bank,
            collection_name=args.collection_name,
            pdf_name=pdf_path.name,
            page_results=page_results,
        )

    question_bank = deduplicate_question_bank(question_bank)
    save_json(out_path, question_bank)

    print("\nExtraction validation PASSED.")
    print(f"Saved question bank: {out_path}")
    if effective_mode == "yearwise":
        print(f"Extracted questions: {validation['unique_question_count']}")
    else:
        print(f"Extracted questions: {validation['total_questions']}")


if __name__ == "__main__":
    main()
