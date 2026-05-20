"""Simulated student for evolution benchmark — per-day learning trajectory."""
import random
import sqlite3
import json
import uuid
from shokti.core.config import DB_PATH
from shokti.sampling_weights import log_session_sampling


def _get_correct_option(mcq: dict) -> str:
    ca = mcq.get("correct_answer", {})
    if isinstance(ca, str):
        ca = json.loads(ca)
    if isinstance(ca, dict):
        return ca.get("option", "A")
    return "A"


def _wrong_option(correct: str) -> str:
    return {"A": "B", "B": "C", "C": "D", "D": "A"}[correct]


class SimulatedStudent:
    def __init__(self, student_id: str, profile: dict, conn: sqlite3.Connection):
        self.student_id = student_id
        self.profile = profile
        self.conn = conn
        # Mutable copy of per-topic accuracy — evolves as student learns
        self._topic_accuracy = dict(profile.get("topic_accuracy", {}))
        # Checkin trajectory: list of accuracy values [day1, day2, ...]
        self._checkin_trajectory: list[float] = []
        # Cumulative MCQs answered (used for gap threshold logic)
        self._cumulative_mcqs_answered: int = 0
        # First and last day checkin accuracies for growth measurement
        self._first_day_acc: float | None = None
        self._last_day_acc: float | None = None

    @property
    def growth(self) -> float:
        """Growth = mean final topic accuracy minus mean initial topic accuracy.

        Uses only topics present in both snapshots for a fair comparison.
        """
        if not self._topic_accuracy:
            return 0.0
        initial = self.profile.get("topic_accuracy", {})
        current = self._topic_accuracy
        # Compute per-topic delta, only for topics that existed at start
        deltas = []
        for key, init_acc in initial.items():
            if key in current:
                deltas.append(current[key] - init_acc)
        if not deltas:
            return 0.0
        return sum(deltas) / len(deltas)

    def _decide_answer(self, mcq_id: int, topic_key: tuple, correct_option: str) -> tuple[str, bool]:
        key_str = f"{topic_key[0]}_{topic_key[1]}"
        p = self._topic_accuracy.get(topic_key, 0.5)
        seed = self.profile["seed"]
        h = hash((seed, mcq_id, self.student_id))
        if h < 0:
            h = -h
        h = h % 10000
        is_correct = (h / 10000) < p
        # Learning effect: update topic accuracy after each attempt
        learning_rate = 0.03
        if is_correct:
            p = p + learning_rate * (1.0 - p)
        else:
            p = p - learning_rate * p
        self._topic_accuracy[topic_key] = max(0.05, min(0.95, p))
        self._cumulative_mcqs_answered += 1
        if is_correct:
            return (correct_option, True)
        return (_wrong_option(correct_option), False)

    def _log_mcq(
        self,
        session_id: str,
        session_type: str,
        mcq: dict,
        selected_option: str,
        is_correct: bool,
    ) -> None:
        """Insert a single MCQ result into student_answer_log."""
        self.conn.execute(
            "INSERT INTO student_answer_log"
            " (student_id, mcq_id, is_correct, time_spent_seconds,"
            "  session_type, session_id, selected_option)"
            " VALUES (?, ?, ?, 0, ?, ?, ?)",
            (
                self.student_id,
                mcq["id"],
                1 if is_correct else 0,
                session_type,
                session_id,
                selected_option,
            ),
        )

    def _log_sampling(self, session_id: str, session_type: str, mcqs: list[dict]) -> None:
        """Proxy to log_session_sampling."""
        log_session_sampling(self.conn, session_id, self.student_id, session_type, mcqs)

    def run_exams(self, exam_ids: list[str]) -> dict[str, dict]:
        """Run preset exams (used for day-0 baseline). Unchanged."""
        from shokti.exams.exam_config import load_exam
        results = {}
        for exam_id in exam_ids:
            exam = load_exam(exam_id)
            mcq_ids = exam["mcq_ids"]
            session_id = str(uuid.uuid4())
            mcq_rows = []
            correct = 0
            total = len(mcq_ids)
            for mid in mcq_ids:
                row = self.conn.execute(
                    "SELECT id, topic_id, topic_name, chapter_id, correct_answer"
                    " FROM question_bank WHERE id = ?",
                    (mid,),
                ).fetchone()
                if not row:
                    continue
                mcq = dict(row)
                chapter_id = mcq["chapter_id"]
                topic_id = mcq["topic_id"]
                topic_key = (chapter_id, topic_id)
                correct_option = _get_correct_option(mcq)
                mcq_id_int = int(mid)
                _, is_corr = self._decide_answer(mcq_id_int, topic_key, correct_option)
                if is_corr:
                    correct += 1
                self._log_mcq(session_id, f"exam{exam_id}", mcq,
                              correct_option if is_corr else _wrong_option(correct_option),
                              is_corr)
                mcq_rows.append(mcq)
            self._log_sampling(session_id, f"exam{exam_id}", mcq_rows)
            results[exam_id] = {
                "correct": correct,
                "total": total,
                "accuracy": correct / total if total else 0,
            }
        self.conn.commit()
        return results

    def run_checkin(self, day: int) -> float:
        """10 random MCQs across all topics. Deterministic via hash(seed, student_id, day)."""
        from shokti.evolution.config import ALL_TOPIC_KEYS, CHECKIN_COUNT

        seed = self.profile["seed"]
        rng = random.Random(hash((seed, self.student_id, day)) % (2**31))

        all_mcqs = [
            dict(r) for r in self.conn.execute(
                "SELECT * FROM question_bank ORDER BY RANDOM() LIMIT ?",
                (CHECKIN_COUNT * 3,),  # oversample then pick
            ).fetchall()
        ]
        rng.shuffle(all_mcqs)
        selected = all_mcqs[:CHECKIN_COUNT]

        session_id = str(uuid.uuid4())
        correct = 0
        for mcq in selected:
            mcq_id_int = int(mcq["id"])
            topic_key = (mcq["chapter_id"], mcq["topic_id"])
            correct_option = _get_correct_option(mcq)
            _, is_correct = self._decide_answer(mcq_id_int, topic_key, correct_option)
            if is_correct:
                correct += 1
            self._log_mcq(session_id, f"checkin_d{day}", mcq,
                          correct_option if is_correct else _wrong_option(correct_option),
                          is_correct)
        self._log_sampling(session_id, f"checkin_d{day}", selected)
        self.conn.commit()
        return correct / CHECKIN_COUNT if CHECKIN_COUNT else 0.0

    def run_adaptive_exam(
        self,
        config: dict,
        exam_idx: int,
        day: int,
    ) -> dict:
        """One adaptive exam using build_evolvable_session config params."""
        from shokti.evolution.session_builder import build_evolvable_session
        from shokti.evolution.config import MCQS_PER_EXAM

        session_id = str(uuid.uuid4())
        mcqs, _ = build_evolvable_session(self.conn, self, config, MCQS_PER_EXAM)

        correct = 0
        topic_results: dict[tuple, dict] = {}
        for mcq in mcqs:
            mcq_id_int = int(mcq["id"])
            topic_key = (mcq["chapter_id"], mcq["topic_id"])
            correct_option = _get_correct_option(mcq)
            _, is_correct = self._decide_answer(mcq_id_int, topic_key, correct_option)
            if is_correct:
                correct += 1
            self._log_mcq(session_id, f"adaptive_d{day}_e{exam_idx}", mcq,
                          correct_option if is_correct else _wrong_option(correct_option),
                          is_correct)
            tk = topic_key
            if tk not in topic_results:
                topic_results[tk] = {"correct": 0, "total": 0}
            topic_results[tk]["total"] += 1
            if is_correct:
                topic_results[tk]["correct"] += 1

        self._log_sampling(session_id, f"adaptive_d{day}_e{exam_idx}", mcqs)
        self.conn.commit()

        return {
            "accuracy": correct / len(mcqs) if mcqs else 0,
            "topic_results": topic_results,
        }

    def run_day(self, day: int, config: dict) -> dict:
        """One day = 1 checkin + EXAMS_PER_DAY adaptive exams."""
        from shokti.evolution.config import EXAMS_PER_DAY

        checkin_acc = self.run_checkin(day)
        self._checkin_trajectory.append(checkin_acc)
        if self._first_day_acc is None:
            self._first_day_acc = checkin_acc
        self._last_day_acc = checkin_acc

        exam_accs = []
        for i in range(EXAMS_PER_DAY):
            result = self.run_adaptive_exam(config, i, day)
            exam_accs.append(result)
        return {"day": day, "checkin": checkin_acc, "exams": exam_accs}

    def cleanup(self) -> None:
        """Delete all traces of this simulated student."""
        self.conn.execute(
            "DELETE FROM student_answer_log WHERE student_id = ?",
            (self.student_id,),
        )
        self.conn.execute(
            "DELETE FROM student_mcq_stats WHERE student_id = ?",
            (self.student_id,),
        )
        self.conn.execute(
            "DELETE FROM topic_sampling_log WHERE student_id = ?",
            (self.student_id,),
        )
        self.conn.commit()