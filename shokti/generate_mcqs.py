import json
import time
from pathlib import Path

from google import genai
from google.genai import errors
from google.genai import types
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"
OUTPUT_FILE = ROOT_DIR / "mcqs_with_citations.json"

STORE_DISPLAY_NAME = "biology-hasan-sir"
MODEL = "gemini-3.1-flash-lite-preview"
DIFFICULTY_LEVEL = "mixed: easy 30%, medium 50%, hard 20%"
OUTPUT_LANGUAGE = "Bangla (bn), with important Biology terms in English brackets"

CHAPTER_JOBS = [
    {
        "chapter": "Chapter 06",
        "topic": "ব্রায়োফাইটা ও টেরিডোফাইটা (Bryophyta and Pteridophyta)",
        "book_page_range": "198-208",
        "source_file": "chapter_06_bryophyta_and_pteridophyta_pages_198-208.pdf",
        "number_of_mcqs": 15,
    },
    {
        "chapter": "Chapter 08",
        "topic": "টিস্যু ও টিস্যুতন্ত্র (Tissue and Tissue System)",
        "book_page_range": "235-254",
        "source_file": "chapter_08_tissue_and_tissue_system_pages_235-254.pdf",
        "number_of_mcqs": 15,
    },
]
TOTAL_MCQS = sum(job["number_of_mcqs"] for job in CHAPTER_JOBS)


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
        description="Semantically similar practice/review questions from the same textbook section that test the same concept."
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

    raise RuntimeError(f"GEMINI_API_KEY was not found in {env_file}")


