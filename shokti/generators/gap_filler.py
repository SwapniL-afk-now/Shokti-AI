"""MCQ Generator for coverage gaps."""

import argparse
import json
import sqlite3
import time

from google import genai
from google.genai import errors
from google.genai import types
from pydantic import BaseModel, Field

from shokti.core.config import ENV_FILE, DB_PATH, GEMINI, MCQ


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
    source_quote: str = Field(description="Short exact or near-exact source text.")
    pdf_page_number: int | None = None
    practice_related_questions: list[str] = Field(
        description="Semantically similar practice questions."
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


def get_gap_topics(conn):
    gaps = conn.execute("""
        SELECT
            qb.book_id, qb.subject, qb.chapter_id, qb.chapter_name,
            qb.topic_id, qb.topic_name,
            COUNT(*) as mcq_count, qb.book_page_range, qb.source_file
        FROM question_bank qb
        GROUP BY qb.book_id, qb.chapter_id, qb.topic_id
        HAVING COUNT(*) < ?
        ORDER BY COUNT(*) ASC, qb.topic_name
    """, (MCQ.GAP_THRESHOLD,)).fetchall()
    return [dict(row) for row in gaps]


def get_example_mcqs(conn, chapter_id, exclude_topic_id):
    mcqs = conn.execute("""
        SELECT question, options, correct_answer, difficulty
        FROM question_bank
        WHERE chapter_id = ? AND topic_id != ?
        ORDER BY RANDOM()
        LIMIT 5
    """, (chapter_id, exclude_topic_id)).fetchall()
    return [dict(row) for row in mcqs]


def build_generation_prompt(topic_data, examples, count: int | None = None, practice_context: dict | None = None):
    examples_text = json.dumps(examples[:3], ensure_ascii=False, indent=2)
    context_text = ""
    if practice_context:
        context_text = f"""
practice_session_context:
{json.dumps(practice_context, ensure_ascii=False, indent=2)}
"""
    min_mcqs = count if count is not None else MCQ.MCQS_PER_GAP
    exact_or_min = f"exactly {count}" if count is not None else f"at least {min_mcqs}"

    # Use difficulty split from practice_context if available, otherwise fall back to config
    if practice_context and "desired_difficulty_split" in practice_context:
        split = practice_context["desired_difficulty_split"]
        difficulty_str = (
            f"easy {int(split.get('easy', MCQ.DIFFICULTY_EASY_RATIO) * 100)}%, "
            f"medium {int(split.get('medium', MCQ.DIFFICULTY_MEDIUM_RATIO) * 100)}%, "
            f"hard {int(split.get('hard', MCQ.DIFFICULTY_HARD_RATIO) * 100)}%"
        )
    else:
        difficulty_str = (
            f"easy {int(MCQ.DIFFICULTY_EASY_RATIO * 100)}%, "
            f"medium {int(MCQ.DIFFICULTY_MEDIUM_RATIO * 100)}%, "
            f"hard {int(MCQ.DIFFICULTY_HARD_RATIO * 100)}%"
        )

    return f"""
You are an expert Bangladeshi medical admission Biology question setter.

Generate {exact_or_min} quality MCQs for this topic:

Topic: {topic_data['topic_name']}
Chapter: {topic_data['chapter_id']} — {topic_data['chapter_name']}
Book pages: {topic_data['book_page_range']}
Source file: {topic_data['source_file']}

Use Gemini File Search on the "{GEMINI.STORE_DISPLAY_NAME}" store.
{context_text}

Rules:
1. Use ONLY content retrieved from File Search as ground truth.
2. Each MCQ must have exactly four options: A, B, C, D.
3. Match the STYLE of these examples:
{examples_text}
4. Do not duplicate any provided qbank, generated, weak-risk, or coverage examples.
5. Difficulty: {difficulty_str}.
6. If practice_session_context exists, generate questions that diagnose its target weakness or coverage gap.
7. Output language: {MCQ.OUTPUT_LANGUAGE}.
8. If quality grounded content is limited, generate fewer MCQs — quality over quantity.
9. Return ONLY valid JSON. No markdown.

JSON format:
{{"topic": "{topic_data['topic_name']}", "number_of_mcqs": ..., "mcqs": [{{...}}]}}
"""


def build_citation_prompt(generated_mcqs, store_name):
    mcqs_text = json.dumps(generated_mcqs.get("mcqs", []), ensure_ascii=False, indent=2)
    return f"""
Use Gemini File Search on this store: {store_name}

For each MCQ below, retrieve content that GROUNDS the correct answer.

MCQs to ground:
{mcqs_text}

For each MCQ, return:
1. source_quote: exact text (1-2 sentences) supporting the correct answer.
2. pdf_page_number: page number from the retrieved content.
3. practice_related_questions: find 2-3 OTHER MCQs in the store semantically similar to this one (by topic or concept).

Return ONLY valid JSON with this exact format:
{{"store_name": "{store_name}", "topic": "...", "number_of_mcqs": ..., "mcqs": [{{"id": ..., "source_quote": "...", "pdf_page_number": ..., "practice_related_questions": [...]}}]}}
"""


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
            print(f"  {label} (attempt {attempt}/{GEMINI.MAX_RETRIES})...", flush=True)
            return client.models.generate_content(
                model=GEMINI.MODEL,
                contents=prompt,
                config=config,
            )
        except errors.ServerError as exc:
            if attempt == GEMINI.MAX_RETRIES:
                raise
            wait_seconds = attempt * GEMINI.RETRY_DELAY_BASE
            print(f"    Server busy. Retrying in {wait_seconds}s...", flush=True)
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
        stores_with_documents.append((store, documents))

    stores_with_documents.sort(key=lambda item: item[0].create_time or "", reverse=True)
    return stores_with_documents[0]


def insert_mcqs(conn, mcqs, topic_data):
    inserted_count = 0
    inserted_rows = []
    for mcq in mcqs:
        options_json = json.dumps(mcq.get("options", {}), ensure_ascii=False)
        correct_json = json.dumps(mcq.get("correct_answer", {}), ensure_ascii=False)
        try:
            cursor = conn.execute("""
                INSERT INTO question_bank (
                    subject, book_id, chapter_id, chapter_name, book_page_range, source_file,
                    topic_id, topic_name, question, options, correct_answer,
                    source_quote, pdf_page_number, practice_related_questions,
                    appearance_counter, difficulty, origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                topic_data.get("subject", "biology"), topic_data.get("book_id", ""),
                topic_data["chapter_id"], topic_data["chapter_name"],
                topic_data["book_page_range"], topic_data["source_file"],
                topic_data["topic_id"], topic_data["topic_name"],
                mcq.get("question"), options_json, correct_json,
                mcq.get("source_quote", ""), mcq.get("pdf_page_number"),
                json.dumps(mcq.get("practice_related_questions", []), ensure_ascii=False),
                0, mcq.get("difficulty", "medium"), "generated",
            ))
            inserted_count += 1
            inserted_rows.append({
                "id": cursor.lastrowid,
                "chapter_id": topic_data["chapter_id"],
                "chapter_name": topic_data["chapter_name"],
                "topic_id": topic_data["topic_id"],
                "topic_name": topic_data["topic_name"],
                "question": mcq.get("question"),
                "options": mcq.get("options"),
                "correct_answer": mcq.get("correct_answer"),
                "explanation": mcq.get("explanation"),
                "difficulty": mcq.get("difficulty", "medium"),
                "book_page_range": topic_data.get("book_page_range"),
                "source_quote": mcq.get("source_quote", ""),
                "pdf_page_number": mcq.get("pdf_page_number"),
                "origin": "generated",
            })
        except sqlite3.IntegrityError:
            print(f"    Skipping duplicate: {mcq.get('question', '')[:50]}...")
    conn.commit()
    return inserted_count, inserted_rows


def fill_topic(gap: dict, conn: sqlite3.Connection, client, store_name: str,
               gen_config, cite_config, count: int | None = None,
               practice_context: dict | None = None) -> tuple[int, list[dict]]:
    """
    Fill a single gap topic by generating and inserting MCQs.

    Args:
        gap: dict with keys topic_id, topic_name, chapter_id, chapter_name,
             book_page_range, source_file, mcq_count
        conn: sqlite3 connection (open, row_factory set)
        client: genai.Client
        store_name: Gemini File Search store name
        gen_config: GenerateContentConfig for MCQ generation
        cite_config: GenerateContentConfig for citation grounding
        count: if set, request exactly this many MCQs from Gemini (default: None = gap threshold)

    Returns:
        Tuple of (number of MCQs inserted, list of inserted MCQ dicts).
    """
    if count is not None:
        print(f"  Generating exactly {count} MCQs for: {gap['topic_name']}")
    else:
        print(f"  Generating MCQs for: {gap['topic_name']}")
        print(f"  Current: {gap['mcq_count']} | Need: {MCQ.GAP_THRESHOLD - gap['mcq_count']} more")

    examples = get_example_mcqs(conn, gap["chapter_id"], gap["topic_id"])

    try:
        gen_response = generate_with_retries(
            client, gen_config,
            build_generation_prompt(gap, examples, count=count, practice_context=practice_context),
            "Generating MCQs",
        )
        generated = GeneratedMCQResponse.model_validate_json(gen_response.text).model_dump()

        cite_response = generate_with_retries(
            client, cite_config,
            build_citation_prompt(generated, store_name),
            "Grounding with citations",
        )
        cited = CitedMCQResponse.model_validate_json(cite_response.text).model_dump()

        mcqs = cited.get("mcqs", [])
        inserted_count, inserted_rows = insert_mcqs(conn, mcqs, gap)
        print(f"  Inserted: {inserted_count} MCQs")
        return inserted_count, inserted_rows

    except Exception as e:
        print(f"  Error: {e}")
        return 0, []


def generate_fresh_mcqs(
    topic_name: str,
    chapter_id: str,
    chapter_name: str,
    book_page_range: str,
    source_file: str,
    count: int,
    conn: sqlite3.Connection,
    client,
    store_name: str,
    gen_config,
    cite_config,
    practice_context: dict | None = None,
    allow_existing_fallback: bool = True,
) -> tuple[int, list[dict]]:
    """
    Generate exactly `count` fresh MCQs for a topic, bypassing gap threshold.
    Always adds to the generated pool regardless of existing MCQ count.

    Returns:
        Tuple of (total_inserted_count, list of inserted MCQ dicts with full data).
    """
    topic_row = conn.execute(
        "SELECT topic_id, subject, book_id FROM question_bank WHERE LOWER(topic_name)=LOWER(?) LIMIT 1",
        (topic_name,),
    ).fetchone()
    topic_id = topic_row["topic_id"] if topic_row else ""
    subject = topic_row["subject"] if topic_row else "biology"
    book_id = topic_row["book_id"] if topic_row else ""

    gap = {
        "topic_id": topic_id,
        "topic_name": topic_name,
        "subject": subject,
        "book_id": book_id,
        "chapter_id": chapter_id,
        "chapter_name": chapter_name,
        "book_page_range": book_page_range,
        "source_file": source_file,
        "mcq_count": 0,
    }
    inserted_count, inserted_rows = fill_topic(
        gap,
        conn,
        client,
        store_name,
        gen_config,
        cite_config,
        count=count,
        practice_context=practice_context,
    )

    # Fill remaining slots from existing generated MCQs for this topic
    remaining = count - inserted_count
    if allow_existing_fallback and remaining > 0:
        existing = [dict(r) for r in conn.execute("""
            SELECT * FROM question_bank
            WHERE origin = 'generated'
              AND LOWER(topic_name) = LOWER(?)
              AND question IS NOT NULL
            LIMIT ?
        """, (topic_name, remaining)).fetchall()]

        fallback_rows = []
        for mcq in existing:
            opts_json = mcq.get("options")
            if isinstance(opts_json, str):
                opts_json = json.dumps(json.loads(opts_json), ensure_ascii=False)
            ca_json = mcq.get("correct_answer")
            if isinstance(ca_json, str):
                ca_json = json.dumps(json.loads(ca_json), ensure_ascii=False)
            try:
                cursor = conn.execute("""
                    INSERT INTO question_bank (
                        subject, book_id, chapter_id, chapter_name, book_page_range, source_file,
                        topic_id, topic_name, question, options, correct_answer,
                        source_quote, pdf_page_number, practice_related_questions,
                        appearance_counter, difficulty, origin
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'generated')
                """, (
                    subject, book_id, chapter_id, chapter_name,
                    book_page_range, source_file,
                    topic_id, topic_name,
                    mcq.get("question"), opts_json, ca_json,
                    mcq.get("source_quote", ""), mcq.get("pdf_page_number"),
                    json.dumps(mcq.get("practice_related_questions", []), ensure_ascii=False),
                    0, mcq.get("difficulty", "medium"),
                ))
                inserted_count += 1
                row_dict = {
                    "id": cursor.lastrowid,
                    "chapter_id": chapter_id,
                    "chapter_name": chapter_name,
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "question": mcq.get("question"),
                    "options": mcq.get("options") if not isinstance(mcq.get("options"), str) else json.loads(mcq.get("options")),
                    "correct_answer": mcq.get("correct_answer") if not isinstance(mcq.get("correct_answer"), str) else json.loads(mcq.get("correct_answer")),
                    "difficulty": mcq.get("difficulty", "medium"),
                    "book_page_range": book_page_range,
                    "source_quote": mcq.get("source_quote", ""),
                    "pdf_page_number": mcq.get("pdf_page_number"),
                    "origin": "generated",
                }
                fallback_rows.append(row_dict)
                remaining -= 1
                if remaining <= 0:
                    break
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        inserted_rows.extend(fallback_rows)

    return inserted_count, inserted_rows


def setup_generator() -> tuple:
    """Initialize Gemini client, store, and configs. Returns (client, store_name, gen_config, cite_config)."""
    client = genai.Client(api_key=load_gemini_api_key(ENV_FILE), vertexai=False)
    store, _ = select_file_search_store(client)
    system_instruction = "Use only retrieved textbook content. Return valid JSON only."
    gen_config = make_config(system_instruction, store.name, GeneratedMCQResponse)
    cite_config = make_config(system_instruction, store.name, CitedMCQResponse)
    return client, store.name, gen_config, cite_config


def main():
    parser = argparse.ArgumentParser(description="Generate MCQs for coverage gaps")
    parser.add_argument("--topic", type=str, help="Process only this topic_id")
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't save")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    gaps = get_gap_topics(conn)
    if not gaps:
        print("No coverage gaps found.")
        conn.close()
        return

    if args.topic:
        gaps = [g for g in gaps if g["topic_id"] == args.topic]
        if not gaps:
            print(f"No gap found for topic_id: {args.topic}")
            conn.close()
            return

    print(f"=== MCQ Gap Generator ===")
    print(f"Found {len(gaps)} gap topic(s)\n")

    client, store_name, gen_config, cite_config = setup_generator()
    print(f"Using File Search store: {store_name}\n")

    total_generated = 0

    for gap in gaps:
        if args.dry_run:
            print(f"[DRY RUN] Would generate MCQs for: {gap['topic_name']}")
        else:
            count = fill_topic(gap, conn, client, store_name, gen_config, cite_config)
            total_generated += count
        print()

    print(f"Done! Generated {total_generated} new MCQs.")
    conn.close()


if __name__ == "__main__":
    main()
