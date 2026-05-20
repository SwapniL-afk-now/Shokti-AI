# Shokti MCQ System — Implementation Plan

**Last updated:** 2026-05-18
**Goal:** Build a student learning system powered by Gemini File Search and a question bank. Two-stage gated system: fixed exams → adaptive practice.

---

## Architecture

```
question_bank JSON files (existing MCQs)
        ↓
SQLite Question Bank Index
        ↓
┌────────────────────────────────────────────────────┐
│                 STAGE 1: FIXED EXAMS               │
│  (Mandatory. Same 90 MCQs for all students.)       │
│  Files: exams/exam_1.json, exam_2.json, exam_3.json│
│  Runner: exams/exam_runner.py                      │
│  Tracks: student_answer_log (with session_type)    │
│  No sampling intelligence. Baseline profiling.     │
└──────────────────────┬─────────────────────────────┘
                       ↓  (all 3 exams completed)
┌────────────────────────────────────────────────────┐
│             STAGE 2: ADAPTIVE PRACTICE              │
│  (Unlocked per-student after 3 exams.)             │
│  Files: adaptive_practice.py + session_builder     │
│  Selection: sampling_weights.py                     │
│    - weakness_signal (student_answer_log)           │
│  × - sampling_debt_signal (topic_sampling_log)      │
│  × - exam_importance_signal (exam_trend table)      │
│  Evolves after every session.                      │
└────────────────────────────────────────────────────┘
        ↓
File Search API (Hasan Sir PDFs — indexed)
        ↓
MCQ Generation + Gap Filling (File Search RAG)
        ↓
Analytics: coverage, gaps, trends, performance
```

**Tech stack:**
- `File Search API` — semantic search, MCQ generation, topic clustering
- `SQLite` — question bank index, student state, usage tracking, exam definitions
- `Python` — all scripts and pipelines

---

## Data Sources

| Source | Location | Status |
|---|---|---|
| Hasan Sir PDFs (indexed in File Search) | `books/chapters/` | ✅ Uploaded |
| Question bank MCQs (chapter 06) | `question_bank/chapter_06.json` | 60+ MCQs |
| Question bank MCQs (chapter 08) | `question_bank/chapter_08.json` | Need to read |
| Medical Qbank PDF (chapters 01-03) | `question_bank/chapter_01-03.json` | ✅ Extracted |

---

## DONE

- [x] Two-pass MCQ generation pipeline (generate + cite)
- [x] Per-MCQ: source_quote, pdf_page_number, practice_related_questions
- [x] Removed redundant fields from output
- [x] Removed dead code
- [x] Redesigned citation prompt for dual-purpose File Search
- [x] Pushed to GitHub
- [x] Step 1.1: JSON → SQLite importer (json_importer.py)
- [x] Step 1.2: Bank overview script (bank_stats.py)
- [x] Step 2.1: Coverage gap analyzer (gap_analyzer.py)
- [x] Step 2.2: MCQ gap generator (gap_filler.py)
- [x] Step 3.1a: Diagnostic baseline practice (practice.py) — random mode
- [x] Step 3.1b: Adaptive session builder (session_builder.py) — gaussian weighting logic
- [x] Step 3.1c: Weak topic tracker (weak_topic_tracker.py)
- [x] Step 3.1d: MCQ generator for full chapters (mcq_generator.py)
- [x] File Search uploaders (file_uploader.py, medical_file_uploader.py)
- [x] Medical MCQ gap fillers (fill_mcq_gaps.py, fill_remaining_gaps.py)

---

## Implementation Phases

### Phase 0: Schema Expansion — Track What's Missing

**Goal:** Add tracking columns and tables that Stages 1 and 2 both depend on.

---

#### Step 0.1: Add `session_type` and `session_id` to `student_answer_log`

**File:** `shokti/ingest/schema_migration_v2.py`

**Changes:**
```sql
ALTER TABLE student_answer_log ADD COLUMN session_type TEXT DEFAULT 'diagnostic';
  -- Values: 'diagnostic', 'exam1', 'exam2', 'exam3', 'adaptive'
ALTER TABLE student_answer_log ADD COLUMN session_id TEXT;
  -- UUID per session, links answers + sampling log together
```

**Backfill:** Existing rows get `session_type='diagnostic'` and NULL session_id.

---

#### Step 0.2: Create `topic_sampling_log` Table

**File:** `shokti/ingest/schema_migration_v2.py`

