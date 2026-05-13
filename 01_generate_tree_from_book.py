import os
import json
import time
import argparse
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF
from dotenv import load_dotenv
from google import genai
from google.genai import types


load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

if not API_KEY:
    raise ValueError("Missing GEMINI_API_KEY in .env")

client = genai.Client(api_key=API_KEY)

MAX_GEMINI_PDF_BYTES = 50 * 1024 * 1024
TARGET_CHUNK_BYTES = 45 * 1024 * 1024


def load_json(path: Path, default: Any):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_json_loads(text: str) -> Dict[str, Any]:
    text = text.strip()

    if text.startswith("```json"):
        text = text.replace("```json", "").replace("```", "").strip()
    elif text.startswith("```"):
        text = text.replace("```", "").strip()

    return json.loads(text)


def make_slug(text: str) -> str:
    allowed = []
    for ch in text.lower().strip():
        if ch.isalnum():
            allowed.append(ch)
        elif ch in [" ", "-", "_", "/", "."]:
            allowed.append("_")
    slug = "".join(allowed)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "node"


def assign_node_ids(node: Dict[str, Any], prefix: str):
    name = node.get("name", "Unnamed")
    node["id"] = node.get("id") or f"{prefix}_{make_slug(name)}"
    node["type"] = node.get("type", "topic")
    node["children"] = node.get("children", [])

    for i, child in enumerate(node["children"], start=1):
        child_prefix = f"{node['id']}_{i}"
        assign_node_ids(child, child_prefix)


def file_state_name(uploaded_file: Any) -> str:
    state = getattr(uploaded_file, "state", None)
    if state is None:
        return "ACTIVE"
    return getattr(state, "name", str(state))


def wait_for_file_active(uploaded_file: Any, poll_seconds: int = 5, timeout_seconds: int = 300):
    start = time.time()

    while True:
        state_name = file_state_name(uploaded_file)

        if state_name == "ACTIVE":
            return uploaded_file
        if state_name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {getattr(uploaded_file, 'name', 'uploaded file')}")
        if time.time() - start > timeout_seconds:
            raise TimeoutError(
                f"Timed out waiting for Gemini file processing. "
                f"Last state: {state_name}"
            )

        print(f"Waiting for uploaded file to process: {state_name}")
        time.sleep(poll_seconds)
        uploaded_file = client.files.get(name=uploaded_file.name)


def save_pdf_pages(source_doc: fitz.Document, start_page: int, end_page: int, out_path: Path):
    chunk_doc = fitz.open()
    chunk_doc.insert_pdf(source_doc, from_page=start_page, to_page=end_page - 1)
    chunk_doc.save(str(out_path), garbage=4, deflate=True)
    chunk_doc.close()


def open_pdf(path: Path, password: Optional[str] = None) -> fitz.Document:
    doc = fitz.open(str(path))
    if doc.needs_pass:
        if not password:
            raise ValueError(f"{path} is password-protected. Pass --password <password>.")
        if not doc.authenticate(password):
            raise ValueError(f"Wrong password for {path}.")
    return doc