def build_generation_prompt(store_name):
    chapter_lines = "\n".join(
        (
            f"- {job['chapter']}: {job['topic']}, book pages "
            f"{job['book_page_range']}, source file {job['source_file']}, "
            f"generate exactly {job['number_of_mcqs']} MCQs"
        )
        for job in CHAPTER_JOBS
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
3. Do NOT mention Gemini, File Search, AI, retrieval, citations, chunks, or tooling in the output.
4. Each MCQ must have exactly four options: A, B, C, D.
5. correct_answer.option must be exactly one of A, B, C, or D.
6. Questions must be clear, medical-admission-style, non-duplicate, and unambiguous.
7. Difficulty level: {DIFFICULTY_LEVEL}.
8. Output language: {OUTPUT_LANGUAGE}.
9. Keep explanations short and grounded in source content.
10. Return ONLY valid JSON. No markdown or extra text.
11. Generate exactly 15 MCQs from Chapter 06 and exactly 15 MCQs from Chapter 08.
12. Do not include citations or grounding metadata in this step.
13. Strictly skip any question if its answer cannot be grounded in retrieved textbook content.

Required JSON format:
{{
  "topic": "Chapter 06 and Chapter 08",
  "number_of_mcqs": {TOTAL_MCQS},
  "mcqs": [
    {{
      "id": 1,
      "chapter": "Chapter 06",
      "topic": "Chapter topic",
      "source_file": "source PDF file name",
      "book_page_range": "book page range",
      "question": "প্রশ্ন এখানে লিখুন।",
      "options": {{
        "A": "Option A",
        "B": "Option B",
        "C": "Option C",
        "D": "Option D"
      }},
      "correct_answer": {{
        "option": "A",
        "text": "Correct answer text"
      }},
      "explanation": "Short explanation based only on retrieved chapter content.",
      "difficulty": "easy|medium|hard"
    }}
  ]
}}
"""


def build_citation_prompt(generated_mcqs, store_name):
    return f"""
You are an expert source-grounding and practice-question derivation assistant.

Use Gemini File Search on this already-created File Search store:
{store_name}

Your task: For each MCQ, retrieve content from the textbook that both GROUNDS the answer AND can be used to derive practice questions covering the same concept from different angles.

For each MCQ:
1. Ground the answer — find the exact source text that supports the correct answer.
2. Find ADDITIONAL content on the SAME topic but from a DIFFERENT angle — definitions, comparisons, exceptions, examples, clinical relevance, mnemonic aids, or counter-examples. This content should NOT be the same passage used for grounding.

Rules:
1. Use ONLY information retrieved from the File Search store.
2. Do not fabricate content. If the retrieved chunks do not contain practice-question-worthy material, return an empty list for practice_related_questions.
3. source_quote: exact or near-exact phrase from the retrieved chunk that directly supports the correct answer.
4. pdf_page_number: the page number from which the source_quote was retrieved.
5. practice_related_questions: derive 1-3 practice questions from the OTHER retrieved chunks (not the source_quote chunk). These should test the SAME concept as the MCQ but from a different angle. If no such content exists, return [].
6. Do NOT use the same chunk for both source_quote and practice_related_questions — they must come from different retrieved content.
7. Preserve every MCQ exactly: do not rewrite question, options, correct_answer, explanation, chapter, topic, source_file, or book_page_range.
8. Return ONLY valid JSON. No markdown or extra text.

Required JSON format:
{{
  "store_name": "{store_name}",
  "topic": "Chapter 06 and Chapter 08",
  "number_of_mcqs": {TOTAL_MCQS},
  "mcqs": [
    {{
      "id": 1,
      "chapter": "Chapter 06",
      "topic": "Chapter topic",
      "source_file": "source PDF file name",
      "book_page_range": "book page range",
      "question": "same question text",
      "options": {{
        "A": "same option A",
        "B": "same option B",
        "C": "same option C",
        "D": "same option D"
      }},
      "correct_answer": {{
        "option": "A",
        "text": "same correct answer text"
      }},
      "explanation": "same explanation",
      "difficulty": "easy|medium|hard",
      "source_quote": "exact source text supporting the answer",
      "pdf_page_number": null,
      "practice_related_questions": [
        "Derived practice question 1 from different angle",
        "Derived practice question 2 from different angle"
      ]
    }}
  ]
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


def validate_output(data):
    mcqs = data.get("mcqs", [])
    if len(mcqs) != TOTAL_MCQS:
        raise RuntimeError(f"Expected {TOTAL_MCQS} MCQs, got {len(mcqs)}")

    for mcq in mcqs:
        options = mcq.get("options")
        if not isinstance(options, dict) or set(options) != {"A", "B", "C", "D"}:
            raise RuntimeError(f"Invalid options in MCQ {mcq.get('id')}")

        option = mcq.get("correct_answer", {}).get("option")
        if option not in {"A", "B", "C", "D"}:
            raise RuntimeError(f"Invalid correct answer in MCQ {mcq.get('id')}")

    counts = {job["chapter"]: 0 for job in CHAPTER_JOBS}
    for mcq in mcqs:
        job = find_job_for_mcq(mcq)
        counts[job["chapter"]] += 1

    expected = {job["chapter"]: job["number_of_mcqs"] for job in CHAPTER_JOBS}
    if counts != expected:
        raise RuntimeError(f"Expected chapter counts {expected}, got {counts}")


def make_config(system_instruction, store_name, schema_model):
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[
            types.Tool(
                file_search=types.FileSearch(
                    file_search_store_names=[store_name]
                )
            )
        ],
        response_mime_type="application/json",
        response_json_schema=schema_model.model_json_schema(),
    )


def generate_with_retries(client, config, prompt, label):
    for attempt in range(1, 4):
        try:
            print(
                f"{label} with {MODEL} attempt {attempt}/3...",
                flush=True,
            )
            return client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=config,
            )
        except errors.ServerError as exc:
            if attempt == 3:
                raise
            wait_seconds = attempt * 30
            print(f"Server busy: {exc}. Retrying in {wait_seconds}s...", flush=True)
            time.sleep(wait_seconds)


def select_file_search_store(client):
    candidates = [
        store
        for store in client.file_search_stores.list()
        if store.display_name == STORE_DISPLAY_NAME
    ]
    if not candidates:
        raise RuntimeError(f"No File Search store found named {STORE_DISPLAY_NAME!r}")

    stores_with_documents = []
    for store in candidates:
        documents = list(client.file_search_stores.documents.list(parent=store.name))
        document_names = {doc.display_name for doc in documents}
        required_names = {job["source_file"] for job in CHAPTER_JOBS}
        if required_names.issubset(document_names):
            stores_with_documents.append((store, documents))

    if not stores_with_documents:
        raise RuntimeError(
            f"Found stores named {STORE_DISPLAY_NAME!r}, but none contain "
            "all required chapter 6 and 8 documents."
        )

    stores_with_documents.sort(
        key=lambda item: item[0].create_time or "",
        reverse=True,
    )
    return stores_with_documents[0]


def main():
    client = genai.Client(api_key=load_gemini_api_key(ENV_FILE), vertexai=False)
    store, documents = select_file_search_store(client)
    print(f"Using File Search store: {store.name}", flush=True)
    print("Documents:", flush=True)
    for document in documents:
        print(f"- {document.display_name or document.name}", flush=True)

    system_instruction = """
You are an expert exam-question generator.
Use only the retrieved textbook/PDF content.
Do not use outside knowledge.
Never mention any AI tool or platform in the generated JSON.
Return valid JSON only.
"""

    generation_config = make_config(system_instruction, store.name, GeneratedMCQResponse)
    citation_config = make_config(system_instruction, store.name, CitedMCQResponse)

    generation_response = generate_with_retries(
        client,
        generation_config,
        build_generation_prompt(store.name),
        f"Generating {TOTAL_MCQS} MCQs",
    )
    generated = GeneratedMCQResponse.model_validate_json(
        generation_response.text
    ).model_dump()
    validate_output(generated)

    citation_response = generate_with_retries(
        client,
        citation_config,
        build_citation_prompt(generated, store.name),
        "Grounding generated MCQs with citations",
    )
    cited = CitedMCQResponse.model_validate_json(
        citation_response.text
    ).model_dump()
    validate_output(cited)

    all_mcqs = [
        normalize_mcq(mcq, index)
        for index, mcq in enumerate(cited["mcqs"], start=1)
    ]

    output = {
        "store_name": store.name,
        "topic": "Chapter 06 and Chapter 08",
        "number_of_mcqs": len(all_mcqs),
        "difficulty": DIFFICULTY_LEVEL,
        "chapters": CHAPTER_JOBS,
        "mcqs": all_mcqs,
    }

    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