```sql
CREATE TABLE topic_sampling_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    student_id TEXT,
    session_type TEXT,
    chapter_id TEXT,
    topic_id TEXT,
    times_sampled INTEGER DEFAULT 0,
    session_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tsl_student ON topic_sampling_log(student_id);
CREATE INDEX idx_tsl_session ON topic_sampling_log(session_id);
```

---

#### Step 0.3: Create `exam_trend` Table

```sql
CREATE TABLE exam_trend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id TEXT,
    topic_id TEXT,
    appearance_frequency REAL DEFAULT 0.0,   -- how often this topic appears in exams
    trend_direction TEXT DEFAULT 'stable',    -- 'rising', 'stable', 'declining'
    last_analyzed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

#### Step 0.4: Ensure `appearance_counter` Is Populated

The column exists in `question_bank` but is never set. Migration sets all existing rows to 0.
Future updates increment it every time an MCQ is served (both exam and adaptive modes).

---

### Phase 1: Stage 1 — Fixed Exams

**Goal:** Three predefined exams. Same 30 MCQs each for every student. Establish baseline.

---

#### Step 1.1: Exam Configuration Files

**File:** `shokti/exams/exam_1.json`

```json
{
  "exam_id": "1",
  "title": "মডেল টেস্ট ১ — ব্রায়োফাইটা ও টেরিডোফাইটা",
  "description": "Chapter 06 focused. 30 MCQs, 30 minutes.",
  "duration_minutes": 30,
  "total_mcqs": 30,
  "mcq_ids": [4, 7, 12, 15, 18, 23, 27, 31, 35, 38,
               42, 46, 50, 53, 57, 61, 64, 68, 72, 75,
               79, 83, 86, 90, 94, 97, 101, 105, 108, 112],
  "topic_breakdown": {
    "ব্রায়োফাইটা": 8,
    "Riccia": 7,
    "টেরিডোফাইটা": 5,
    "Pteris": 10
  },
  "chapter_ids": ["06"],
  "difficulty_mix": {
    "easy": 0.25,
    "medium": 0.50,
    "hard": 0.25
  }
}
```

**Files:** `shokti/exams/exam_1.json`, `exam_2.json`, `exam_3.json`

Design principles for exam creation:
- Exam 1: Chapter 06 (Bryophyta + Pteridophyta) — focused
- Exam 2: Chapter 08 (Tissue System) — focused
- Exam 3: Mixed from both chapters — comprehensive
- MCQ IDs selected to cover all topics proportionally
- Order matters: easy questions first, difficulty increases

---

#### Step 1.2: Exam Config Loader

**File:** `shokti/exams/exam_config.py`

```python
"""Exam definitions, loading, and unlock logic."""

EXAM_IDS = ["1", "2", "3"]
EXAM_DIR = Path(__file__).parent
EXAM_FILES = {
    "1": "exam_1.json",
    "2": "exam_2.json",
    "3": "exam_3.json",
}

def load_exam(exam_id: str) -> dict:
    """Load exam JSON definition."""

def get_student_exam_status(conn, student_id) -> dict:
    """Return which exams the student has completed.
    Returns: {'1': True, '2': False, '3': False}
    """

def has_completed_all_exams(conn, student_id) -> bool:
    """Return True if student finished all 3 exams."""

def get_next_incomplete_exam(conn, student_id) -> str | None:
    """Return the next exam_id the student hasn't taken, or None."""
```

---

#### Step 1.3: Exam Runner

**File:** `shokti/exams/exam_runner.py`

**What it does:**
1. Takes `exam_id` and `student_id`
2. Loads the exam JSON
3. Fetches MCQ rows from SQLite by `mcq_ids` (preserving order)
4. Serves MCQs one at a time with a timer (30 min total)
5. After each answer: shows correct/incorrect + explanation
6. Records to `student_answer_log` with:
   - `session_type = f'exam{exam_id}'`
   - `session_id = uuid4()`
7. Increments `appearance_counter` for each MCQ served
8. Writes one `topic_sampling_log` row per session
9. At end: shows score + time spent

**Key difference from diagnostic practice:**
- Fixed question set, not random
- Timer enforced
- No adaptive selection
- Results feed into baseline profile

---

#### Step 1.4: Practice Router

**File:** `shokti/practice.py` (revised)

**What it does:**
- Entry point CLI: `python shokti/practice.py`
- Detects student's exam status
- Routes to the right mode:

```
practice.py
  ├── Mode detection:
  │   └── has_completed_all_exams(student)?
  │         NO  → exams/exam_runner.py (next incomplete exam)
  │         YES → adaptive_practice.py
  │
  └── CLI flags for override:
      --mode exam       → force exam mode
      --exam-id 1       → specific exam
      --mode diagnostic → old random mode (optional)
      --mode adaptive   → force adaptive even if exams not done
