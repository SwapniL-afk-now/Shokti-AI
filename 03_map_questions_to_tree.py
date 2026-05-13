import os
import json
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types


load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

if not API_KEY:
    raise ValueError("Missing GEMINI_API_KEY in .env")

client = genai.Client(api_key=API_KEY)


def load_json(path: Path, default: Any = None):
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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


def flatten_tree_nodes(
    node: Dict[str, Any],
    path: Optional[List[str]] = None,
    output: Optional[List[Dict[str, Any]]] = None,
):
    if path is None:
        path = []
    if output is None:
        output = []

    current_path = path + [node.get("name", "Unnamed")]

    output.append({
        "id": node.get("id"),
        "name": node.get("name"),
        "type": node.get("type"),
        "path": " > ".join(current_path),
        "aliases": node.get("aliases", []),
        "keywords": node.get("keywords", []),
        "summary": node.get("summary", ""),
        "is_leaf": len(node.get("children", [])) == 0
    })

    for child in node.get("children", []):
        flatten_tree_nodes(child, current_path, output)

    return output


def get_all_nodes(syllabus_tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes = []

    for subject in syllabus_tree.get("subjects", []):
        nodes.extend(flatten_tree_nodes(subject))

    return nodes


def get_nodes_by_subject(all_nodes: List[Dict[str, Any]], subject: str) -> List[Dict[str, Any]]:
    subject_lower = subject.lower()

    return [
        n for n in all_nodes
        if n["path"].lower().startswith(subject_lower)
        or subject_lower in n["path"].lower()
    ]


def chunk_list(items: List[Any], chunk_size: int):
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def compact_question_text(question: Dict[str, Any]) -> str:
    options = question.get("options") or {}
    if not isinstance(options, dict):
        options = {}
    chapter = question.get("chapter", {})
    if not isinstance(chapter, dict):
        chapter = {}
    question_type = question.get("question_type", "mcq")

    lines = [
        f"Question No: {question.get('question_number')}",
        f"Type: {question_type}",
        f"Subject Guess: {question.get('subject_guess')}",
        f"Chapter Guess: {question.get('chapter_guess') or chapter.get('chapter_name')}",
        f"Topic Guess: {question.get('topic_guess')}",
        f"Source: {question.get('source_exam')} {question.get('source_year_label') or ''}",
        f"Stem: {question.get('stem')}",
    ]

    if question_type == "mcq":
        lines += [
            "Options:",
            f"A. {options.get('A')}",
            f"B. {options.get('B')}",
            f"C. {options.get('C')}",
            f"D. {options.get('D')}",
        ]
    else:
        marks = question.get("marks")
        if marks:
            lines.append(f"Marks: {marks}")
        written_type = question.get("written_type")
        if written_type:
            lines.append(f"Written Type: {written_type}")
        sub_questions = question.get("sub_questions") or []
        if sub_questions:
            lines.append("Sub-questions:")
            for sq in sub_questions:
                mark_str = f" [{sq.get('marks')} marks]" if sq.get("marks") else ""
                lines.append(f"  {sq.get('label', '')}: {sq.get('text', '')}{mark_str}")

    lines += ["Raw Text:", str(question.get("raw_text"))]
    return "\n".join(lines)


def first_pass_select_candidates(
    question: Dict[str, Any],
    candidate_nodes: List[Dict[str, Any]],
    max_nodes_per_chunk: int = 80,
) -> List[Dict[str, Any]]:
    selected = []

    q_text = compact_question_text(question)

    for chunk in chunk_list(candidate_nodes, max_nodes_per_chunk):
        node_text = "\n".join([
            f"- id: {n['id']} | path: {n['path']} | keywords: {n.get('keywords', [])}"
            for n in chunk
        ])

        prompt = f"""
You are selecting possible syllabus nodes for a medical admission MCQ.

Question:
{q_text}

Candidate syllabus nodes:
{node_text}

Task:
Choose possible relevant nodes from the candidate list.
Prefer the most specific leaf/topic nodes.
A question may map to multiple nodes if genuinely relevant.
If no node is relevant, return empty list.

Return only valid JSON.

JSON format:
{{
  "selected_node_ids": ["node_id_1", "node_id_2"]
}}
"""

        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )

        data = safe_json_loads(response.text)
        selected_ids = set(data.get("selected_node_ids", []))

        for n in chunk:
            if n["id"] in selected_ids:
                selected.append(n)

    # Deduplicate
    dedup = {}
    for n in selected:
        dedup[n["id"]] = n

    return list(dedup.values())


