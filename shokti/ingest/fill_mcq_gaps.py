"""Fill MCQ gaps for chapters 01-03 using discovery + targeted extraction.

Usage:
    python shokti/ingest/fill_mcq_gaps.py

Two-phase approach:
1. Discover all existing MCQ IDs in each chapter (1 call per chapter)
2. Extract only the missing IDs by querying discovered ranges (30 per call)
"""

import json
import re
import time
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from shokti.core.config import GEMINI


ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
QUESTION_BANK_DIR = ROOT / "question_bank"

MEDICAL_STORE_NAME = GEMINI.STORE_NAME
MODEL = GEMINI.MODEL
RANGE_SIZE = 30

CHAPTER_JOBS = [
    {"chapter_id": "01", "chapter_name": "কোষ ও এর গঠন", "expected": 205},
    {"chapter_id": "02", "chapter_name": "কোষ বিভাজন", "expected": 96},
    {"chapter_id": "03", "chapter_name": "কোষ রসায়ন", "expected": 123},
]


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


def query_store(client, prompt: str) -> str:
    config = types.GenerateContentConfig(
        tools=[types.Tool(file_search=types.FileSearch(
            file_search_store_names=[MEDICAL_STORE_NAME]
        ))],
    )
    response = client.models.generate_content(model=MODEL, contents=prompt, config=config)
    return response.text


def extract_json(text: str) -> list:
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


# Phase 1: Discover all MCQ IDs in a chapter
PROMPT_DISCOVER = """From medical_qbank.pdf in the File Search store (free_medical_qbank), list ALL MCQ question IDs/numbers that appear in Chapter {chapter_id} ({chapter_name}).

Search thoroughly through the chapter. Return a JSON array of all the MCQ IDs you find.
Format: [1, 2, 3, 4, 5, ...] — just an array of integers, no other text.

Only include IDs that are actual MCQ numbers (question numbers) in this chapter.
Do not include IDs from other chapters."""


def discover_chapter_ids(client, chapter_id, chapter_name) -> set[int]:
    prompt = PROMPT_DISCOVER.format(chapter_id=chapter_id, chapter_name=chapter_name)
    print(f"    Discovering MCQ IDs for chapter {chapter_id}...", end=" ", flush=True)
    text = query_store(client, prompt)
    ids = extract_json(text)
    discovered = set()
    for item in ids:
        if isinstance(item, int):
            discovered.add(item)
        elif isinstance(item, str):
            for part in item.split(","):
                try:
                    discovered.add(int(part.strip()))
                except ValueError:
                    pass
    print(f"found {len(discovered)} MCQ IDs", flush=True)
    return discovered


# Phase 2: Extract MCQs for a specific ID range
PROMPT_EXTRACT = """From medical_qbank.pdf in the File Search store (free_medical_qbank), extract MCQs with IDs in range {id_start}–{id_end} from Chapter {chapter_id} ({chapter_name}).

TASK:
1. Find MCQs with IDs between {id_start} and {id_end} in this chapter
2. Extract: mcq_id, question, options A/B/C/D, correct_answer
3. Correct answer: ক→A, খ→B, গ→C, ঘ→D; set correct_answer.text to EXACT option text
4. Do NOT skip any MCQ in the range

Return only a valid JSON array of MCQ objects. No markdown.
[{{"mcq_id": 1, "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_answer": {{"option": "A", "text": "..."}}}}]"""


def extract_range(client, chapter_id, chapter_name, id_start, id_end) -> list[dict]:
    prompt = PROMPT_EXTRACT.format(
        chapter_id=chapter_id, chapter_name=chapter_name,
        id_start=id_start, id_end=id_end,
    )
    print(f"    Querying IDs {id_start}–{id_end}...", end=" ", flush=True)
    text = query_store(client, prompt)
    mcqs = extract_json(text)
    normalized = []
    for m in mcqs:
        if not isinstance(m, dict) or m.get("mcq_id") is None:
            continue
        normalized.append({
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
        })
    print(f"got {len(normalized)}", flush=True)
    return normalized


