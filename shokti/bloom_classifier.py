"""Bloom's taxonomy classifier for MCQs."""
import argparse
import sqlite3
from shokti.core.config import DB_PATH

BLOOM_LEVELS = ["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]

# Keyword mappings per Bloom level
BLOOM_KEYWORDS = {
    "Remember": ["which", "what", "define", "name", "identify", "list", "state", "recall", "remember", "who", "where", "when"],
    "Understand": ["explain", "describe", "distinguish", "compare", "illustrate", "interpret", "summarize", "outline", "classify", "relate"],
    "Apply": ["calculate", "solve", "use", "determine", "find", "apply", "demonstrate", "show how", "construct", "predict"],
    "Analyze": ["analyze", "why", "how", "compare", "differentiate", "examine", "investigate", "break down", "distinguish between", "evaluate"],
    "Evaluate": ["evaluate", "assess", "judge", "criticize", "justify", "argue", "defend", "support", "recommend", "critique"],
    "Create": ["design", "propose", "synthesize", "create", "formulate", "develop", "compose", "construct", "generate", "plan"],
}


def classify_mcq(mcq_question: str) -> str:
    """Classify a single MCQ into Bloom's level based on keywords.
    Remember: 'which', 'what', 'define', 'name', 'identify'
    Understand: 'explain', 'describe', 'distinguish', 'compare'
    Apply: 'calculate', 'solve', 'use', 'determine'
    Analyze: 'analyze', 'why', 'how', 'compare'
    Evaluate: 'evaluate', 'assess', 'judge', 'criticize'
    Create: 'design', 'propose', 'synthesize', 'create'"""
    if not mcq_question:
        return "Remember"  # default

    q_lower = mcq_question.lower()

    # Score each level by keyword matches
    scores = {}
    for level, keywords in BLOOM_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in q_lower)
        scores[level] = score

    # Return the highest-scoring level; default to Remember
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Remember"


def get_topic_bloom_profile(conn) -> dict:
    """For each topic, get distribution of Bloom levels.
    Flag topics with only low-level (Remember/Understand) MCQs."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, question, topic_name FROM question_bank")
    rows = cursor.fetchall()

    topic_profile = {}
    for qid, question, topic_name in rows:
        if topic_name not in topic_profile:
            topic_profile[topic_name] = {"total": 0, "levels": {l: 0 for l in BLOOM_LEVELS}}

        level = classify_mcq(question)
        topic_profile[topic_name]["total"] += 1
        topic_profile[topic_name]["levels"][level] += 1

    # Flag low-level-only topics
    for topic, data in topic_profile.items():
        total = data["total"]
        low_level = data["levels"]["Remember"] + data["levels"]["Understand"]
        data["low_level_pct"] = (low_level / total * 100) if total > 0 else 0.0
        data["flagged"] = data["low_level_pct"] == 100.0 and total >= 5

    return topic_profile


def print_bloom_profile():
    """Print Bloom's taxonomy profile per topic."""
    conn = sqlite3.connect(DB_PATH)
    profile = get_topic_bloom_profile(conn)
    conn.close()

    print(f"\n{'='*75}")
    print(f"  BLOOM'S TAXONOMY PROFILE BY TOPIC")
    print(f"{'='*75}")
    print(f"  {'Topic':<35} {'Total':>5} ", end="")
    for level in BLOOM_LEVELS:
        print(f"{level[:3]:>5} ", end="")
    print(f"{'Low%':>6} {'Flag':>4}")
    print(f"  {'-'*35} {'-'*5} ", end="")
    for _ in BLOOM_LEVELS:
        print(f"{'-----':>5} ", end="")
    print(f"{'------':>6} {'----':>4}")

    for topic, data in sorted(profile.items(), key=lambda x: x[1]["total"], reverse=True):
        total = data["total"]
        print(f"  {topic[:32]:<35} {total:>5} ", end="")
        for level in BLOOM_LEVELS:
            cnt = data["levels"][level]
            pct = (cnt / total * 100) if total > 0 else 0
            print(f"{cnt:>4}({pct:>4.0f})", end=" ")
        print(f"{data['low_level_pct']:>6.1f} {'***' if data['flagged'] else '':>4}")

    print(f"{'='*75}")
    print(f"\n  Flag = '***' means topic has only Remember/Understand MCQs (5+ MCQs total)")
    print(f"  Consider adding higher-level (Apply/Analyze) questions for these topics.\n")


def print_mcq_level(question: str):
    """Print classification for a single question."""
    level = classify_mcq(question)
    print(f"\n  Question: {question[:80]}...")
    print(f"  Bloom's Level: {level}\n")


def main():
    parser = argparse.ArgumentParser(description="Bloom's taxonomy classifier for MCQs")
    parser.add_argument("--profile", action="store_true",
                        help="Print topic-level Bloom profile")
    parser.add_argument("--question", type=str,
                        help="Classify a single question")
    args = parser.parse_args()

    if args.question:
        print_mcq_level(args.question)
    else:
        print_bloom_profile()


if __name__ == "__main__":
    main()