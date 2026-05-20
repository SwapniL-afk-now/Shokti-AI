"""MCQ Generator for full chapters using File Search."""

import json
import time

from google import genai
from google.genai import errors
from google.genai import types
from pydantic import BaseModel, Field

from shokti.core.config import ENV_FILE, OUTPUT_DIR, GEMINI, MCQ

OUTPUT_FILE = OUTPUT_DIR / "mcqs_with_citations.json"
TOTAL_MCQS = sum(job["number_of_mcqs"] for job in MCQ.CHAPTER_JOBS)
CHAPTER_JOBS = MCQ.CHAPTER_JOBS


class Options(BaseModel):
    A: str
    B: str
    C: str
    D: str


class CorrectAnswer(BaseModel):
    option: str = Field(description="One of A, B, C, or D.")
    text: str


class GeneratedMCQ(BaseModel):
    id: int
    chapter: str
    topic: str
    source_file: str
    book_page_range: str
    question: str
    options: Options
    correct_answer: CorrectAnswer
    explanation: str
    difficulty: str


class GeneratedMCQResponse(BaseModel):
    topic: str
    number_of_mcqs: int
    mcqs: list[GeneratedMCQ]


class CitedMCQ(GeneratedMCQ):
    source_quote: str = Field(
        description="Short exact or near-exact source text retrieved from the book."
    )
    pdf_page_number: int | None = None
    practice_related_questions: list[str] = Field(
        description="Semantically similar practice questions from the same textbook section."
    )


class CitedMCQResponse(BaseModel):
    store_name: str
    topic: str
    number_of_mcqs: int
    mcqs: list[CitedMCQ]


def load_gemini_api_key(env_file):
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "GEMINI_API_KEY":
            return value.strip().strip("\"'")
    raise RuntimeError(f"GEMINI_API_KEY not found in {env_file}")


def build_generation_prompt(store_name):
    chapter_lines = "\n".join(
        f"- {job['chapter']}: {job['topic']}, book pages "
        f"{job['book_page_range']}, source file {job['source_file']}, "
        f"generate exactly {job['number_of_mcqs']} MCQs"
        for job in CHAPTER_JOBS
    )
    difficulty_level = (
        f"mixed: easy {int(MCQ.DIFFICULTY_EASY_RATIO*100)}%, "
        f"medium {int(MCQ.DIFFICULTY_MEDIUM_RATIO*100)}%, "
        f"hard {int(MCQ.DIFFICULTY_HARD_RATIO*100)}%"
    )
    return f"""
You are an expert Bangladeshi medical admission Biology question setter.

Use Gemini File Search on this already-created File Search store:
{store_name}

Generate exactly {TOTAL_MCQS} MCQs from ONLY these uploaded chapters:
{chapter_lines}

Rules:
1. Use ONLY content retrieved from the File Search store.
2. Do NOT use outside knowledge.
3. Each MCQ must have exactly four options: A, B, C, D.
4. correct_answer.option must be exactly one of A, B, C, or D.
5. Questions must be clear, medical-admission-style, non-duplicate.
6. Difficulty level: {difficulty_level}.
7. Output language: {MCQ.OUTPUT_LANGUAGE}.
8. Return ONLY valid JSON. No markdown.

Required JSON format:
{{
  "topic": "Chapter 06 and Chapter 08",
  "number_of_mcqs": {TOTAL_MCQS},
  "mcqs": [{{
    "id": 1,
    "chapter": "Chapter 06",
    "topic": "Chapter topic",
    "source_file": "source PDF file name",
    "book_page_range": "book page range",
    "question": "প্রশ্ন এখানে লিখুন।",
    "options": {{"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"}},
    "correct_answer": {{"option": "A", "text": "Correct answer text"}},
    "explanation": "Short explanation.",
    "difficulty": "easy|medium|hard"
  }}]
}}
"""


def build_citation_prompt(generated_mcqs, store_name):
    return f"""
You are an expert source-grounding assistant.

Use Gemini File Search on this store: {store_name}

For each MCQ, retrieve content that GROUNDS the correct answer.

Rules:
1. Use ONLY information from File Search store.
2. source_quote: exact text supporting the correct answer.
3. pdf_page_number: page number from retrieved content.
4. practice_related_questions: 1-3 related questions from different angles.
5. Preserve ALL original MCQ fields exactly.
6. Return ONLY valid JSON.

Required JSON format:
{{
  "store_name": "{store_name}",
  "topic": "{generated_mcqs['topic']}",
  "number_of_mcqs": {generated_mcqs['number_of_mcqs']},
  "mcqs": [{{
    "id": 1,
    "chapter": "...",
    "source_quote": "exact source text",
    "pdf_page_number": 42,
    "practice_related_questions": []
  }}]
}}
"""


