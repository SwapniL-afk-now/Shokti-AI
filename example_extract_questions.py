"""
Example: extract exam questions by topic using RAG semantic search.

Prerequisites
-------------
1. data/syllabus_tree.json      → run 01_generate_tree_from_book.py
2. data/question_bank.json      → run 02_extract_question_bank.py
3. data/mapped_questions.json   → run 03_map_questions_to_tree.py
4. data/node_embeddings.json    → run 04_create_embeddings.py

Run
---
    python example_extract_questions.py
"""

from rag import search_questions


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------

def print_results(result: dict, max_questions: int = 5):
    print(f"\n{'='*65}")
    print(f"  Query: {result['query']}")
    print(f"{'='*65}")

    nodes = result["matched_nodes"]
    print(f"\nMatched nodes ({len(nodes)}):")
    for n in nodes:
        print(f"  [{n['score']:.3f}]  {n['path']}")

    questions = result["questions"]
    print(f"\nQuestions found: {len(questions)}")

    for i, q in enumerate(questions[:max_questions], 1):
        q_type = q.get("question_type", "mcq")
        stem = (q.get("stem") or "").strip()[:120]
        source_parts = [q.get("source_exam"), q.get("source_year_label")]
        source = " ".join(p for p in source_parts if p)

        print(f"\n  {i}. [{q_type.upper()}] {stem}{'…' if len(q.get('stem','')) > 120 else ''}")

        if q_type == "mcq":
            opts = q.get("options") or {}
            for key in ["A", "B", "C", "D"]:
                if opts.get(key):
                    print(f"       {key}. {opts[key]}")
            if q.get("answer"):
                print(f"       Answer: {q['answer']}")
        else:
            marks = q.get("marks")
            if marks:
                print(f"       Marks: {marks}")
            for sq in (q.get("sub_questions") or [])[:3]:
                print(f"       ({sq.get('label')}) {sq.get('text','')[:80]}")

        if source:
            print(f"       Source: {source}")

    if len(questions) > max_questions:
        print(f"\n  … and {len(questions) - max_questions} more question(s) not shown.")

    if not questions:
        print("  No questions found. Try lowering min_score or broadening the query.")


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    # --- Example 1: Physics concept ---
    print_results(search_questions(
        query="Newton's laws of motion and their applications",
        top_k_nodes=5,
        min_score=0.5,
        include_subtree=True,
    ))

    # --- Example 2: Biology topic ---
    print_results(search_questions(
        query="photosynthesis light reaction dark reaction chlorophyll",
        top_k_nodes=5,
        min_score=0.5,
    ))

    # --- Example 3: Chemistry ---
    print_results(search_questions(
        query="electrochemical cells electrolysis Faraday's laws",
        top_k_nodes=5,
        min_score=0.5,
    ))

    # --- Example 4: Narrow search, no subtree expansion ---
    # Use this when you want only the exact matched node, not its children.
    print_results(search_questions(
        query="meiosis stages prophase metaphase anaphase",
        top_k_nodes=3,
        min_score=0.6,
        include_subtree=False,
    ), max_questions=3)

    # --- Example 5: Custom topic description ---
    # You can describe a concept in plain language, not just keywords.
    print_results(search_questions(
        query="the process by which plants convert carbon dioxide and water "
              "into glucose using sunlight energy",
        top_k_nodes=4,
        min_score=0.45,
    ))
