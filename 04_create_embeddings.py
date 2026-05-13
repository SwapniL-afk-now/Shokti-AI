import os
import json
import time
import hashlib
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from tqdm import tqdm


load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
EMBED_MODEL = "gemini-embedding-2"
BATCH_SIZE = 50
OUTPUT_DIM = 768  # 128–3072; 768 is a good balance of quality vs storage

if not API_KEY:
    raise ValueError("Missing GEMINI_API_KEY in .env")

client = genai.Client(api_key=API_KEY)


def load_json(path: Path, default: Any = None) -> Any:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_node_text(node: Dict[str, Any], path: str) -> str:
    """Build the text that will be embedded for a node.

    gemini-embedding-2 does not use task_type; instead the instruction is
    embedded directly in the text.
    """
    parts = ["Represent this syllabus topic:", path, node.get("name", ""), node.get("summary", "")]

    aliases = [a for a in (node.get("aliases") or []) if a]
    if aliases:
        parts.append("aliases: " + ", ".join(aliases))

    keywords = [k for k in (node.get("keywords") or []) if k]
    if keywords:
        parts.append("keywords: " + ", ".join(keywords))

    return " | ".join(p for p in parts if p)


def flatten_nodes(
    node: Dict[str, Any],
    path_parts: Optional[List[str]] = None,
    result: Optional[List[Dict]] = None,
) -> List[Dict]:
    if path_parts is None:
        path_parts = []
    if result is None:
        result = []

    current_path = path_parts + [node.get("name", "Unnamed")]
    path_str = " > ".join(current_path)

    node_id = node.get("id")
    if node_id:
        result.append({
            "id": node_id,
            "name": node.get("name", ""),
            "path": path_str,
            "text": build_node_text(node, path_str),
        })

    for child in node.get("children", []):
        flatten_nodes(child, current_path, result)

    return result


def get_all_nodes(tree: Dict[str, Any]) -> List[Dict]:
    nodes = []
    for subject in tree.get("subjects", []):
        flatten_nodes(subject, [], nodes)
    return nodes


def embed_batch(texts: List[str]) -> List[List[float]]:
    response = client.models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=OUTPUT_DIM),
    )
    return [e.values for e in response.embeddings]


def main():
    parser = argparse.ArgumentParser(
        description="Build or update Gemini embeddings for every syllabus tree node."
    )
    parser.add_argument("--tree", default="data/syllabus_tree.json")
    parser.add_argument("--out", default="data/node_embeddings.json")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed all nodes even if their text is unchanged.",
    )
    args = parser.parse_args()

    tree = load_json(Path(args.tree))
    if not tree:
        raise FileNotFoundError(f"{args.tree} not found. Run 01_generate_tree_from_book.py first.")

    all_nodes = get_all_nodes(tree)
    print(f"Nodes in tree:   {len(all_nodes)}")

    existing = load_json(Path(args.out), default={"model": EMBED_MODEL, "nodes": {}})
    stored: Dict[str, Any] = existing.get("nodes", {})

    # Determine which nodes need (re-)embedding based on text hash.
    to_embed = []
    for node in all_nodes:
        h = text_hash(node["text"])
        if not args.force and node["id"] in stored and stored[node["id"]].get("text_hash") == h:
            continue
        to_embed.append({**node, "text_hash": h})

    print(f"Nodes to embed:  {len(to_embed)} (skipping {len(all_nodes) - len(to_embed)} unchanged)")

    if not to_embed:
        print("All embeddings are up to date.")
        return

    out_path = Path(args.out)
    total = len(to_embed)

    for i in tqdm(range(0, total, BATCH_SIZE), desc="Embedding batches"):
        batch = to_embed[i:i + BATCH_SIZE]
        vectors = embed_batch([n["text"] for n in batch])

        for node, vec in zip(batch, vectors):
            stored[node["id"]] = {
                "name": node["name"],
                "path": node["path"],
                "text": node["text"],
                "text_hash": node["text_hash"],
                "embedding": list(vec),
            }

        # Save after every batch so a crash doesn't lose progress.
        save_json(out_path, {"model": EMBED_MODEL, "nodes": stored})

        if i + BATCH_SIZE < total:
            time.sleep(0.5)

    print(f"\nSaved {len(stored)} node embeddings → {args.out}")


if __name__ == "__main__":
    main()
