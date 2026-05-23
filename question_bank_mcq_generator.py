"""Extract MCQs from medical_qbank.pdf chapters 01-03 using Gemini File Search (RAG).

Usage:
    source /Users/ismamnurswapnil/shokti/.venv/bin/activate
    python question_bank_mcq_generator.py

API Budget: 6 generative calls (batch size 100, no retries, no gap-fill)
- Ch01: 205 MCQs → 3 calls (75+75+55)
- Ch02: 96 MCQs → 1 call (96)
- Ch03: 123 MCQs → 2 calls (75+48)
"""

import json
import re
import time
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from shokti.core.config import GEMINI

# ---------------------------------------------------------------------------
# Paths & Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
OUTPUT_DIR = ROOT / "question_bank"

MEDICAL_STORE_NAME = GEMINI.STORE_NAME
MODEL = GEMINI.MODEL
BATCH_SIZE = 100

CHAPTER_JOBS = [
    {
        "chapter_id": "01",
        "chapter_name": "কোষ ও এর গঠন",
        "book_page_range": "1-67",
        "source_file": "medical_qbank.pdf",
        "topics": "কোষপ্রাচীর ও কোষঝিল্লি, প্রোটোপ্লাজম, সাইটোপ্লাজম, সাইটোপ্লাজমীয় অঙ্গাণু (মাইটোকন্ড্রিয়া, রাইবোজোম, নিউক্লিয়াস, এন্ডোপ্লাজমিক রেটিকুলাম, গলজি বডি, লাইসোজোম, প্লাস্টিড, ভ্যাকুওল, সেন্ট্রিওল, সিলিয়া, ফ্লাজেলা), নিউক্লিয়াস, ক্রোমোজোম, DNA, RNA, প্রাণিকোষ, উদ্ভিদকোষ",
        "expected_mcq_count": 205,
    },
    {
        "chapter_id": "02",
        "chapter_name": "কোষ বিভাজন",
        "book_page_range": "67-87",
        "source_file": "medical_qbank.pdf",
        "topics": "অ্যামাইটোসিস, মাইটোসিস, কোষচক্র, ইন্টারফেজ, ক্যারিওকাইনেসিস, সাইটোকাইনেসিস, মায়োসিস, ক্রসিং ওভার, মাতৃকোষ, অপত্য কোষ, ক্রোমাটিড, সিন্যাপসিস, টেট্রাড",
        "expected_mcq_count": 96,
    },
    {
        "chapter_id": "03",
        "chapter_name": "কোষ রসায়ন",
        "book_page_range": "88-127",
        "source_file": "medical_qbank.pdf",
        "topics": "কার্বোহাইড্রেট (গ্লুকোজ, ফ্রুক্টোজ, স্টার্চ, সেলুলোজ, গ্লাইকোজেন), প্রোটিন (অ্যামিনো অ্যাসিড, পেপটাইড বন্ড), লিপিড (তেল, চর্বি, ফ্যাটি অ্যাসিড, গ্লিসারল), এনজাইম, ভিটামিন, খনিজ লবণ, নিউক্লিক অ্যাসিড (DNA, RNA), পানি",
        "expected_mcq_count": 123,
    },
]


# ---------------------------------------------------------------------------
# Pydantic schemas — matches chapter_08.json exactly
# ---------------------------------------------------------------------------

class MCQOption(BaseModel):
    A: str = ""
    B: str = ""
    C: str = ""
    D: str = ""


class CorrectAnswer(BaseModel):
    option: str = ""
    text: str = ""


class MCQ(BaseModel):
    mcq_id: int
    question: str = ""
    options: MCQOption = Field(default_factory=MCQOption)
    correct_answer: CorrectAnswer = Field(default_factory=CorrectAnswer)


class Topic(BaseModel):
    topic_id: str = "1"
    topic_name: str = ""
    mcqs: list[MCQ] = Field(default_factory=list)


class Chapter(BaseModel):
    chapter_id: str
    chapter_name: str = ""
    book_page_range: str = ""
    source_file: str = "medical_qbank.pdf"
    topics: list[Topic] = Field(default_factory=list)