def split_pdf_for_gemini(book_path: Path, temp_dir: Path, password: Optional[str] = None) -> List[Path]:
    """
    Gemini accepts PDFs up to 50 MB. Split oversized textbooks into smaller
    page-range PDFs so the whole book can still be sent in one model request.
    """
    file_size = book_path.stat().st_size
    if file_size <= MAX_GEMINI_PDF_BYTES:
        return [book_path]

    doc = open_pdf(book_path, password)
    total_pages = doc.page_count
    estimated_pages = max(1, int(total_pages * TARGET_CHUNK_BYTES / file_size))

    chunks = []
    start_page = 0

    while start_page < total_pages:
        end_page = min(total_pages, start_page + estimated_pages)

        while True:
            chunk_path = temp_dir / f"{book_path.stem}_pages_{start_page + 1}_{end_page}.pdf"
            save_pdf_pages(doc, start_page, end_page, chunk_path)

            if chunk_path.stat().st_size <= MAX_GEMINI_PDF_BYTES:
                break

            if end_page - start_page == 1:
                raise ValueError(
                    f"Single page {start_page + 1} exceeds Gemini's 50 MB PDF limit. "
                    "Please compress the PDF before running this script."
                )

            chunk_path.unlink()
            end_page = start_page + max(1, (end_page - start_page) // 2)

        chunks.append(chunk_path)
        start_page = end_page

    doc.close()
    return chunks


def upload_pdf_parts(book_path: Path, password: Optional[str] = None) -> List[Any]:
    file_size_mb = book_path.stat().st_size / (1024 * 1024)
    print(f"Book size: {file_size_mb:.1f} MB")

    with tempfile.TemporaryDirectory(prefix="gemini_pdf_chunks_") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        pdf_parts = split_pdf_for_gemini(book_path, temp_dir, password=password)

        if len(pdf_parts) > 1:
            print(f"PDF exceeds Gemini's 50 MB limit; split into {len(pdf_parts)} upload parts.")

        uploaded_files = []
        for index, pdf_part in enumerate(pdf_parts, start=1):
            part_size_mb = pdf_part.stat().st_size / (1024 * 1024)
            print(f"Uploading PDF part {index}/{len(pdf_parts)}: {pdf_part.name} ({part_size_mb:.1f} MB)")
            uploaded_file = client.files.upload(
                file=str(pdf_part),
                config=types.UploadFileConfig(
                    mime_type="application/pdf",
                    display_name=f"{book_path.name} part {index}",
                ),
            )
            uploaded_files.append(wait_for_file_active(uploaded_file))

        return uploaded_files


def generate_tree_from_book(
    book_path: Path,
    subject: str,
    paper: str,
    class_level: str,
    language: str = "Bangla",
    university: str = "Medical/MBBS Admission",
    password: Optional[str] = None,
) -> Dict[str, Any]:
    print(f"Preparing book: {book_path}")
    uploaded_files = upload_pdf_parts(book_path, password=password)

    prompt = f"""
You are building a structured syllabus tree for an admission analytics platform.
Target university / exam: {university}

Book metadata:
- Subject: {subject}
- Paper: {paper}
- Class level: {class_level}
- Language: {language}

Task:
Analyze this textbook PDF and create a detailed multi-layer syllabus tree.

The tree should follow this structure as much as possible:
Subject
  Paper
    Chapter
      Section
        Subsection
          Topic
            Subtopic / concept / formula / example-type

Important:
- Use the book's own chapter and section structure when visible.
- For scanned pages, infer structure from headings, subheadings, exercises, and content.
- Do not create too many tiny meaningless nodes.
- Leaf nodes should be useful for mapping MCQ questions later.
- The `name` field must always be the English name. If the textbook uses a Bangla term, translate it to English for `name`.
- The first element of `aliases` must always be the Bangla name of the node. Additional aliases (English variants, abbreviations) follow after it.
- Include relevant `keywords` for each node (both Bangla and English terms).
- Include formulas/concepts where useful, especially Physics and Chemistry.
- For each node write a compact 1–2 sentence English `summary` describing what concepts or content the node covers. The summary will be used for semantic / RAG search, so make it informative and specific.
- Return only valid JSON.

JSON format:
{{
  "subject": "{subject}",
  "paper": "{paper}",
  "class_level": "{class_level}",
  "source_book": "{book_path.name}",
  "root": {{
    "name": "{subject}",
    "type": "subject",
    "aliases": [],
    "keywords": [],
    "summary": "...",
    "children": [
      {{
        "name": "{paper}",
        "type": "paper",
        "aliases": [],
        "keywords": [],
        "summary": "...",
        "children": []
      }}
    ]
  }}
}}
"""

    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[*uploaded_files, prompt],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    max_output_tokens=65536,
                ),
            )

            finish_reason = None
            if response.candidates:
                finish_reason = getattr(response.candidates[0], "finish_reason", None)
                finish_reason_name = getattr(finish_reason, "name", str(finish_reason))
                if finish_reason_name not in ("STOP", "1"):
                    raise ValueError(f"Unexpected finish_reason: {finish_reason_name}")

            if not response.text:
                raise ValueError("Empty response from Gemini")

            data = safe_json_loads(response.text)
            break
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            last_error = exc
            print(f"Attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                time.sleep(5)
    else:
        raise RuntimeError(f"All 3 attempts to generate tree failed. Last error: {last_error}")

    root = data["root"]
    assign_node_ids(root, make_slug(subject))

    return data


def upsert_book_tree(master_tree: Dict[str, Any], new_book_tree: Dict[str, Any]):
    master_tree.setdefault("books", [])
    master_tree.setdefault("subjects", [])

    subject = new_book_tree["subject"]
    paper = new_book_tree["paper"]

    # Remove previous same subject-paper-source if exists
    master_tree["books"] = [
        b for b in master_tree["books"]
        if not (
            b.get("subject") == subject
            and b.get("paper") == paper
            and b.get("source_book") == new_book_tree.get("source_book")
        )
    ]

    master_tree["books"].append(new_book_tree)

    # Subject-level combined tree
    existing_subject = None
    for s in master_tree["subjects"]:
        if s.get("name") == subject:
            existing_subject = s
            break

    if not existing_subject:
        existing_subject = {
            "id": make_slug(subject),
            "name": subject,
            "type": "subject",
            "children": []
        }
        master_tree["subjects"].append(existing_subject)

    # Remove same paper under subject, then add updated paper tree
    new_paper_node = new_book_tree["root"]["children"][0]

    existing_subject["children"] = [
        p for p in existing_subject["children"]
        if p.get("name") != paper
    ]
    existing_subject["children"].append(new_paper_node)

    master_tree["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", required=True, help="Path to textbook PDF")
    parser.add_argument("--subject", required=True, help="Physics / Chemistry / Biology / Math / etc.")
    parser.add_argument("--paper", required=True, help="1st Paper / 2nd Paper / Single Paper / etc.")
    parser.add_argument("--class-level", default="HSC")
    parser.add_argument("--language", default="Bangla")
    parser.add_argument("--university", default="Medical/MBBS Admission",
                        help="Target university or exam, e.g. 'Medical/MBBS', 'BUET', 'DU Unit-A'")
    parser.add_argument("--password", default=None, help="Password for encrypted PDF books")
    parser.add_argument("--out", default="data/syllabus_tree.json")

    args = parser.parse_args()

    book_path = Path(args.book)
    out_path = Path(args.out)

    if not book_path.exists():
        raise FileNotFoundError(book_path)

    master_tree = load_json(out_path, default={
        "version": 1,
        "description": "Admission syllabus tree",
        "subjects": [],
        "books": []
    })

    new_tree = generate_tree_from_book(
        book_path=book_path,
        subject=args.subject,
        paper=args.paper,
        class_level=args.class_level,
        language=args.language,
        university=args.university,
        password=args.password,
    )

    upsert_book_tree(master_tree, new_tree)
    save_json(out_path, master_tree)

    print(f"Saved/updated syllabus tree: {out_path}")


if __name__ == "__main__":
    main()