```

---

### Phase 2: Stage 2 — Adaptive Practice

**Goal:** Once 3 exams are done, the system enters self-evolving mode.

---

#### Step 2.1: Sampling Weights Engine

**File:** `shokti/sampling_weights.py`

**What it does:**
Computes a combined selection weight for each topic by blending 3 signals:

```
weight(topic) = weakness_signal × W1
              + sampling_debt_signal × W2
              + exam_importance_signal × W3

Default ratio:  W1=0.40 (weakness)
                W2=0.35 (sampling debt)
                W3=0.25 (exam importance)
```

**Functions:**

```python
def get_combined_weights(conn, student_id) -> dict[str, float]:
    """Return {topic_id: weight} for all topics. Weights sum to 1.0."""

def compute_weakness_signal(conn, student_id) -> dict[str, float]:
    """Low accuracy → high weight.
    weight = 1.0 - accuracy
    0% accuracy → 1.0  (highest priority)
    100% accuracy → 0.0 (skip)
    """

def compute_sampling_debt(conn, student_id) -> dict[str, float]:
    """Fewer times sampled → higher weight (relative to peer avg).
    debt = max(0, peer_avg - student_times) / peer_avg
    """

def compute_exam_importance(conn) -> dict[str, float]:
    """From exam_trend table.
    Higher appearance_frequency → higher weight.
    """

def log_session_sampling(conn, session_id, student_id, session_type, mcqs):
    """Write topic_sampling_log after each session.
    Groups MCQs by topic_id → counts times_sampled per topic.
    """
```

**Config in `config.py`:**
```python
@dataclass
class SamplingConfig:
    WEAKNESS_WEIGHT: float = 0.40
    DEBT_WEIGHT: float = 0.35
    IMPORTANCE_WEIGHT: float = 0.25
    DEBT_PEER_WINDOW_DAYS: int = 30  # how far back to compute peer avg
```

---

#### Step 2.2: Adaptive Practice Runner

**File:** `shokti/adaptive_practice.py`

**What it does:**
1. Checks `has_completed_all_exams()` — gate
2. Calls `sampling_weights.get_combined_weights()` for the student
3. Passes weights to `session_builder.build_session()`
4. Serves MCQs one at a time
5. Records answers with `session_type='adaptive'` and `session_id`
6. Increments `appearance_counter`
7. Writes `topic_sampling_log` (feeds back into weights for next session)
8. At end: shows accuracy + compares against baseline from exams

**Modes:**
- `adaptive` (default) — combined weights
- `weakness` — pure weakness signal (ignore debt/importance)
- `coverage` — pure debt signal (maximize topic exposure)
- `timed` — countdown per MCQ (30/45/60s levels)

**Per-session flow:**
```
1. Compute weights
2. Select 30 MCQs via weighted sampling
3. Show MCQ → student answers → record + increment counter
4. Repeat for all 30
5. Log topic_sampling for the session
6. Print summary with comparison to exam baseline
```

---

#### Step 2.3: Revise Session Builder

**File:** `shokti/selectors/session_builder.py` (revised)

**Changes:**
- Remove hardcoded ratios (`QBANK_RATIO`, `GENERATED_RATIO`) that don't exist
- Accept external `topic_weights: dict[str, float]` parameter
- Replace gaussian weighting with weighted sampling using incoming weights
- Keep `_build_weakness_session`, `_build_coverage_session`, `_build_random_session` as fallbacks

```python
def build_session(
    conn, student_id, count=30, mode="adaptive",
    topic_weights: dict[str, float] | None = None,
) -> tuple[list[dict], SessionComposition]:
```

When `mode='adaptive'` and `topic_weights` provided:
- For each MCQ, assign weight = `topic_weights.get(mcq['topic_id'], 0.1)`
- Use weighted reservoir sampling to select `count` MCQs

---

### Phase 3: MCQ Quality Pipeline

**Goal:** Ensure the bank has enough quality MCQs in every topic.

---

#### Step 3.1: Coverage Gap Analyzer (existing — extended)

**File:** `shokti/coverage_gaps.py`

**New student-aware output:**
```
Coverage for testrun:
  Topics with <5 MCQs globally:
    - টেরিডোফাইটা: 7 MCQs (need 3 more to reach 10?)
  Topics student hasn't seen:
    - আবরণী টিস্যু (19 MCQs in bank, 0 attempted)
    - গ্রাউন্ড টিস্যুতন্ত্র (11 MCQs in bank, 0 attempted)