def find_job_for_mcq(mcq):
    source_file = mcq.get("source_file")
    chapter = mcq.get("chapter")
    for job in CHAPTER_JOBS:
        if source_file == job["source_file"] or chapter == job["chapter"]:
            return job
    return CHAPTER_JOBS[0]


def normalize_mcq(mcq, global_id):
    job = find_job_for_mcq(mcq)
    return {
        "id": global_id,
        "chapter": job["chapter"],
        "topic": job["topic"],
        "source_file": job["source_file"],
        "book_page_range": job["book_page_range"],
        "question": mcq.get("question"),
        "options": mcq.get("options"),
        "correct_answer": mcq.get("correct_answer"),
        "explanation": mcq.get("explanation"),
        "difficulty": mcq.get("difficulty"),
        "pdf_page_number": mcq.get("pdf_page_number"),
        "source_quote": mcq.get("source_quote"),
        "practice_related_questions": mcq.get("practice_related_questions", []),
    }


def make_config(system_instruction, store_name, schema_model):
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[types.Tool(file_search=types.FileSearch(
            file_search_store_names=[store_name]
        ))],
        response_mime_type="application/json",
        response_json_schema=schema_model.model_json_schema(),
    )


def generate_with_retries(client, config, prompt, label):
    for attempt in range(1, GEMINI.MAX_RETRIES + 1):
        try:
            print(f"{label} with {GEMINI.MODEL} attempt {attempt}/{GEMINI.MAX_RETRIES}...", flush=True)
            return client.models.generate_content(
                model=GEMINI.MODEL,
                contents=prompt,
                config=config,
            )
        except errors.ServerError as exc:
            if attempt == GEMINI.MAX_RETRIES:
                raise
            wait_seconds = attempt * GEMINI.RETRY_DELAY_BASE
            print(f"Server busy: {exc}. Retrying in {wait_seconds}s...", flush=True)
            time.sleep(wait_seconds)


def select_file_search_store(client):
    candidates = [
        store for store in client.file_search_stores.list()
        if store.display_name == GEMINI.STORE_DISPLAY_NAME
    ]
    if not candidates:
        raise RuntimeError(f"No File Search store found: {GEMINI.STORE_DISPLAY_NAME}")

    stores_with_documents = []
    for store in candidates:
        documents = list(client.file_search_stores.documents.list(parent=store.name))
        document_names = {doc.display_name for doc in documents}
        required_names = {job["source_file"] for job in CHAPTER_JOBS}
        if required_names.issubset(document_names):
            stores_with_documents.append((store, documents))

    if not stores_with_documents:
        raise RuntimeError(f"Found stores, but none contain all required documents.")
    
    stores_with_documents.sort(key=lambda item: item[0].create_time or "", reverse=True)
    return stores_with_documents[0]


def main():
    client = genai.Client(api_key=load_gemini_api_key(ENV_FILE), vertexai=False)
    store, documents = select_file_search_store(client)
    print(f"Using File Search store: {store.name}", flush=True)

    system_instruction = """
You are an expert exam-question generator.
Use only retrieved textbook content.
Do not use outside knowledge.
Return valid JSON only.
"""
    generation_config = make_config(system_instruction, store.name, GeneratedMCQResponse)
    citation_config = make_config(system_instruction, store.name, CitedMCQResponse)

    gen_response = generate_with_retries(
        client, generation_config,
        build_generation_prompt(store.name),
        f"Generating {TOTAL_MCQS} MCQs",
    )
    generated = GeneratedMCQResponse.model_validate_json(gen_response.text).model_dump()

    cite_response = generate_with_retries(
        client, citation_config,
        build_citation_prompt(generated, store.name),
        "Grounding with citations",
    )
    cited = CitedMCQResponse.model_validate_json(cite_response.text).model_dump()

    all_mcqs = [normalize_mcq(mcq, i) for i, mcq in enumerate(cited["mcqs"], start=1)]

    output = {
        "store_name": store.name,
        "topic": "Chapter 06 and Chapter 08",
        "number_of_mcqs": len(all_mcqs),
        "difficulty": f"mixed: easy 30%, medium 50%, hard 20%",
        "chapters": CHAPTER_JOBS,
        "mcqs": all_mcqs,
    }

    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()