# Load existing MCQs from chapter JSON
def load_existing(chapter_id):
    path = QUESTION_BANK_DIR / f"chapter_{chapter_id}.json"
    if not path.exists():
        return [], "unknown"
    with open(path) as f:
        data = json.load(f)
    ch_data = data["chapters"][0]
    mcqs = ch_data["topics"][0]["mcqs"]
    book_page_range = ch_data.get("book_page_range", "unknown")
    return mcqs, book_page_range


def save_chapter(chapter_id, chapter_name, book_page_range, all_mcqs):
    class MCQOption(BaseModel):
        A: str = ""; B: str = ""; C: str = ""; D: str = ""

    class CorrectAnswer(BaseModel):
        option: str = ""; text: str = ""

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

    qb = QuestionBank(chapters=[Chapter(
        chapter_id=chapter_id,
        chapter_name=chapter_name,
        book_page_range=book_page_range,
        topics=[Topic(topic_id="1", topic_name=chapter_name, mcqs=[MCQ(**m) for m in all_mcqs])],
    )])
    path = QUESTION_BANK_DIR / f"chapter_{chapter_id}.json"
    path.write_text(qb.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")


def fill_chapter(client, job):
    chapter_id = job["chapter_id"]
    chapter_name = job["chapter_name"]
    expected = job["expected"]

    # Load existing
    existing, book_page_range = load_existing(chapter_id)
    seen = {m["mcq_id"]: m for m in existing if m.get("mcq_id")}
    print(f"\n  Chapter {chapter_id}: {len(seen)} existing MCQs")

    # Phase 1: Discover all MCQ IDs in chapter
    discovered_ids = discover_chapter_ids(client, chapter_id, chapter_name)
    print(f"  Discovered {len(discovered_ids)} MCQ IDs in chapter")

    # IDs that still need to be filled
    missing_from_existing = discovered_ids - set(seen.keys())
    print(f"  Missing from existing: {len(missing_from_existing)}")

    if not missing_from_existing:
        print(f"  Nothing to fill! ✓")
        return 0

    # Phase 2: Build non-overlapping ranges from discovered missing IDs
    sorted_missing = sorted(missing_from_existing)
    ranges = []
    range_start = sorted_missing[0]
    range_end = sorted_missing[0]

    for mid in sorted_missing[1:]:
        if mid <= range_end + 5:  # Allow small gaps within range
            range_end = mid
        else:
            ranges.append((range_start, range_end))
            range_start = mid
            range_end = mid
    ranges.append((range_start, range_end))

    print(f"  {len(ranges)} ranges to query")
    new_found = 0

    for r_start, r_end in ranges:
        results = extract_range(client, chapter_id, chapter_name, r_start, r_end)
        for m in results:
            mid = m["mcq_id"]
            if mid not in seen or not seen[mid].get("correct_answer", {}).get("option"):
                seen[mid] = m
                new_found += 1
        time.sleep(0.5)

    all_mcqs = sorted(seen.values(), key=lambda x: x["mcq_id"])
    print(f"  Total: {len(all_mcqs)} MCQs, {new_found} newly filled")

    # Count remaining gaps among discovered IDs
    gaps = [i for i in sorted(discovered_ids) if i not in seen]
    print(f"  Remaining gaps (among discovered): {len(gaps)}")
    if gaps and len(gaps) <= 10:
        print(f"  Gaps: {gaps}")
    elif gaps:
        print(f"  Sample gaps: {gaps[:10]}")

    save_chapter(chapter_id, chapter_name, book_page_range, all_mcqs)
    return len(gaps)


def main():
    client = get_client()
    for job in CHAPTER_JOBS:
        print(f"\n{'='*50}", flush=True)
        print(f"Chapter {job['chapter_id']}: {job['chapter_name']}", flush=True)
        fill_chapter(client, job)
    print(f"\nDone!", flush=True)


if __name__ == "__main__":
    main()