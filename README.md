# Question Insight v2

Analyzes medical and university admission exam question papers against a structured syllabus tree — showing which topics appear most, by year and subject.

## How it works

```
Textbook PDF  →  01  →  Syllabus Tree
Question PDF  →  02  →  Question Bank
                 03  →  Mapped Questions  →  Web Viewer
```

**Script 01** reads a textbook PDF and asks Gemini to build a hierarchical syllabus tree (Subject → Paper → Chapter → Section → Topic).

**Script 02** extracts every MCQ, written, or mixed question from an exam PDF, page by page, with validation and checkpointing.

**Script 03** maps each question to the closest node(s) in the syllabus tree using a two-pass LLM approach.

**app.py** serves an interactive tree viewer with heatmap and year/subject trend charts.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:
```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-pro
```

## Usage

```bash
# 1. Generate syllabus tree from a textbook
python 01_generate_tree_from_book.py \
  --book books/biology_1st.pdf \
  --subject Biology \
  --paper "1st Paper" \
  --university "Medical/MBBS Admission"

# 2. Extract questions from an exam paper
python 02_extract_question_bank.py \
  --pdf question_pdfs/exam.pdf \
  --mode yearwise \
  --year 2023 \
  --exam-name "Medical Admission" \
  --question-type mcq        # mcq | written | mixed

# 3. Map questions to the syllabus tree
python 03_map_questions_to_tree.py --allow-new-nodes

# 4. Run the viewer
uvicorn app:app --reload
# open http://localhost:8000
```

Password-protected PDFs are supported via `--password`.

## Supported universities / exam types

| Exam | Mode | Question type |
|------|------|---------------|
| Medical / MBBS | yearwise | mcq |
| BUET | yearwise | mcq or mixed |
| DU Unit-A | yearwise | written |
| Chapterwise banks | chapterwise | mcq |

## Stack

- **LLM** — Google Gemini 2.5 Pro (`google-genai`)
- **PDF processing** — PyMuPDF
- **API** — FastAPI + uvicorn
- **Frontend** — Vanilla JS + Chart.js
