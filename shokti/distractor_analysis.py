"""Distractor entropy analyzer."""
import argparse
import sqlite3
from collections import Counter
from shokti.core.config import DB_PATH


DISTRACTOR_TYPES = ["too_broad", "too_narrow", "concept_confused", "irrelevant"]


def analyze_distractors(conn) -> dict:
    """For each topic, analyze wrong options.
    Classify: 'too_broad', 'too_narrow', 'concept_confused', 'irrelevant'
    Find: option letter bias, option length patterns
    Return distractor_guide dict."""
    cursor = conn.cursor()

    # Collect all wrong answers with their options and topics
    cursor.execute("""
        SELECT
            q.topic_name,
            sal.selected_option,
            sal.is_correct,
            q.options,
            q.correct_answer,
            q.question
        FROM student_answer_log sal
        JOIN question_bank q ON sal.mcq_id = q.id
        WHERE sal.is_correct = 0 AND sal.selected_option IS NOT NULL AND sal.selected_option != ''
    """)
    rows = cursor.fetchall()

    # Per-topic analysis
    topic_data = {}
    for row in rows:
        topic_name, selected, _, options_json, correct_json, question = row
        import json
        opts = json.loads(options_json) if options_json else {}
        correct_data = json.loads(correct_json) if correct_json else {}
        correct = correct_data.get("option", "") if isinstance(correct_data, dict) else ""
        if topic_name not in topic_data:
            topic_data[topic_name] = {
                "wrong_selections": Counter(),
                "options_chosen": [],
                "option_lengths": [],
                "letter_bias": Counter(),
                "distractors_by_type": Counter(),
            }

        d = topic_data[topic_name]
        # Track which wrong option was selected
        if selected in ("A", "B", "C", "D"):
            d["wrong_selections"][selected] += 1
            d["letter_bias"][selected] += 1

        # Get the wrong option text
        wrong_text = opts.get(selected, "") if isinstance(opts, dict) else ""

        # Classify the wrong option
        opt_class = classify_distractor(wrong_text, correct, question)
        d["distractors_by_type"][opt_class] += 1

        # Track option length
        d["option_lengths"].append(len(wrong_text) if wrong_text else 0)

    # Build distractor guide
    distractor_guide = {}
    for topic, data in topic_data.items():
        # Option letter bias
        most_biased = data["letter_bias"].most_common(1)
        letter_bias = most_biased[0][0] if most_biased else None
        total_bias = sum(data["letter_bias"].values())
        bias_pct = (most_biased[0][1] / total_bias * 100) if total_bias > 0 else 0

        # Average wrong option length
        avg_len = sum(data["option_lengths"]) / len(data["option_lengths"]) if data["option_lengths"] else 0

        # Most common distractor type
        type_counts = data["distractors_by_type"]
        dominant_type = type_counts.most_common(1)[0][0] if type_counts else "unknown"
        type_pct = (type_counts.most_common(1)[0][1] / sum(type_counts.values()) * 100) if type_counts else 0

        distractor_guide[topic] = {
            "wrong_answer_count": sum(data["wrong_selections"].values()),
            "letter_bias": letter_bias,
            "bias_pct": round(bias_pct, 1),
            "avg_wrong_option_length": round(avg_len, 1),
            "dominant_distractor_type": dominant_type,
            "dominant_type_pct": round(type_pct, 1),
            "type_breakdown": dict(type_counts),
        }

    return distractor_guide


def classify_distractor(wrong_text: str, correct_option: str, question: str) -> str:
    """Classify a distractor into a category."""
    if not wrong_text:
        return "irrelevant"

    w = wrong_text.lower()
    c = correct_option.lower() if isinstance(correct_option, str) else ""

    # Check if it's too broad (the correct answer concept expanded beyond scope)
    # e.g., "all plants are..." when specific answer is "bryophytes"
    broad_markers = ["all", "every", "always", "none", "never", "any plant", "all living"]
    too_broad = any(m in w for m in broad_markers)

    # Check if it's too narrow (correct answer simplified)
    # e.g., "spore" when the answer is "generative cell"
    narrow_markers = ["only", "just", "specifically", "exactly", "merely"]
    too_narrow = any(m in w for m in narrow_markers)

    # Check if concepts are confused (related but wrong biological structure)
    confused_pairs = [
        ("archegonium", "antheridium"),
        ("sporophyte", "gametophyte"),
        ("xylem", "phloem"),
        ("meiosis", "mitosis"),
        ("monocot", "dicot"),
        ("anther", "pollen"),
        ("ovary", "ovule"),
    ]
    concept_confused = any(
        (a in w and b in c) or (b in w and a in c)
        for a, b in confused_pairs
    )

    # Check if completely irrelevant
    question_words = set(question.lower().split())
    wrong_words = set(wrong_text.lower().split())
    overlap = len(question_words & wrong_words) / max(len(wrong_words), 1)
    irrelevant = overlap < 0.1 and len(wrong_text) > 10

    if concept_confused:
        return "concept_confused"
    elif too_broad:
        return "too_broad"
    elif too_narrow:
        return "too_narrow"
    elif irrelevant:
        return "irrelevant"
    else:
        return "concept_confused"  # default


def print_distractor_analysis():
    """Print analysis results."""
    conn = sqlite3.connect(DB_PATH)
    guide = analyze_distractors(conn)
    conn.close()

    if not guide:
        print("\nNo distractor data available.\n")
        return

    print(f"\n{'='*75}")
    print(f"  DISTRACTOR ANALYSIS REPORT")
    print(f"{'='*75}")
    print(f"  {'Topic':<30} {'Wrong':>5} {'Bias':>4} {'Bias%':>6} {'Dom Type':>16} {'AvgLen':>6}")
    print(f"  {'-'*30} {'-'*5} {'-'*4} {'-'*6} {'-'*16} {'-'*6}")

    for topic, data in sorted(guide.items(), key=lambda x: x[1]["wrong_answer_count"], reverse=True):
        topic_short = topic[:27] + "..." if len(topic) > 30 else topic
        print(f"  {topic_short:<30} {data['wrong_answer_count']:>5} "
              f"{data['letter_bias'] or '?':>4} {data['bias_pct']:>5.1f}% "
              f"{data['dominant_distractor_type']:<16} {data['avg_wrong_option_length']:>6.1f}")

    print(f"{'='*75}")
    print(f"\n  DOMINANT DISTRACTOR TYPE BREAKDOWN (by topic):")
    for topic, data in sorted(guide.items(), key=lambda x: x[1]["wrong_answer_count"], reverse=True):
        tb = data["type_breakdown"]
        breakdown_str = ", ".join(f"{k}:{v}" for k, v in sorted(tb.items(), key=lambda x: -x[1]))
        print(f"    {topic[:40]}: {breakdown_str}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Distractor entropy analyzer")
    parser.add_argument("--print", action="store_true", help="Print analysis report")
    args = parser.parse_args()

    print_distractor_analysis()


if __name__ == "__main__":
    main()