class QuestionBank(BaseModel):
    subject: str = "Biology"
    source: str = "মেডিকেল মাস্টার প্রশ্নব্যাংক"
    store_name: str = "উন্মেষ (Unmesh)"
    chapters: list[Chapter] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

def load_api_key(env_file: Path) -> str:
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "GEMINI_API_KEY":
            return value.strip().strip("\"'")
    raise RuntimeError(f"GEMINI_API_KEY not found in {env_file}")


def get_client():
    api_key = load_api_key(ENV_FILE)
    return genai.Client(api_key=api_key, http_options={"timeout": 600000})


# ---------------------------------------------------------------------------
# File Search helper (no JSON schema enforcement)
# ---------------------------------------------------------------------------

def query_store(client, prompt: str) -> str:
    """Query File Search store and return raw text. No schema enforcement."""
    config = types.GenerateContentConfig(
        tools=[types.Tool(file_search=types.FileSearch(
            file_search_store_names=[MEDICAL_STORE_NAME]
        ))],
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config,
    )
    return response.text


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def extract_json_array(text: str) -> list:
    """Extract JSON array from model response (handles markdown fences)."""
    block_match = re.search(r'```(?:json)?\s*\n?(\[[\s\S]*?\])\n?```', text, re.DOTALL)
    if block_match:
        try:
            return json.loads(block_match.group(1))
        except json.JSONDecodeError:
            pass

    array_match = re.search(r'(\[[\s\S]*\])', text, re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group(1))
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    return []


# ---------------------------------------------------------------------------
# Batch extraction prompt — comprehensive, no ID range
# ---------------------------------------------------------------------------

EXTRACT_PROMPT_TEMPLATE = """You are extracting ALL MCQs from medical_qbank.pdf in the File Search store (free_medical_qbank).

CHAPTER: {chapter_name} (Chapter {chapter_id})
PAGE RANGE: {book_page_range}

This chapter covers: {topics}

TASK — extract EVERY MCQ in this chapter without skipping any:
1. Search for all content related to this chapter
2. Find ALL MCQs in the chapter (check chapter markers: প্রথম অধ্যায়, দ্বিতীয় অধ্যায় etc.)
3. For each MCQ extract: mcq_id, question text, all 4 options (A/B/C/D), correct_answer
4. Correct answer from answer key (end of chapter): ক→A, খ→B, গ→C, ঘ→D
5. Set correct_answer.text to EXACT full text of the chosen option
6. Do NOT skip any MCQ — include ALL found in this chapter

Return a valid JSON array of MCQ objects. No markdown, no explanation.
[{{"mcq_id": 1, "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_answer": {{"option": "A", "text": "..."}}}}]"""


def extract_batch(client, job: dict, id_start: int, id_end: int) -> list[dict]:
    """Extract one batch of MCQs via RAG (no schema enforcement)."""
    prompt = EXTRACT_PROMPT_TEMPLATE.format(
        chapter_id=job["chapter_id"],
        chapter_name=job["chapter_name"],
        book_page_range=job["book_page_range"],
        topics=job["topics"],
    )

    print(f"    Extracting all MCQs for chapter {job['chapter_id']}...", flush=True)
    text = query_store(client, prompt)

    mcqs = extract_json_array(text)
    if not mcqs:
        print(f"    WARNING: Could not parse JSON. Text starts: {text[:300]}", flush=True)
        return []

    normalized = []
    for m in mcqs:
        if not isinstance(m, dict):
            continue
        entry = {
            "mcq_id": m.get("mcq_id"),
            "question": m.get("question", ""),
            "options": {
                "A": m.get("options", {}).get("A") or "",
                "B": m.get("options", {}).get("B") or "",
                "C": m.get("options", {}).get("C") or "",
                "D": m.get("options", {}).get("D") or "",
            },
            "correct_answer": {
                "option": m.get("correct_answer", {}).get("option") or "",
                "text": m.get("correct_answer", {}).get("text") or "",
            },
        }
        normalized.append(entry)

    print(f"      Got {len(normalized)} MCQs", flush=True)
    return normalized


