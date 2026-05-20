"""Confusion cluster mapping — detects mixed-up concepts."""
import argparse
import sqlite3
from shokti.core.config import DB_PATH


def detect_confusion_clusters(student_id: str, min_wrong: int = 5) -> list:
    """Find topics where student has 5+ wrong answers.
    Then use Gemini File Search to find semantically similar MCQ pairs
    that student might confuse (e.g. Archegonium ↔ Antheridium).
    Return list of confusion pairs with explanation."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Find topics with >= min_wrong wrong answers
    cursor.execute("""
        SELECT
            q.topic_name,
            COUNT(*) as wrong_count
        FROM student_answer_log sal
        JOIN question_bank q ON sal.mcq_id = q.id
        WHERE sal.student_id = ?
          AND sal.is_correct = 0
        GROUP BY q.topic_name
        HAVING COUNT(*) >= ?
        ORDER BY wrong_count DESC
    """, (student_id, min_wrong))
    weak_topics = cursor.fetchall()

    if not weak_topics:
        conn.close()
        return []

    # For each weak topic, get the wrong MCQs and their concepts
    confusion_pairs = []

    for topic, wrong_count in weak_topics:
        # Get questions from this topic that student got wrong
        import json
        cursor.execute("""
            SELECT q.id, q.question, q.correct_answer, q.options
            FROM student_answer_log sal
            JOIN question_bank q ON sal.mcq_id = q.id
            WHERE sal.student_id = ? AND q.topic_name = ? AND sal.is_correct = 0
            GROUP BY q.id
            LIMIT 20
        """, (student_id, topic))
        wrong_mcqs = cursor.fetchall()

        # Find key terms in the wrong questions to identify likely confusion
        for mcq in wrong_mcqs:
            qid, question, correct_json, options_json = mcq
            correct = json.loads(correct_json) if isinstance(correct_json, str) else correct_json
            correct = correct.get('option', 'A') if isinstance(correct, dict) else correct
            opts = json.loads(options_json) if isinstance(options_json, str) else options_json
            # Extract likely confused terms by comparing wrong options with correct
            # (Students who confuse archegonium with antheridium will have similar question patterns)
            terms = extract_key_terms(question)
            for term in terms:
                # Look for other MCQs containing semantically similar terms
                # (placeholder for Gemini File Search integration)
                similar = find_similar_mcqs(conn, term, topic, student_id, qid)
                if similar:
                    confusion_pairs.append({
                        "topic": topic,
                        "source_mcq": {"id": qid, "question": question, "correct": correct},
                        "confused_with": similar,
                        "explanation": f"Student confused '{term}' concept in {topic}",
                        "wrong_count": wrong_count,
                    })

    conn.close()
    return confusion_pairs


def extract_key_terms(question: str) -> list:
    """Extract key biological terms from question text."""
    # Common patterns: terms in parentheses, all-caps words, or quoted terms
    import re
    # Find terms in parentheses
    parens = re.findall(r'\(([^)]+)\)', question)
    # Find all-caps multi-letter words (biological terms)
    caps = re.findall(r'\b[A-Z][a-z]{2,}\b', question)
    # Find quoted terms
    quoted = re.findall(r'"([^"]+)"', question)
    terms = [t.strip() for t in parens + caps + quoted if len(t) > 3]
    return terms


def find_similar_mcqs(conn, term: str, exclude_topic: str, student_id: str, exclude_qid: int) -> dict | None:
    """Find MCQ with similar term in different topic (potential confusion)."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, question, topic_name, correct_answer
        FROM question_bank
        WHERE topic_name != ?
          AND (question LIKE ? OR options LIKE ?)
          AND id NOT IN (
              SELECT mcq_id FROM student_answer_log
              WHERE student_id = ? AND is_correct = 0
          )
        LIMIT 3
    """, (exclude_topic, f"%{term}%", f"%{term}%", student_id))
    similar = cursor.fetchone()
    if similar:
        import json
        ca = json.loads(similar[3]) if isinstance(similar[3], str) else similar[3]
        correct_letter = ca.get("option", "?") if isinstance(ca, dict) else similar[3]
        return {
            "id": similar[0],
            "question": similar[1],
            "topic": similar[2],
            "correct": correct_letter,
        }
    return None


def print_confusion_clusters(student_id: str, min_wrong: int = 5):
    """CLI: print confusion clusters."""
    pairs = detect_confusion_clusters(student_id, min_wrong)
    if not pairs:
        print(f"\nNo confusion clusters found for student {student_id} (min_wrong={min_wrong}).\n")
        return

    import json
    print(f"\n{'='*65}")
    print(f"  CONFUSION CLUSTERS (min wrong={min_wrong})")
    print(f"{'='*65}")
    for i, pair in enumerate(pairs, 1):
        src = pair["source_mcq"]
        tgt = pair["confused_with"]
        tgt_correct = tgt.get("correct", "")
        if isinstance(tgt_correct, str):
            try:
                tgt_correct = json.loads(tgt_correct).get("option", tgt_correct)
            except (json.JSONDecodeError, AttributeError):
                pass
        print(f"\n  [{i}] Topic: {pair['topic']} ({pair['wrong_count']} wrong)")
        print(f"      Source Q: {src['question'][:70]}...")
        print(f"      Correct:  {src['correct']}")
        print(f"      Confused with: {tgt['question'][:60]}...")
        print(f"      In topic: {tgt['topic']}  | Correct: {tgt_correct}")
        print(f"      Why confused: {pair['explanation']}")
    print(f"\n{'='*65}\n")


def main():
    parser = argparse.ArgumentParser(description="Confusion cluster mapping")
    parser.add_argument("student_id", nargs="?", default="STUDENT_001",
                        help="Student ID (default: STUDENT_001)")
    parser.add_argument("--min-wrong", type=int, default=5,
                        help="Minimum wrong answers to flag a topic (default: 5)")
    args = parser.parse_args()
    print_confusion_clusters(args.student_id, args.min_wrong)


if __name__ == "__main__":
    main()