```

---

#### Step 3.2: Exam Trend Analyzer

**File:** `shokti/topic_priority.py` (expanded from Step 5.1)

**What it does:**
- Reads all student data across all exams + adaptive sessions
- Counts: which topics appear most frequently in exams
- Computes: `appearance_frequency = times_topic_appeared / total_exam_questions`
- Writes to `exam_trend` table
- Detects trend direction by comparing last 30 days to previous 30 days

**Output:**
```
Topic Importance (from 120 exam questions across all students):
  Topic                        Frequency  Trend
  ব্রায়োফাইটা                  32%       stable
  Pteris                       25%       declining
  Riccia                       20%       rising
  টেরিডোফাইটা                   10%       stable
  আবরণী টিস্যু                  30%       rising
```

This feeds directly into `sampling_weights.compute_exam_importance()`.

---

#### Step 3.3: MCQ Generator for Gaps (existing)

**File:** `shokti/generate_gaps.py`

No changes needed — reads gaps from SQLite, generates MCQs via File Search, writes back.

---

#### Step 3.4: Distractor Entropy Analyzer (new)

**File:** `shokti/distractor_analysis.py`

**What it does:**
- For each topic, analyze wrong options
- Classify: "too broad", "too narrow", "concept confused", "irrelevant"
- Find: option letter bias, option length patterns
- Output: `distractor_guide.json` — rules for better distractors

---

#### Step 3.5: Bloom's Taxonomy Classifier (new)

**File:** `shokti/bloom_classifier.py`

Classifies each MCQ: Remember / Understand / Apply / Analyze / Evaluate.
Flags topics with only low-level MCQs.

---

### Phase 4: Student Analytics

**Goal:** Turn exam + adaptive data into insights.

---

#### Step 4.1: Exam Baseline Report

**File:** `shokti/exams/exam_report.py`

**What it does:**
After each exam, print:
```
=== Exam 1 Report: testrun ===
Score: 18/30 (60%) — Time: 24:32 / 30:00

Topic breakdown:
  ব্রায়োফাইটা:  6/8  (75%)
  Riccia:       5/7  (71%)
  টেরিডোফাইটা:   3/5  (60%)
  Pteris:       4/10 (40%) ← weak

Rank among all students: 12th / 45
```

---

#### Step 4.2: Pre-vs-Post Comparison

**File:** `shokti/adaptive_practice.py` (built into summary)

After each adaptive session, compare:
```
=== Adaptive Session Summary ===
Accuracy: 22/30 (73%)

Compared to your exam baseline (60%):
  +13% improvement overall
  ব্রায়োফাইটা: 75% → 88%  (+13%)
  Pteris:       40% → 60%  (+20%) ← improving!
```

---

#### Step 4.3: Student Performance Dashboard

**File:** `shokti/student_dashboard.py`

Per-student overview combining all data:
```
=== Student Dashboard: testrun ===
Exams completed: 3/3
Adaptive sessions: 12

Baseline accuracy (exams): 60%
Current accuracy (adaptive): 73%
Improvement: +13%

Topic accuracy history:
  ব্রায়োফাইটা:          75% → 85% → 88%  ↑
  Riccia:               71% → 70% → 75%  →
  Pteris:               40% → 50% → 60%  ↑
  টেরিডোফাইটা:           60% → 65% → 70%  ↑

Weakest topic: Pteris (60% — 15 sessions of improvement)
Most improved: Pteris (+20%)
Next adaptive focus: Pteris + ভাস্কুলার বান্ডল (least sampled)
```

---

### Phase 5: Advanced Features

---

#### Step 5.1: Confusion Cluster Mapping

**File:** `shokti/confusion_map.py`

After ≥5 wrong answers on a topic, File Search finds semantically similar MCQs.
Detects: "You confuse Archegonium ↔ Antheridium" patterns.

---

#### Step 5.2: Progress Graph

**File:** `shokti/progress_graph.py`

Accuracy per topic over time. PNG image output.

---

#### Step 5.3: Foundation Gap Finder

**File:** `shokti/foundation_gaps.py`

When student fails a topic, File Search finds its prerequisites.
"Study Bryophyta first. You don't have the foundation for Pteris."

---

#### Step 5.4: Spaced Repetition Scheduler

**File:** `shokti/spaced_repetition.py`

`next_review_at` from `student_mcq_stats`. Used by adaptive practice for MCQs due for review.

---

### Phase 6: Scale

When 1-3 users work → shared SQLite (Google Drive / Dropbox).
When 10+ users → PocketBase or Supabase.

---

## Complete Execution Order

```
Phase 0: Schema Expansion
  Step 0.1  Add session_type + session_id columns
  Step 0.2  Create topic_sampling_log table
  Step 0.3  Create exam_trend table
  Step 0.4  Reset + populate appearance_counter
      ↓