def final_map_question(
    question: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    allow_new_nodes: bool,
) -> Dict[str, Any]:
    q_text = compact_question_text(question)

    candidate_text = "\n".join([
        f"- id: {n['id']} | path: {n['path']} | type: {n['type']} | keywords: {n.get('keywords', [])}"
        for n in candidates
    ])

    prompt = f"""
You are mapping a medical admission MCQ to a syllabus tree.

Question:
{q_text}

Candidate nodes:
{candidate_text}

Allow new node creation: {allow_new_nodes}

Rules:
- Prefer existing candidate node IDs.
- Choose the most specific node possible.
- A question can map to multiple nodes.
- Use multiple nodes only when actually needed.
- If no existing node fits and new node creation is allowed, suggest a new path.
- If new node creation is not allowed and no node fits, return empty node_ids and explain.
- Return only valid JSON.

JSON format:
{{
  "node_ids": ["existing_node_id"],
  "confidence": 0.0,
  "reason": "short explanation",
  "new_nodes": [
    {{
      "subject": "English / GK / Physics / Chemistry / Biology",
      "path": ["English", "Grammar", "Right form of verbs"],
      "reason": "why this new node is needed",
      "summary": "compact 1-2 sentence description of this node for semantic search"
    }}
  ]
}}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )

    return safe_json_loads(response.text)


def find_node_by_id(node: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    if node.get("id") == node_id:
        return node

    for child in node.get("children", []):
        found = find_node_by_id(child, node_id)
        if found:
            return found

    return None


def ensure_subject_node(syllabus_tree: Dict[str, Any], subject: str) -> Dict[str, Any]:
    syllabus_tree.setdefault("subjects", [])

    for s in syllabus_tree["subjects"]:
        if s.get("name", "").lower() == subject.lower():
            return s

    new_subject = {
        "id": make_slug(subject),
        "name": subject,
        "type": "subject",
        "aliases": [],
        "keywords": [],
        "summary": "",
        "children": []
    }

    syllabus_tree["subjects"].append(new_subject)
    return new_subject


def normalize_subject_name(subject: Optional[str]) -> Optional[str]:
    if not subject:
        return None

    subject_lower = subject.lower()
    subject_map = {
        "physics": ["physics", "পদার্থ"],
        "chemistry": ["chemistry", "রসায়ন", "রসায়ন"],
        "biology": ["biology", "জীববিজ্ঞান", "উদ্ভিদ", "প্রাণী"],
        "english": ["english", "ইংরেজি"],
        "general knowledge": ["gk", "general knowledge", "সাধারণ জ্ঞান"],
    }

    for canonical, tokens in subject_map.items():
        if any(token in subject_lower for token in tokens):
            return canonical.title()

    return subject


def normalize_dynamic_path(question: Dict[str, Any], path: List[str]) -> List[str]:
    if not path:
        return path

    first = path[0].strip().lower()
    starts_with_paper = first in ["1st paper", "2nd paper", "first paper", "second paper", "প্রথম পত্র", "দ্বিতীয় পত্র", "দ্বিতীয় পত্র"]

    if starts_with_paper:
        chapter = question.get("chapter") if isinstance(question.get("chapter"), dict) else {}
        subject = normalize_subject_name(question.get("subject_guess") or chapter.get("subject"))
        if subject:
            return [subject, *path]

    return path


def add_dynamic_path_to_tree(
    syllabus_tree: Dict[str, Any],
    path: List[str],
    leaf_summary: str = "",
) -> str:
    """
    Adds a dynamic path like:
    ["English", "Grammar", "Right form of verbs"]

    Returns the final node id.
    leaf_summary is attached to the deepest new node; intermediate new nodes
    get an auto-generated summary.
    """
    if not path:
        raise ValueError("Empty path")

    current = ensure_subject_node(syllabus_tree, path[0])
    current_path_id = current["id"]

    path_segments = path[1:]
    for i, level_name in enumerate(path_segments):
        children = current.setdefault("children", [])

        existing = None
        for child in children:
            if child.get("name", "").lower() == level_name.lower():
                existing = child
                break

        if existing:
            current = existing
            current_path_id = current["id"]
            continue

        new_id = f"{current_path_id}_{make_slug(level_name)}"
        is_leaf = (i == len(path_segments) - 1)
        summary = leaf_summary if (is_leaf and leaf_summary) else f"Covers {level_name} in the medical admission syllabus."

        new_node = {
            "id": new_id,
            "name": level_name,
            "type": "dynamic_topic",
            "aliases": [],
            "keywords": [],
            "summary": summary,
            "children": []
        }

        children.append(new_node)
        current = new_node
        current_path_id = new_id

    return current["id"]


def question_bank_from_checkpoint(data: Dict[str, Any]) -> Dict[str, Any]:
    collection_name = data.get("collection_name", "Partial Question Bank")
    source_pdf = Path(data.get("pdf", "checkpoint")).name
    mode = data.get("mode", "checkpoint")

    if mode == "chapterwise":
        chapters_by_key = {}
        total_questions = 0

        for page_result in data.get("page_results", []):
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

            for question_index, question in enumerate(page_result.get("questions", []), start=1):
                total_questions += 1
                chapter["total_questions"] += 1

                mapped_question = question.copy()
                mapped_question.setdefault("id", f"{source_pdf}_p{page_result.get('page_number')}_q{question_index}")
                mapped_question.setdefault("source_pdf", source_pdf)
                mapped_question.setdefault("page_number", page_result.get("page_number"))
                mapped_question.setdefault("question_index_on_page", question_index)
                mapped_question.setdefault("subject_guess", subject)
                mapped_question.setdefault("chapter", {
                    "subject": subject,
                    "chapter_number": chapter_number,
                    "chapter_name": chapter_name
                })

                chapter["questions"].append(mapped_question)

        return {
            "version": 1,
            "description": "Partial question bank from extraction checkpoint",
            "collections": [{
                "collection_name": collection_name,
                "source_pdf": source_pdf,
                "extraction_mode": "chapterwise_checkpoint",
                "total_questions": total_questions,
                "chapters": list(chapters_by_key.values())
            }]
        }

    questions = []

    for page_result in data.get("page_results", []):
        for question_index, question in enumerate(page_result.get("questions", []), start=1):
            mapped_question = question.copy()
            mapped_question.setdefault("id", f"{source_pdf}_p{page_result.get('page_number')}_q{question_index}")
            mapped_question.setdefault("source_pdf", source_pdf)
            mapped_question.setdefault("page_number", page_result.get("page_number"))
            mapped_question.setdefault("question_index_on_page", question_index)
            questions.append(mapped_question)

    return {
        "version": 1,
        "description": "Partial question bank from extraction checkpoint",
        "collections": [{
            "collection_name": collection_name,
            "source_pdf": source_pdf,
            "extraction_mode": f"{mode}_checkpoint",
            "total_questions": len(questions),
            "questions": questions
        }]
    }


def load_question_source(path: Path) -> Dict[str, Any]:
    data = load_json(path)

    if "page_results" in data:
        print(f"Loaded extraction checkpoint for partial mapping: {path}")
        return question_bank_from_checkpoint(data)

    return data


def map_single_question(
    question: Dict[str, Any],
    all_nodes: List[Dict[str, Any]],
    allow_new_nodes: bool,
) -> Dict[str, Any]:
    subject_guess = question.get("subject_guess", "Unknown")

    if subject_guess and subject_guess.lower() not in ["unknown", "gk", "general knowledge"]:
        subject_nodes = get_nodes_by_subject(all_nodes, subject_guess)
    else:
        subject_nodes = all_nodes

    # If subject-specific candidates are too few, fallback to all nodes
    if len(subject_nodes) < 5:
        subject_nodes = all_nodes

    first_candidates = first_pass_select_candidates(
        question=question,
        candidate_nodes=subject_nodes,
    )

    # Fallback: if first pass finds nothing, use all subject nodes but limit final input
    if not first_candidates:
        first_candidates = subject_nodes[:120]

    result = final_map_question(
        question=question,
        candidates=first_candidates,
        allow_new_nodes=allow_new_nodes,
    )

    return result


def map_question_and_update_tree(
    question: Dict[str, Any],
    syllabus_tree: Dict[str, Any],
    all_nodes: List[Dict[str, Any]],
    allow_new_nodes: bool,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    mapping_result = map_single_question(
        question=question,
        all_nodes=all_nodes,
        allow_new_nodes=allow_new_nodes,
    )

    node_ids = mapping_result.get("node_ids", [])
    new_nodes = mapping_result.get("new_nodes", [])

    if allow_new_nodes and new_nodes:
        for new_node in new_nodes:
            path = new_node.get("path", [])
            if path:
                path = normalize_dynamic_path(question, path)
                new_node["_normalized_path"] = path
                new_node_id = add_dynamic_path_to_tree(
                    syllabus_tree,
                    path,
                    leaf_summary=new_node.get("summary", ""),
                )
                if new_node_id not in node_ids:
                    node_ids.append(new_node_id)

        all_nodes = get_all_nodes(syllabus_tree)

    mapped_question = question.copy()
    mapped_question["mapping"] = {
        "mapped": len(node_ids) > 0,
        "node_ids": node_ids,
        "confidence": mapping_result.get("confidence"),
        "reason": mapping_result.get("reason"),
        "new_nodes": new_nodes
    }

    return mapped_question, all_nodes


def map_question_with_checkpoint(
    question: Dict[str, Any],
    syllabus_tree: Dict[str, Any],
    all_nodes: List[Dict[str, Any]],
    allow_new_nodes: bool,
    checkpoint: Dict[str, Any],
    checkpoint_path: Path,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    q_id = question.get("id")
    mapped_cache = checkpoint.get("mapped", {})

    if q_id and q_id in mapped_cache:
        cached = mapped_cache[q_id]
        node_ids = cached.get("node_ids", [])
        new_nodes = cached.get("new_nodes", [])

        if allow_new_nodes and new_nodes:
            for new_node in new_nodes:
                path = new_node.get("_normalized_path") or new_node.get("path", [])
                if path:
                    add_dynamic_path_to_tree(syllabus_tree, path, leaf_summary=new_node.get("summary", ""))
            all_nodes = get_all_nodes(syllabus_tree)

        mapped_question = question.copy()
        mapped_question["mapping"] = {
            "mapped": len(node_ids) > 0,
            "node_ids": node_ids,
            "confidence": cached.get("confidence"),
            "reason": cached.get("reason"),
            "new_nodes": new_nodes,
        }
        print(f"  [checkpoint hit] {q_id}")
        return mapped_question, all_nodes

    mapped_question, all_nodes = map_question_and_update_tree(
        question=question,
        syllabus_tree=syllabus_tree,
        all_nodes=all_nodes,
        allow_new_nodes=allow_new_nodes,
    )

    if q_id:
        checkpoint.setdefault("mapped", {})[q_id] = {
            "node_ids": mapped_question["mapping"]["node_ids"],
            "confidence": mapped_question["mapping"]["confidence"],
            "reason": mapped_question["mapping"]["reason"],
            "new_nodes": mapped_question["mapping"].get("new_nodes", []),
        }
        save_json(checkpoint_path, checkpoint)

    return mapped_question, all_nodes


def map_exam_record(
    exam: Dict[str, Any],
    syllabus_tree: Dict[str, Any],
    all_nodes: List[Dict[str, Any]],
    allow_new_nodes: bool,
    checkpoint: Dict[str, Any],
    checkpoint_path: Path,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    mapped_exam = {
        "year": exam.get("year"),
        "exam_name": exam.get("exam_name"),
        "source_pdf": exam.get("source_pdf"),
        "questions": []
    }

    print(f"Mapping exam: {exam.get('year')} - {exam.get('source_pdf')}")

    for question in exam.get("questions", []):
        print(f"Mapping question: {question.get('id') or question.get('question_number')}")
        mapped_question, all_nodes = map_question_with_checkpoint(
            question=question,
            syllabus_tree=syllabus_tree,
            all_nodes=all_nodes,
            allow_new_nodes=allow_new_nodes,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
        )
        mapped_exam["questions"].append(mapped_question)

    return mapped_exam, all_nodes


def map_collection_record(
    collection: Dict[str, Any],
    syllabus_tree: Dict[str, Any],
    all_nodes: List[Dict[str, Any]],
    allow_new_nodes: bool,
    checkpoint: Dict[str, Any],
    checkpoint_path: Path,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    mapped_collection = {
        "collection_name": collection.get("collection_name"),
        "source_pdf": collection.get("source_pdf"),
        "extraction_mode": collection.get("extraction_mode"),
        "total_questions": collection.get("total_questions"),
    }

    print(f"Mapping collection: {collection.get('collection_name')} - {collection.get('source_pdf')}")

    if "chapters" in collection:
        mapped_collection["chapters"] = []

        for chapter in collection.get("chapters", []):
            mapped_chapter = {
                "subject": chapter.get("subject"),
                "chapter_number": chapter.get("chapter_number"),
                "chapter_name": chapter.get("chapter_name"),
                "total_questions": chapter.get("total_questions"),
                "questions": []
            }

            print(f"Mapping chapter: {chapter.get('subject')} - {chapter.get('chapter_name')}")

            for question in chapter.get("questions", []):
                print(f"Mapping question: {question.get('id')}")
                mapped_question, all_nodes = map_question_with_checkpoint(
                    question=question,
                    syllabus_tree=syllabus_tree,
                    all_nodes=all_nodes,
                    allow_new_nodes=allow_new_nodes,
                    checkpoint=checkpoint,
                    checkpoint_path=checkpoint_path,
                )
                mapped_chapter["questions"].append(mapped_question)

            mapped_collection["chapters"].append(mapped_chapter)
    else:
        mapped_collection["questions"] = []

        for question in collection.get("questions", []):
            print(f"Mapping question: {question.get('id')}")
            mapped_question, all_nodes = map_question_with_checkpoint(
                question=question,
                syllabus_tree=syllabus_tree,
                all_nodes=all_nodes,
                allow_new_nodes=allow_new_nodes,
                checkpoint=checkpoint,
                checkpoint_path=checkpoint_path,
            )
            mapped_collection["questions"].append(mapped_question)

    return mapped_collection, all_nodes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", default="data/syllabus_tree.json")
    parser.add_argument("--questions", default="data/question_bank.json")
    parser.add_argument("--out", default="data/mapped_questions.json")
    parser.add_argument(
        "--allow-new-nodes",
        action="store_true",
        help="Allow model to create new nodes, useful for English/GK."
    )

    args = parser.parse_args()

    tree_path = Path(args.tree)
    questions_path = Path(args.questions)
    out_path = Path(args.out)

    if "extraction_checkpoints" in out_path.parts:
        raise ValueError(
            "Do not write mapped output into data/extraction_checkpoints. "
            "Use --out data/mapped_questions.json instead."
        )

    if not questions_path.exists():
        raise FileNotFoundError(
            f"{questions_path} does not exist. Run 02_extract_question_bank.py first; "
            "the checkpoint file is only partial progress, not the final question bank."
        )

    syllabus_tree = load_json(tree_path)
    question_bank = load_question_source(questions_path)

    all_nodes = get_all_nodes(syllabus_tree)

    checkpoint_path = out_path.with_suffix(".checkpoint.json")
    checkpoint = load_json(checkpoint_path, default={"allow_new_nodes": args.allow_new_nodes, "mapped": {}})

    if checkpoint.get("mapped") and checkpoint.get("allow_new_nodes") != args.allow_new_nodes:
        print(
            f"WARNING: checkpoint was created with allow_new_nodes={checkpoint['allow_new_nodes']} "
            f"but current run uses allow_new_nodes={args.allow_new_nodes}. Ignoring checkpoint."
        )
        checkpoint = {"allow_new_nodes": args.allow_new_nodes, "mapped": {}}
    elif checkpoint.get("mapped"):
        print(f"Loaded mapping checkpoint: {checkpoint_path} ({len(checkpoint['mapped'])} questions cached)")

    mapped_output = {
        "version": 1,
        "description": "Questions mapped to syllabus tree",
        "exams": [],
        "collections": []
    }

    for exam in question_bank.get("exams", []):
        mapped_exam, all_nodes = map_exam_record(
            exam=exam,
            syllabus_tree=syllabus_tree,
            all_nodes=all_nodes,
            allow_new_nodes=args.allow_new_nodes,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
        )
        mapped_output["exams"].append(mapped_exam)

    for collection in question_bank.get("collections", []):
        mapped_collection, all_nodes = map_collection_record(
            collection=collection,
            syllabus_tree=syllabus_tree,
            all_nodes=all_nodes,
            allow_new_nodes=args.allow_new_nodes,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
        )
        mapped_output["collections"].append(mapped_collection)

    save_json(out_path, mapped_output)

    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print(f"Deleted mapping checkpoint: {checkpoint_path}")

    # Save updated tree too, because English/GK dynamic nodes may be added.
    if args.allow_new_nodes:
        save_json(tree_path, syllabus_tree)

    print(f"Saved mapped questions: {out_path}")

    if args.allow_new_nodes:
        print(f"Updated syllabus tree with dynamic nodes: {tree_path}")


if __name__ == "__main__":
    main()
