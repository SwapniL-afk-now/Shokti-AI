"""
RAG search over the syllabus tree node embeddings.

Core functions
--------------
find_nodes(query, ...)           → ranked list of matching tree nodes
search_questions(query, ...)     → end-to-end: query → nodes → questions
get_questions_for_nodes(...)     → questions from a pre-selected node set
embed_query(text)                → raw embedding vector for any text
"""

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types


load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
EMBED_MODEL = "gemini-embedding-2"
OUTPUT_DIM = 768  # must match the value used in 04_create_embeddings.py

if not API_KEY:
    raise ValueError("Missing GEMINI_API_KEY in .env")

client = genai.Client(api_key=API_KEY)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_embeddings(path: Path = Path("data/node_embeddings.json")) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run 04_create_embeddings.py first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_tree(path: Path = Path("data/syllabus_tree.json")) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_mapped_questions(
    path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    candidates = [
        Path("data/mapped_questions.json"),
        Path("data/mapped_questions_partial.json"),
    ]
    if path:
        candidates = [path] + candidates
    for p in candidates:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["_source_file"] = str(p)
            return data
    return None


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_query(text: str) -> List[float]:
    """Embed a user query.

    gemini-embedding-2 does not support task_type; the retrieval instruction
    is prepended directly to the query text instead.
    """
    instructed = f"Find syllabus topics related to: {text}"
    response = client.models.embed_content(
        model=EMBED_MODEL,
        contents=instructed,
        config=types.EmbedContentConfig(output_dimensionality=OUTPUT_DIM),
    )
    return response.embeddings[0].values


# ---------------------------------------------------------------------------
# Node search
# ---------------------------------------------------------------------------

def find_nodes(
    query: str,
    embeddings: Optional[Dict[str, Any]] = None,
    top_k: int = 10,
    min_score: float = 0.5,
    embeddings_path: Path = Path("data/node_embeddings.json"),
) -> List[Dict[str, Any]]:
    """
    Find the most relevant syllabus nodes for a natural language query.

    Returns a list of dicts sorted by descending similarity score:
        [{"id": ..., "name": ..., "path": ..., "score": 0.87}, ...]
    """
    if embeddings is None:
        embeddings = load_embeddings(embeddings_path)

    nodes_meta = embeddings.get("nodes", {})
    if not nodes_meta:
        return []

    node_ids = list(nodes_meta.keys())
    matrix = np.array(
        [nodes_meta[nid]["embedding"] for nid in node_ids], dtype=np.float32
    )

    query_vec = np.array(embed_query(query), dtype=np.float32)

    # Vectorised cosine similarity
    q_norm = np.linalg.norm(query_vec)
    row_norms = np.linalg.norm(matrix, axis=1)
    scores = (matrix @ query_vec) / (row_norms * q_norm + 1e-10)

    top_indices = np.argsort(scores)[::-1]

    results = []
    for idx in top_indices:
        score = float(scores[idx])
        if score < min_score or len(results) >= top_k:
            break
        nid = node_ids[idx]
        meta = nodes_meta[nid]
        results.append({
            "id": nid,
            "name": meta["name"],
            "path": meta["path"],
            "score": round(score, 4),
        })

    return results


# ---------------------------------------------------------------------------
# Tree traversal helpers
# ---------------------------------------------------------------------------

def _collect_all_ids(node: Dict[str, Any], out: Set[str]):
    nid = node.get("id")
    if nid:
        out.add(nid)
    for child in node.get("children", []):
        _collect_all_ids(child, out)


def collect_subtree_ids(
    tree: Dict[str, Any],
    target_ids: Set[str],
) -> Set[str]:
    """
    Return target_ids plus the IDs of every descendant node in the tree.
    Useful when a query matches a chapter and you want all its sub-topics.
    """
    result: Set[str] = set()

    def walk(node: Dict[str, Any]):
        if node.get("id") in target_ids:
            _collect_all_ids(node, result)
        else:
            for child in node.get("children", []):
                walk(child)

    for subject in tree.get("subjects", []):
        walk(subject)

    return result


# ---------------------------------------------------------------------------
# Question retrieval
# ---------------------------------------------------------------------------

def iter_questions(mapped_data: Dict[str, Any]):
    """Yield every question from mapped_questions regardless of structure."""
    for exam in mapped_data.get("exams", []):
        yield from exam.get("questions", [])
    for collection in mapped_data.get("collections", []):
        yield from collection.get("questions", [])
        for chapter in collection.get("chapters", []):
            yield from chapter.get("questions", [])


def get_questions_for_nodes(
    mapped_data: Dict[str, Any],
    node_ids: Set[str],
) -> List[Dict[str, Any]]:
    """Return all questions whose mapping.node_ids overlaps node_ids."""
    results = []
    for q in iter_questions(mapped_data):
        mapped = set(q.get("mapping", {}).get("node_ids", []))
        if mapped & node_ids:
            results.append(q)
    return results


# ---------------------------------------------------------------------------
# End-to-end search
# ---------------------------------------------------------------------------

def search_questions(
    query: str,
    top_k_nodes: int = 5,
    min_score: float = 0.5,
    include_subtree: bool = True,
    embeddings_path: Path = Path("data/node_embeddings.json"),
    questions_path: Optional[Path] = None,
    tree_path: Path = Path("data/syllabus_tree.json"),
) -> Dict[str, Any]:
    """
    End-to-end RAG pipeline: natural language query → relevant questions.

    Parameters
    ----------
    query          : natural language topic description
    top_k_nodes    : how many tree nodes to match
    min_score      : minimum cosine similarity (0–1) to include a node
    include_subtree: also return questions from child nodes of each match
    embeddings_path: path to node_embeddings.json
    questions_path : override for mapped_questions.json
    tree_path      : path to syllabus_tree.json (needed for subtree expansion)

    Returns
    -------
    {
        "query": str,
        "matched_nodes": [{"id", "name", "path", "score"}, ...],
        "node_ids_searched": [...],
        "questions": [...],
        "source": str,
    }
    """
    embeddings = load_embeddings(embeddings_path)
    matched_nodes = find_nodes(
        query, embeddings, top_k=top_k_nodes, min_score=min_score
    )

    if not matched_nodes:
        return {
            "query": query,
            "matched_nodes": [],
            "node_ids_searched": [],
            "questions": [],
            "source": None,
        }

    node_ids: Set[str] = {n["id"] for n in matched_nodes}

    if include_subtree:
        tree = load_tree(tree_path)
        node_ids = collect_subtree_ids(tree, node_ids)

    mapped_data = load_mapped_questions(questions_path)
    questions = get_questions_for_nodes(mapped_data, node_ids) if mapped_data else []

    return {
        "query": query,
        "matched_nodes": matched_nodes,
        "node_ids_searched": sorted(node_ids),
        "questions": questions,
        "source": mapped_data.get("_source_file") if mapped_data else None,
    }