Phase 1: Stage 1 — Fixed Exams
  Step 1.1  Create exam_1.json, exam_2.json, exam_3.json
  Step 1.2  exam_config.py — loader + unlock logic
  Step 1.3  exam_runner.py — fixed exam session logic
  Step 1.4  practice.py — router (exam → adaptive)
      ↓

Phase 2: Stage 2 — Adaptive Practice
  Step 2.1  sampling_weights.py — 3-signal weight engine
  Step 2.2  adaptive_practice.py — evolving practice runner
  Step 2.3  session_builder.py — revised (weights-driven)
      ↓

Phase 3: MCQ Quality
  Step 3.1  coverage_gaps.py — extended (student-aware)
  Step 3.2  topic_priority.py — exam trend analyzer
  Step 3.3  generate_gaps.py — (existing, no change)
  Step 3.4  distractor_analysis.py
  Step 3.5  bloom_classifier.py
      ↓

Phase 4: Student Analytics
  Step 4.1  exam_report.py — per-exam + ranking
  Step 4.2  adaptive_practice.py — baseline comparison
  Step 4.3  student_dashboard.py — full profile
      ↓

Phase 5: Advanced
  Step 5.1  confusion_map.py
  Step 5.2  progress_graph.py
  Step 5.3  foundation_gaps.py
  Step 5.4  spaced_repetition.py
```

---

## File Structure (Complete)

```
shokti/
├── practice.py                  # Router: exam → adaptive
│
├── exams/                       # Stage 1: Fixed Exams
│   ├── __init__.py
│   ├── exam_1.json              # 30 predefined MCQ IDs
│   ├── exam_2.json              # 30 predefined MCQ IDs
│   ├── exam_3.json              # 30 predefined MCQ IDs
│   ├── exam_config.py           # Loader + unlock checker
│   └── exam_runner.py           # Fixed exam session logic
│
├── adaptive_practice.py         # Stage 2: Evolving practice
├── sampling_weights.py          # 3-signal weight engine
│
├── selectors/                   # Session builders (revised)
│   ├── __init__.py
│   ├── session_builder.py       # Weighted MCQ selection
│   └── weak_topic_tracker.py    # Topic stats (existing)
│
├── analytics/                   # Reports
│   ├── __init__.py
│   ├── bank_stats.py            # Bank overview (existing)
│   ├── gap_analyzer.py          # Coverage gaps (existing)
│   └── student_dashboard.py     # Full student profile
│
├── generators/                  # MCQ generation
│   ├── __init__.py
│   ├── mcq_generator.py         # Full chapter gen (existing)
│   └── gap_filler.py            # Gap gen (existing)
│
├── ingest/                      # Data ingestion
│   ├── __init__.py
│   ├── json_importer.py         # JSON → SQLite (existing)
│   ├── schema_migration_v2.py   # New tables + columns
│   ├── fill_mcq_gaps.py         # Medical MCQ gap fill (existing)
│   └── fill_remaining_gaps.py   # Remaining gaps (existing)
│
├── infrastructure/              # File uploads
│   ├── __init__.py
│   └── file_uploader.py         # File Search uploader (existing)
│
├── core/
│   └── config.py                # Central config (revised)
│
├── confusion_map.py             # Step 5.1
├── progress_graph.py            # Step 5.2
├── foundation_gaps.py           # Step 5.3
├── spaced_repetition.py         # Step 5.4
├── distractor_analysis.py       # Step 3.4
├── bloom_classifier.py          # Step 3.5
├── topic_priority.py            # Step 3.2 (exam trends)
├── exam_report.py               # Step 4.1
├── bank_stats.py                # Entry point → analytics (existing)
├── coverage_gaps.py             # Entry point → analytics (existing)
├── generate_gaps.py             # Entry point → generators (existing)
├── generate_mcqs.py             # Entry point → generators (existing)
├── index_qbank.py               # Entry point → ingest (existing)
├── file_search.py               # Entry point → infrastructure (existing)
└── requirements.txt             # Dependencies
```

---

## Key Dependencies (Updated)

```
Schema (Phase 0)    → everything else
Exam config (1.1)   → exam_runner (1.3)
Exam runner (1.3)   → student_answer_log
Student data         → sampling_weights (2.1)
sampling_weights (2.1) + session_builder (2.3) → adaptive_practice (2.2)
adaptive_practice (2.2) → topic_sampling_log (writes)
topic_sampling_log  → sampling_weights (2.1) [LOOP]
topic_priority (3.2) → exam_trend → sampling_weights (2.1) [LOOP]
exam_report (4.1)   → student_answer_log
student_dashboard (4.3) → all tables
```

**Self-evolving loop:**

```
adaptive_practice
    ↓ writes session data
