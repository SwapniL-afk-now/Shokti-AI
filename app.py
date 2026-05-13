import copy
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


app = FastAPI(title="Syllabus Tree Viewer")

TREE_PATH = Path("data/syllabus_tree.json")
MAPPED_PATHS = [
    Path("data/mapped_questions.json"),
    Path("data/mapped_questions_partial.json"),
]
STATIC_DIR = Path(__file__).parent / "static"


class _Cache:
    tree_mtime: float = 0.0
    tree_data: Optional[dict] = None
    mapped_mtime: float = 0.0
    mapped_path: str = ""
    mapped_data: Optional[dict] = None


_cache = _Cache()


def load_tree() -> dict[str, Any]:
    if not TREE_PATH.exists():
        raise HTTPException(status_code=404, detail=f"{TREE_PATH} not found")

    mtime = TREE_PATH.stat().st_mtime
    if _cache.tree_data is None or mtime != _cache.tree_mtime:
        with TREE_PATH.open("r", encoding="utf-8") as file:
            _cache.tree_data = json.load(file)
        _cache.tree_mtime = mtime

    return _cache.tree_data


def load_mapped_questions() -> Optional[dict[str, Any]]:
    for path in MAPPED_PATHS:
        if path.exists():
            mtime = path.stat().st_mtime
            if (
                _cache.mapped_data is None
                or str(path) != _cache.mapped_path
                or mtime != _cache.mapped_mtime
            ):
                with path.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                data["_source_file"] = str(path)
                _cache.mapped_data = data
                _cache.mapped_mtime = mtime
                _cache.mapped_path = str(path)
            return _cache.mapped_data
    return None


def iter_mapped_questions(mapped_data: dict[str, Any]):
    for exam in mapped_data.get("exams", []):
        yield from exam.get("questions", [])

    for collection in mapped_data.get("collections", []):
        for question in collection.get("questions", []):
            yield question

        for chapter in collection.get("chapters", []):
            yield from chapter.get("questions", [])


def question_summary(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": question.get("id"),
        "question_type": question.get("question_type", "mcq"),
        "written_type": question.get("written_type"),
        "stem": question.get("stem"),
        "options": question.get("options") or {},
        "marks": question.get("marks"),
        "sub_questions": question.get("sub_questions") or [],
        "answer": question.get("answer"),
        "source_exam": question.get("source_exam"),
        "source_year_label": question.get("source_year_label"),
        "subject_guess": question.get("subject_guess"),
        "page_number": question.get("page_number"),
        "mapping": question.get("mapping", {}),
    }


def find_node(node: dict[str, Any], node_id: str) -> Optional[dict[str, Any]]:
    if node.get("id") == node_id:
        return node

    for child in node.get("children", []):
        found = find_node(child, node_id)
        if found:
            return found

    return None


def find_tree_node(tree: dict[str, Any], node_id: str) -> Optional[dict[str, Any]]:
    for subject in tree.get("subjects", []):
        found = find_node(subject, node_id)
        if found:
            return found
    return None


def collect_node_ids(node: dict[str, Any]) -> set[str]:
    node_ids = set()
    node_id = node.get("id")
    if node_id:
        node_ids.add(node_id)

    for child in node.get("children", []):
        node_ids.update(collect_node_ids(child))

    return node_ids


def count_questions_by_node(mapped_data: Optional[dict[str, Any]]) -> dict[str, set[str]]:
    counts: dict[str, set[str]] = {}
    if not mapped_data:
        return counts

    for question in iter_mapped_questions(mapped_data):
        question_id = question.get("id")
        if not question_id:
            continue

        node_ids = question.get("mapping", {}).get("node_ids", [])
        for node_id in node_ids:
            counts.setdefault(node_id, set()).add(question_id)

    return counts


def annotate_counts(node: dict[str, Any], direct_counts: dict[str, set[str]]) -> set[str]:
    direct_question_ids = set(direct_counts.get(node.get("id"), set()))
    subtree_question_ids = set(direct_question_ids)

    for child in node.get("children", []):
        subtree_question_ids.update(annotate_counts(child, direct_counts))

    node["_question_count_direct"] = len(direct_question_ids)
    node["_question_count_total"] = len(subtree_question_ids)
    return subtree_question_ids


def load_tree_with_counts() -> dict[str, Any]:
    tree = copy.deepcopy(load_tree())
    mapped_data = load_mapped_questions()
    direct_counts = count_questions_by_node(mapped_data)

    for subject in tree.get("subjects", []):
        annotate_counts(subject, direct_counts)

    tree["_question_count_source"] = mapped_data.get("_source_file") if mapped_data else None
    return tree


@app.get("/api/tree")
def get_tree():
    return load_tree_with_counts()


@app.get("/api/node-questions/{node_id}")
def get_node_questions(node_id: str):
    tree = load_tree()
    node = find_tree_node(tree, node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    mapped_data = load_mapped_questions()
    if not mapped_data:
        return {
            "node_id": node_id,
            "direct_questions": [],
            "subtree_questions": [],
            "mapped_source": None,
        }

    subtree_node_ids = collect_node_ids(node)
    direct_questions = []
    subtree_questions = []

    for question in iter_mapped_questions(mapped_data):
        mapped_node_ids = set(question.get("mapping", {}).get("node_ids", []))
        if node_id in mapped_node_ids:
            direct_questions.append(question_summary(question))
        if mapped_node_ids.intersection(subtree_node_ids):
            subtree_questions.append(question_summary(question))

    return {
        "node_id": node_id,
        "direct_questions": direct_questions,
        "subtree_questions": subtree_questions,
        "mapped_source": mapped_data.get("_source_file"),
    }


@app.get("/api/stats")
def get_stats():
    mapped_data = load_mapped_questions()

    year_counts: dict[str, int] = {}
    subject_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}

    if mapped_data:
        for question in iter_mapped_questions(mapped_data):
            year_label = question.get("source_year_label") or (
                str(question.get("year")) if question.get("year") else None
            )
            if year_label:
                year_counts[str(year_label)] = year_counts.get(str(year_label), 0) + 1

            subject = question.get("subject_guess") or "Unknown"
            subject_counts[subject] = subject_counts.get(subject, 0) + 1

            q_type = question.get("question_type") or "mcq"
            type_counts[q_type] = type_counts.get(q_type, 0) + 1

    return {
        "years": year_counts,
        "subjects": subject_counts,
        "question_types": type_counts,
        "source": mapped_data.get("_source_file") if mapped_data else None,
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