# ---------------------------------------------------------------------------
# Gap detection (no refill round)
# ---------------------------------------------------------------------------

def detect_gaps(mcqs: list[dict], expected_max: int) -> list[int]:
    """Return list of mcq_ids missing in range 1..expected_max."""
    if not mcqs:
        return list(range(1, expected_max + 1))
    existing = {m["mcq_id"] for m in mcqs if m.get("mcq_id")}
    expected = set(range(1, expected_max + 1))
    return sorted(expected - existing)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_mcq(m: dict) -> list[str]:
    issues = []
    if not m.get("mcq_id"):
        issues.append("missing mcq_id")
    if not m.get("question"):
        issues.append("missing question")
    opts = m.get("options", {})
    for k in ("A", "B", "C", "D"):
        if not opts.get(k):
            issues.append(f"missing option {k}")
    ca = m.get("correct_answer", {})
    if not ca.get("option"):
        issues.append("missing correct_answer.option")
    if not ca.get("text"):
        issues.append("missing correct_answer.text")
    if ca.get("option") not in ("A", "B", "C", "D", ""):
        issues.append(f"invalid correct_answer.option: {ca['option']}")
    return issues


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    client = get_client()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_lines = []

    for job in CHAPTER_JOBS:
        chapter_id = job["chapter_id"]
        chapter_name = job["chapter_name"]
        expected_max = job["expected_mcq_count"]

        print(f"\n{'='*60}", flush=True)
        print(f"CHAPTER {chapter_id}: {chapter_name} (expected: {expected_max})", flush=True)
        print(f"{'='*60}", flush=True)

        # Batch extraction
        all_mcqs = []
        for batch_start in range(1, expected_max + 1, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE - 1, expected_max)
            batch_mcqs = extract_batch(client, job, batch_start, batch_end)
            all_mcqs.extend(batch_mcqs)
            time.sleep(0.5)

        # Deduplicate (keep last, more complete)
        seen = {}
        for m in all_mcqs:
            mid = m.get("mcq_id")
            if mid is not None:
                seen[mid] = m
        all_mcqs = sorted(seen.values(), key=lambda x: x["mcq_id"])
        print(f"  Extracted {len(all_mcqs)} unique MCQs", flush=True)

        # Gap check (no refill round)
        gap_ids = detect_gaps(all_mcqs, expected_max)
        if gap_ids:
            print(f"  Gaps: {len(gap_ids)} missing IDs ({gap_ids[:10]}...)", flush=True)
        else:
            print(f"  All IDs 1-{expected_max} accounted for ✓", flush=True)

        # Validation
        issue_count = 0
        for m in all_mcqs:
            issues = validate_mcq(m)
            if issues:
                issue_count += 1
                if issue_count <= 5:
                    print(f"    MCQ {m.get('mcq_id')}: {'; '.join(issues)}", flush=True)

        status = "✓" if issue_count == 0 else f"{issue_count} issues"
        print(f"  Validation: {status}", flush=True)

        summary_lines.append(
            f"  Ch{chapter_id} ({chapter_name}): {len(all_mcqs)} MCQs "
            f"(max ID {expected_max}), {issue_count} issues, {len(gap_ids)} gaps"
        )

        # Write output
        chapter_data = Chapter(
            chapter_id=chapter_id,
            chapter_name=chapter_name,
            book_page_range=job["book_page_range"],
            source_file=job["source_file"],
            topics=[Topic(
                topic_id="1",
                topic_name=chapter_name,
                mcqs=[MCQ(**m) for m in all_mcqs],
            )],
        )

        qb = QuestionBank(chapters=[chapter_data])
        output_path = OUTPUT_DIR / f"chapter_{chapter_id}.json"
        output_path.write_text(
            qb.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  Wrote {output_path}", flush=True)

    print(f"\n{'='*60}", flush=True)
    print("SUMMARY", flush=True)
    print(f"{'='*60}", flush=True)
    for line in summary_lines:
        print(line, flush=True)
    print(f"\nDone! 6 generative API calls total.", flush=True)


if __name__ == "__main__":
    main()