topic_sampling_log + student_answer_log
    ↓ reads
sampling_weights (next session)
    ↓ feeds
adaptive_practice selection
    ↓
[repeats — weights evolve per session]
```

---

## Database Schema (Complete)

```sql
CREATE TABLE question_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT,
    chapter_id TEXT,
    chapter_name TEXT,
    book_page_range TEXT,
    source_file TEXT,
    topic_id TEXT,
    topic_name TEXT,
    question TEXT,
    options TEXT,           -- JSON {A,B,C,D}
    correct_answer TEXT,    -- JSON {option, text}
    source_quote TEXT DEFAULT '',
    pdf_page_number INTEGER,
    practice_related_questions TEXT DEFAULT '[]',
    appearance_counter INTEGER DEFAULT 0,   -- incremented every time served
    question_hash TEXT UNIQUE,
    difficulty TEXT DEFAULT 'medium',
    origin TEXT DEFAULT 'question_bank',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE student_answer_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT,
    mcq_id INTEGER,
    session_id TEXT,            -- UUID linking answers + sampling log
    session_type TEXT,          -- 'diagnostic' | 'exam1' | 'exam2' | 'exam3' | 'adaptive'
    is_correct BOOLEAN,
    confidence_rating INTEGER,  -- 1-5 (optional, for calibration)
    answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    time_spent_seconds INTEGER,
    FOREIGN KEY (mcq_id) REFERENCES question_bank(id)
);

CREATE TABLE student_mcq_stats (
    student_id TEXT,
    mcq_id INTEGER,
    correct_count INTEGER DEFAULT 0,
    wrong_count INTEGER DEFAULT 0,
    last_seen_at TIMESTAMP,
    next_review_at TIMESTAMP,
    FOREIGN KEY (mcq_id) REFERENCES question_bank(id),
    PRIMARY KEY (student_id, mcq_id)
);

CREATE TABLE topic_sampling_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    student_id TEXT,
    session_type TEXT,
    chapter_id TEXT,
    topic_id TEXT,
    times_sampled INTEGER DEFAULT 0,
    session_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE exam_trend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id TEXT,
    topic_id TEXT,
    appearance_frequency REAL DEFAULT 0.0,
    trend_direction TEXT DEFAULT 'stable',
    last_analyzed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chapter ON question_bank(chapter_id);
CREATE INDEX idx_topic ON question_bank(topic_id);
CREATE INDEX idx_hash ON question_bank(question_hash);
CREATE INDEX idx_student_answer ON student_answer_log(student_id, mcq_id);
CREATE INDEX idx_student_answer_session ON student_answer_log(session_id);
CREATE INDEX idx_tsl_student ON topic_sampling_log(student_id);
CREATE INDEX idx_tsl_session ON topic_sampling_log(session_id);
```

---

## Config Additions

In `shokti/core/config.py`, add:

```python
@dataclass
class SamplingConfig:
    """Sampling weight configuration."""
    WEAKNESS_WEIGHT: float = 0.40
    DEBT_WEIGHT: float = 0.35
    IMPORTANCE_WEIGHT: float = 0.25
    DEBT_PEER_WINDOW_DAYS: int = 30

@dataclass
class ExamConfig:
    """Exam configuration."""
    EXAM_IDS: list = field(default_factory=lambda: ["1", "2", "3"])
    DEFAULT_EXAM_COUNT: int = 30
    DEFAULT_EXAM_DURATION: int = 30  # minutes

# Export these alongside existing config objects:
SAMPLING = SamplingConfig()
EXAM = ExamConfig()
```
