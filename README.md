# Question Insight v2

Analyzes medical and university admission exam question papers against a structured syllabus tree — showing which topics appear most, by year and subject. Supports semantic search over topics using RAG embeddings.

## How it works

```
Textbook PDF  →  01  →  Syllabus Tree
Question PDF  →  02  →  Question Bank
                 03  →  Mapped Questions  →  Web Viewer
Syllabus Tree →  04  →  Node Embeddings  →  RAG Search
```

| Script | What it does |
|--------|-------------|
| `01_generate_tree_from_book.py` | Reads a textbook PDF and asks Gemini to build a hierarchical syllabus tree (Subject → Paper → Chapter → Section → Topic) |
| `02_extract_question_bank.py` | Extracts every question from an exam PDF page by page, with per-page validation and checkpointing |
| `03_map_questions_to_tree.py` | Maps each question to the closest syllabus node(s) using a two-pass LLM approach, with checkpoint/resume |
| `04_create_embeddings.py` | Generates Gemini embeddings for every node summary; incremental — only re-embeds changed nodes |
| `app.py` | FastAPI web viewer with tree, heatmap, and year/subject trend charts |
| `rag.py` | Importable module for semantic node search and question retrieval |
| `example_extract_questions.py` | Example RAG queries: find exam questions by topic description |

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

### Step 1 — Generate syllabus tree from a textbook

Run once per textbook. Merges into `data/syllabus_tree.json`.

```bash
python 01_generate_tree_from_book.py \
  --book books/biology_1st.pdf \
  --subject Biology \
  --paper "1st Paper" \
  --university "Medical/MBBS Admission"
```

Optional: `--password` for encrypted PDFs, `--language`, `--class-level`, `--out`.

### Step 2 — Extract questions from an exam paper

```bash
# MCQ paper (default)
python 02_extract_question_bank.py \
  --pdf question_pdfs/exam.pdf \
  --mode yearwise \
  --year 2023 \
  --exam-name "Medical Admission" \
  --question-type mcq

# Written or mixed paper
python 02_extract_question_bank.py \
  --pdf question_pdfs/du_2023.pdf \
  --mode yearwise \
  --year 2023 \
  --exam-name "DU Unit-A" \
  --question-type written   # mcq | written | mixed
```

Extraction modes: `yearwise` (one exam per year), `chapterwise`, `random`.  
Progress is saved after every page — safe to interrupt and resume.  
Output: `data/question_bank.json`.

### Step 3 — Map questions to the syllabus tree

```bash
python 03_map_questions_to_tree.py

# With --allow-new-nodes to handle English/GK not in the tree
python 03_map_questions_to_tree.py --allow-new-nodes
```

Checkpointed after every question — safe to interrupt and resume.  
Output: `data/mapped_questions.json`.

### Step 4 — Build node embeddings (for RAG search)

```bash
python 04_create_embeddings.py
```

Uses `gemini-embedding-2` at 768 dimensions.  
Output: `data/node_embeddings.json`.

**Incremental updates** — the script hashes each node's content and skips nodes that haven't changed. So adding new books and re-running only embeds the new nodes:

```bash
# Add two new books
python 01_generate_tree_from_book.py --book books/math.pdf --subject Mathematics --paper "1st Paper" --university BUET
python 01_generate_tree_from_book.py --book books/physics_2nd.pdf --subject Physics --paper "2nd Paper" --university BUET

# Only the new nodes get embedded — existing ones are skipped
python 04_create_embeddings.py
```

Use `--force` to re-embed everything (e.g. after switching embedding models):

```bash
python 04_create_embeddings.py --force
```

### Run the web viewer

```bash
uvicorn app:app --reload
# open http://localhost:8000
```

### RAG search

```bash
# Run the bundled examples
python example_extract_questions.py
```

Or use `rag.py` directly in your own code:

```python
from rag import search_questions

result = search_questions(
    query="photosynthesis light reaction and dark reaction",
    top_k_nodes=5,
    min_score=0.5,
    include_subtree=True,
)

for node in result["matched_nodes"]:
    print(f"[{node['score']:.3f}] {node['path']}")

for q in result["questions"]:
    print(q["stem"])
```

## Supported universities / exam types

| Exam | Mode | Question type |
|------|------|---------------|
| Medical / MBBS | yearwise | mcq |
| BUET | yearwise | mcq or mixed |
| DU (written units) | yearwise | written |
| Chapterwise banks | chapterwise | mcq |
| Any university | random | mcq |

## Stack

- **LLM** — Google Gemini 2.5 Pro (`google-genai`)
- **Embeddings** — Gemini Embedding 2 (`gemini-embedding-2`, 768-dim)
- **PDF processing** — PyMuPDF
- **API** — FastAPI + uvicorn
- **Frontend** — Vanilla JS + Chart.js
- **Numerics** — NumPy (cosine similarity)

## Data files

| File | Description |
|------|-------------|
| `data/syllabus_tree.json` | Master syllabus tree (all subjects merged) |
| `data/question_bank.json` | Extracted questions |
| `data/mapped_questions.json` | Questions with `mapping.node_ids` filled |
| `data/node_embeddings.json` | Gemini embedding vectors per node |
| `data/extraction_checkpoints/` | Per-page extraction progress (resumable) |
